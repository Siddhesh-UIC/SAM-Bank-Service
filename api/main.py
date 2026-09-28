"""Mock bank backend API for the SAM IVR demo.

Whitelisted tool-style endpoints only (balance, transactions, cards, card block),
plus admin routes for the validation UI: browse/add/delete rows and a call log of
everything the agent did, over REST and over the direct DB connection.
"""

import json
import os
import re
import time
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import unquote

import psycopg
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://bank:bank123@127.0.0.1:5432/bank")
PG_LOG_FILE = os.environ.get("PG_LOG_FILE")  # Postgres JSON statement log; unset → DB rows missing from call log
STATIC = Path(__file__).parent / "static"
AGENT_PATHS = ("/customers/", "/accounts/", "/cards/", "/ivr/")

app = FastAPI(
    title="SAM Bank Service",
    version="1.0.0",
    description="Mock core-banking API for the Bahasa Indonesia IVR PoC. "
    "Amounts are in IDR. Customers are identified by caller phone number (E.164, e.g. +6281234567801).",
)


def query(sql: str, params: tuple = ()) -> list[dict]:  # runs as the owner "bank", unlike SAM's sam_agent
    # ponytail: one connection per request; add psycopg_pool if load testing through the API
    with psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=True) as conn:
        cur = conn.execute(sql, params)
        return cur.fetchall() if cur.description else []


def mask_pins(text: str) -> str:
    """Hide PINs and session tokens: "pin"/"token" in JSON bodies, quoted 6-digit and 64-hex literals in SQL."""
    text = re.sub(r'("(?:pin|token)"\s*:\s*")[^"]*"', r'\1******"', text)
    text = re.sub(r"'[0-9a-f]{64}'", "'<session token>'", text)
    return re.sub(r"'\d{6}'", "'******'", text)


@app.middleware("http")
async def log_agent_calls(request: Request, call_next):  # the UI's call log: every agent/IVR request, secrets masked
    if not request.url.path.startswith(AGENT_PATHS):
        return await call_next(request)
    body = (await request.body()).decode(errors="replace")
    start = time.perf_counter()
    response = await call_next(request)
    ms = (time.perf_counter() - start) * 1000
    await run_in_threadpool(
        query, "INSERT INTO api_request_log (method, path, detail, status, duration_ms) VALUES (%s, %s, %s, %s, %s)",
        (request.method, unquote(request.url.path), mask_pins(request.url.query or body) or None, response.status_code, ms))
    return response


# ── Agent endpoints (the OpenAPI spec). Used by the Option B toolset; the voice pipe uses only get_customer ──

def customer_or_404(phone: str) -> dict:
    rows = query("SELECT cif, full_name, phone, city FROM customers WHERE phone = %s", (phone,))
    if not rows:
        raise HTTPException(404, "CUSTOMER_NOT_FOUND")
    return rows[0]


@app.get("/health", tags=["system"])
def health():
    query("SELECT 1")
    return {"status": "healthy", "service": "sam-bank-service"}


@app.get("/customers/by-phone/{phone}", tags=["customer"], operation_id="get_customer_by_phone",
         summary="Look up a customer by caller phone number")
def get_customer(phone: str):
    return customer_or_404(phone)


@app.get("/customers/by-phone/{phone}/accounts", tags=["balance"], operation_id="get_balances",
         summary="List a customer's accounts with current balances (IDR)")
def get_balances(phone: str):
    customer_or_404(phone)
    return query(
        "SELECT account_no, product, currency, balance, account_status FROM customer_accounts "
        "WHERE phone = %s ORDER BY account_no", (phone,))


@app.get("/customers/by-phone/{phone}/accounts/{account_no}/transactions", tags=["transactions"],
         operation_id="get_recent_transactions",
         summary="Most recent transactions for one of the caller's accounts, newest first (negative amount = debit)")
