"""End-to-end smoke test against the running stack: `docker compose up -d` then `python tests/smoke_test.py`."""

import json
import os
import urllib.error
import urllib.request

BASE = os.environ.get("BANK_API_URL", "http://localhost:8000")
PHONE = "%2B6281234567808"  # Rina Kartika Sari, URL-encoded +


def call(path, body=None):
    req = urllib.request.Request(BASE + path, method="POST" if body else "GET",
                                 data=json.dumps(body).encode() if body else None,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


assert call("/health")[0] == 200
assert call("/customers/by-phone/%2B620000")[0] == 404

status, accounts = call(f"/customers/by-phone/{PHONE}/accounts")
assert status == 200 and accounts[0]["account_no"] == "1230000011", accounts

status, txns = call("/accounts/1230000011/transactions?limit=3")
assert status == 200 and len(txns) == 3
assert txns[0]["posted_at"] >= txns[1]["posted_at"] >= txns[2]["posted_at"]

block = {"phone": "+6281234567808", "date_of_birth": "1993-04-04", "pin": "000000", "card_last4": "4408"}
assert call("/cards/block", block)[0] == 401, "wrong PIN must be refused"
assert call("/cards/block", {**block, "pin": "12"})[0] == 422, "malformed PIN must be rejected"
assert call("/cards/block", {**block, "pin": "890123", "card_last4": "9999"})[0] == 404

status, res = call("/cards/block", {**block, "pin": "890123"})
assert status in (200, 409), res  # 409 when re-run without resetting the DB
assert call("/cards/block", {**block, "pin": "890123"})[0] == 409
_, cards = call(f"/customers/by-phone/{PHONE}/cards")
assert cards[0]["card_status"] == "BLOCKED"

assert call("/admin/tables/customers")[0] == 200
assert call("/admin/tables/pg_shadow")[0] == 404

print("smoke test passed")
