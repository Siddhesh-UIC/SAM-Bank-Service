"""Mock bank backend API for the SAM IVR demo.

Whitelisted tool-style endpoints only (balance, transactions, cards, card block),
plus a read-only admin view of the tables for the validation UI.
"""

import os
from datetime import date
from pathlib import Path

import psycopg
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://bank:bank123@localhost:5432/bank")
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


# ── Validation UI (not for agents) ──────────────────────────────────────────

ADMIN_TABLES = {
    "customers": "SELECT id, cif, full_name, phone, email, date_of_birth, city, created_at FROM customers ORDER BY id",
    "accounts": "SELECT * FROM accounts ORDER BY id",
    "transactions": "SELECT t.id, a.account_no, t.posted_at, t.description, t.channel, t.amount "
                    "FROM transactions t JOIN accounts a ON a.id = t.account_id ORDER BY t.posted_at DESC",
    "cards": "SELECT * FROM cards ORDER BY id",
    "card_block_requests": "SELECT * FROM card_block_requests ORDER BY created_at DESC",
}


@app.get("/admin/tables/{name}", include_in_schema=False)
def admin_table(name: str):
    if name not in ADMIN_TABLES:
        raise HTTPException(404, "UNKNOWN_TABLE")
    return query(ADMIN_TABLES[name])


@app.get("/", include_in_schema=False)
def ui():
    return FileResponse(STATIC / "index.html")
