"""Mock bank backend API for the SAM IVR demo.

Whitelisted tool-style endpoints only (balance, transactions, cards, card block),
plus a read-only admin view of the tables for the validation UI.
"""

import os
import re
from datetime import date
from pathlib import Path

import psycopg
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://bank:bank123@127.0.0.1:5432/bank")
STATIC = Path(__file__).parent / "static"

app = FastAPI(
    title="SAM Bank Service",
    version="1.0.0",
    description="Mock core-banking API for the Bahasa Indonesia IVR PoC. "
    "Amounts are in IDR. Customers are identified by caller phone number (E.164, e.g. +6281234567801).",
)


def query(sql: str, params: tuple = ()) -> list[dict]:
    # ponytail: one connection per request; add psycopg_pool if load testing through the API
    with psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=True) as conn:
        return conn.execute(sql, params).fetchall()


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


@app.get("/accounts/{account_no}/transactions", tags=["transactions"], operation_id="get_recent_transactions",
         summary="Most recent transactions for an account, newest first (negative amount = debit)")
def get_transactions(account_no: str, limit: int = Query(5, ge=1, le=50)):
    if not query("SELECT 1 FROM accounts WHERE account_no = %s", (account_no,)):
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
        status = {"IDENTITY_VERIFICATION_FAILED": 401, "CARD_ALREADY_BLOCKED": 409}.get(row["message"], 404)
        raise HTTPException(status, row["message"])
    return row


# ── Validation UI (not for agents; hidden from the OpenAPI spec) ────────────

ADMIN_TABLES = {
    "customers": "SELECT id, cif, full_name, phone, email, date_of_birth, city, created_at FROM customers ORDER BY id",
    "accounts": "SELECT * FROM accounts ORDER BY id",
    "transactions": "SELECT t.id, a.account_no, t.posted_at, t.description, t.channel, t.amount "
                    "FROM transactions t JOIN accounts a ON a.id = t.account_id ORDER BY t.posted_at DESC",
    "cards": "SELECT * FROM cards ORDER BY id",
    "card_block_requests": "SELECT * FROM card_block_requests ORDER BY created_at DESC",
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


@app.get("/", include_in_schema=False)
def ui():
    return FileResponse(STATIC / "index.html")