def get_transactions(phone: str, account_no: str, limit: int = Query(5, ge=1, le=50)):
    # Scoped to the caller: an account that exists but belongs to someone else gives the same 404 as one that
    # doesn't exist, so a caller can't read another customer's transactions or probe which account numbers exist.
    if not query("SELECT 1 FROM customer_accounts WHERE phone = %s AND account_no = %s", (phone, account_no)):
        raise HTTPException(404, "ACCOUNT_NOT_FOUND")
    return query(
        "SELECT posted_at, description, channel, amount, direction FROM account_transactions "
        "WHERE account_no = %s ORDER BY posted_at DESC LIMIT %s", (account_no, limit))


@app.get("/customers/by-phone/{phone}/cards", tags=["cards"], operation_id="get_cards",
         summary="List a customer's debit and credit cards (last 4 digits and status)")
def get_cards(phone: str):
    customer_or_404(phone)
    return query(
        "SELECT card_type, network, card_last4, expiry, card_status, blocked_at, block_reason, account_no "
        "FROM customer_cards WHERE phone = %s ORDER BY card_type, card_last4", (phone,))


class BlockCardRequest(BaseModel):
    phone: str = Field(description="Caller phone number, E.164", examples=["+6281234567801"])
    date_of_birth: date = Field(description="Customer date of birth for identity verification")
    pin: str = Field(pattern=r"^\d{6}$", description="6-digit phone-banking PIN")
    card_last4: str = Field(pattern=r"^\d{4}$", description="Last 4 digits of the card to block")
    reason: str = Field("LOST", description="LOST, STOLEN or SUSPECTED_FRAUD")


@app.post("/cards/block", tags=["cards"], operation_id="block_card",
          summary="Block a card after verifying the caller's identity (date of birth + PIN)")
def block_card(req: BlockCardRequest):
    row = query("SELECT * FROM block_card(%s, %s, %s, %s, %s, 'IVR')",
                (req.phone, req.date_of_birth, req.pin, req.card_last4, req.reason))[0]
    if not row["success"]:
        status = {"IDENTITY_VERIFICATION_FAILED": 401, "CARD_ALREADY_BLOCKED": 409,
                  "VERIFICATION_LOCKED": 423}.get(row["message"], 404)
        raise HTTPException(status, row["message"])
    return row


# ── IVR sessions (for the voice pipe, not agents; hidden from the OpenAPI spec) ────
# The voice pipe checks the caller's PIN once at the start of a call and gets a token; the agent then acts for that
# customer through the token (block_card_verified) and never asks for the PIN.

class OpenSessionRequest(BaseModel):
    phone: str = Field(description="Caller phone number, E.164")
    pin: str = Field(pattern=r"^\d{6}$", description="6-digit phone-banking PIN")
    channel: str = Field("IVR", max_length=20)


class EndSessionRequest(BaseModel):
    token: str = Field(pattern=r"^[0-9a-f]{64}$")


@app.post("/ivr/sessions", include_in_schema=False)
def open_session(req: OpenSessionRequest):
    """200 {token, full_name}; 404 CUSTOMER_NOT_FOUND, 401 IDENTITY_VERIFICATION_FAILED, 423 VERIFICATION_LOCKED."""
    row = query("SELECT * FROM open_ivr_session(%s, %s, %s)", (req.phone, req.pin, req.channel))[0]
    if not row["success"]:
        raise HTTPException({"CUSTOMER_NOT_FOUND": 404, "VERIFICATION_LOCKED": 423}.get(row["message"], 401),
                            row["message"])
    return {"token": row["token"], "full_name": row["full_name"]}


@app.post("/ivr/sessions/end", include_in_schema=False)
def end_session(req: EndSessionRequest):
    return {"ended": bool(query("SELECT end_ivr_session(%s) AS ended", (req.token,))[0]["ended"])}


# ── Validation UI (not for agents; hidden from the OpenAPI spec) ────────────

ADMIN_TABLES = {
    "customers": "SELECT id, cif, full_name, phone, email, date_of_birth, city, created_at FROM customers ORDER BY id",
    "accounts": "SELECT * FROM accounts ORDER BY id",
    "transactions": "SELECT t.id, a.account_no, t.posted_at, t.description, t.channel, t.amount "
                    "FROM transactions t JOIN accounts a ON a.id = t.account_id ORDER BY t.posted_at DESC",
    "cards": "SELECT * FROM cards ORDER BY id",
    "card_block_requests": "SELECT * FROM card_block_requests ORDER BY created_at DESC",
    "verification_attempts": "SELECT * FROM verification_attempts ORDER BY created_at DESC",
    "ivr_sessions": "SELECT left(s.token, 8) || '…' AS token, c.phone, c.full_name, s.channel, s.created_at, "
                    "s.expires_at, s.ended_at, CASE WHEN s.ended_at IS NULL AND s.expires_at > now() "
                    "THEN 'ACTIVE' ELSE 'CLOSED' END AS status "
                    "FROM ivr_sessions s JOIN customers c ON c.id = s.customer_id ORDER BY s.created_at DESC",
}


# Add-record forms for testing: field name → input type (list = dropdown options).
ADMIN_FORMS = {
    "customers": {"cif": "text", "full_name": "text", "phone": "text", "email": "text",
                  "date_of_birth": "date", "city": "text", "pin": "text"},
    "accounts": {"account_no": "text", "customer_id": "number", "product": ["TABUNGAN", "GIRO", "DEPOSITO"],
                 "balance": "number", "status": ["ACTIVE", "DORMANT", "CLOSED"], "opened_at": "date"},
    "transactions": {"account_no": "text", "description": "text",
                     "channel": ["ATM", "QRIS", "TRANSFER", "EDC", "MOBILE", "TELLER"], "amount": "number"},
    "cards": {"customer_id": "number", "account_id": "number", "card_type": ["DEBIT", "CREDIT"],
              "network": ["GPN", "VISA", "MASTERCARD"], "card_last4": "text", "expiry": "text",
              "status": ["ACTIVE", "BLOCKED"]},
}

ADMIN_INSERTS = {
    "customers": "INSERT INTO customers (cif, full_name, phone, email, date_of_birth, city, pin_hash) "
                 "VALUES (%(cif)s, %(full_name)s, %(phone)s, %(email)s, %(date_of_birth)s, %(city)s, "
                 "crypt(%(pin)s, gen_salt('bf'))) RETURNING id",
    "accounts": "INSERT INTO accounts (account_no, customer_id, product, balance, status, opened_at) "
                "VALUES (%(account_no)s, %(customer_id)s, %(product)s, %(balance)s, %(status)s, %(opened_at)s) RETURNING id",
    # Posting a transaction moves the balance too, so balances stay consistent.
    "transactions": "WITH a AS (UPDATE accounts SET balance = balance + %(amount)s::numeric "
                    "WHERE account_no = %(account_no)s RETURNING id) "
                    "INSERT INTO transactions (account_id, posted_at, description, channel, amount) "
                    "SELECT id, now(), %(description)s, %(channel)s, %(amount)s::numeric FROM a RETURNING id",
    "cards": "INSERT INTO cards (customer_id, account_id, card_type, network, card_last4, expiry, status) "
             "VALUES (%(customer_id)s, %(account_id)s, %(card_type)s, %(network)s, %(card_last4)s, %(expiry)s, "
             "%(status)s) RETURNING id",
}

ADMIN_DELETES = {
    "customers": "DELETE FROM customers WHERE id = %s RETURNING id",  # cascades to accounts, cards, transactions
    "accounts": "DELETE FROM accounts WHERE id = %s RETURNING id",
    "transactions": "WITH t AS (DELETE FROM transactions WHERE id = %s RETURNING account_id, amount) "
                    "UPDATE accounts a SET balance = a.balance - t.amount FROM t WHERE a.id = t.account_id RETURNING a.id",
    "cards": "DELETE FROM cards WHERE id = %s RETURNING id",
    "card_block_requests": "DELETE FROM card_block_requests WHERE id = %s RETURNING id",
    "verification_attempts": "DELETE FROM verification_attempts WHERE id = %s RETURNING id",  # delete FAILED rows to unlock
}


def admin_write(sql: str, params) -> list[dict]:
    try:
        return query(sql, params)
    except psycopg.Error as e:  # constraint violations etc. → readable message in the UI
        raise HTTPException(400, e.diag.message_primary or str(e))


@app.get("/admin/tables/{name}", include_in_schema=False)
def admin_table(name: str):
    if name not in ADMIN_TABLES:
        raise HTTPException(404, "UNKNOWN_TABLE")
    return query(ADMIN_TABLES[name])


@app.get("/admin/forms", include_in_schema=False)
def admin_forms():
    return {"forms": ADMIN_FORMS, "deletable": list(ADMIN_DELETES)}


@app.post("/admin/tables/{name}", include_in_schema=False)
def admin_add(name: str, body: dict[str, str | int | float | None]):
    if name not in ADMIN_INSERTS:
        raise HTTPException(404, "UNKNOWN_TABLE")
    params = {f: (body.get(f) if body.get(f) != "" else None) for f in ADMIN_FORMS[name]}
    if name == "customers" and not re.fullmatch(r"\d{6}", str(params["pin"] or "")):
        raise HTTPException(400, "PIN must be 6 digits")
    rows = admin_write(ADMIN_INSERTS[name], params)
    if not rows:
        raise HTTPException(400, "account_no not found")  # only the transactions insert can match nothing
    return rows[0]


@app.delete("/admin/tables/{name}/{row_id}", include_in_schema=False)
def admin_delete(name: str, row_id: int):
    if name not in ADMIN_DELETES:
        raise HTTPException(404, "UNKNOWN_TABLE")
    if not admin_write(ADMIN_DELETES[name], (row_id,)):
        raise HTTPException(404, "ROW_NOT_FOUND")
    return {"deleted": row_id}


# ── Call log: agent REST calls (api_request_log) + agent SQL (Postgres JSON log) ──

PG_DURATION = re.compile(r"duration: ([\d.]+) ms\s+(?:statement|execute [^:]*): (.*)", re.S)


def db_calls() -> list[dict]:
    if not PG_LOG_FILE or not os.path.exists(PG_LOG_FILE):
        return []
    with open(PG_LOG_FILE, "rb") as f:
        # ponytail: tails the last 2 MB of the log, plenty for a demo session; ship to a log store for more
        f.seek(max(0, os.path.getsize(PG_LOG_FILE) - 2_000_000))
        lines = f.read().decode(errors="replace").splitlines()
    rows = []
    for line in lines:
        try:
            e = json.loads(line)
        except ValueError:
            continue  # first line may be cut in half by the seek
        if e.get("user") != "sam_agent":
            continue
        at = datetime.strptime(e["timestamp"], "%Y-%m-%d %H:%M:%S.%f %Z").replace(tzinfo=timezone.utc)
        if m := PG_DURATION.match(e.get("message", "")):
            rows.append({"logged_at": at, "source": "DB", "request": mask_pins(m[2]), "result": "OK", "ms": float(m[1])})
        elif e.get("error_severity") in ("ERROR", "FATAL"):
            rows.append({"logged_at": at, "source": "DB", "request": mask_pins(e.get("statement") or "(connect)"),
                         "result": "ERROR: " + e.get("message", ""), "ms": None})
    return rows


@app.get("/admin/logs", include_in_schema=False)
def admin_logs(limit: int = Query(200, ge=1, le=2000)):
    api_rows = [{"logged_at": r["created_at"], "source": "API",
                 "request": f"{r['method']} {r['path']}" + (f"  {r['detail']}" if r["detail"] else ""),
                 "result": str(r["status"]), "ms": float(r["duration_ms"])}
                for r in query("SELECT * FROM api_request_log ORDER BY id DESC LIMIT %s", (limit,))]
    return sorted(api_rows + db_calls(), key=lambda r: r["logged_at"], reverse=True)[:limit]


@app.get("/", include_in_schema=False)
def ui():
    return FileResponse(STATIC / "index.html")
