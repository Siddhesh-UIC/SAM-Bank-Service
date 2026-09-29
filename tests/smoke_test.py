"""End-to-end smoke test against the running stack: `docker compose up -d` then `python tests/smoke_test.py`."""

import json
import os
import subprocess
import time
import urllib.error
import urllib.request

BASE = os.environ.get("BANK_API_URL", "http://127.0.0.1:8000")
PHONE = "%2B6281234567808"  # Rina Kartika Sari, URL-encoded +


def call(path, body=None, method=None):
    req = urllib.request.Request(BASE + path, method=method or ("POST" if body else "GET"),
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

status, txns = call(f"/customers/by-phone/{PHONE}/accounts/1230000011/transactions?limit=3")
assert status == 200 and len(txns) == 3
assert txns[0]["posted_at"] >= txns[1]["posted_at"] >= txns[2]["posted_at"]
# Another customer's account is refused, exactly like one that doesn't exist, and the old unscoped route is gone.
other = call("/customers/by-phone/%2B6281234567802/accounts")[1][0]["account_no"]
assert other != "1230000011"
assert call(f"/customers/by-phone/{PHONE}/accounts/{other}/transactions")[0] == 404, "read another customer's account"
assert call(f"/customers/by-phone/{PHONE}/accounts/0000000000/transactions")[0] == 404
assert call("/accounts/1230000011/transactions")[0] == 404, "unscoped transactions route still exists"

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

# Admin add/delete: a posted transaction moves the balance, deleting it moves it back.
def balance():
    return call(f"/customers/by-phone/{PHONE}/accounts")[1][0]["balance"]

before = balance()
status, row = call("/admin/tables/transactions", {"account_no": "1230000011", "description": "Test",
                                                  "channel": "ATM", "amount": "-100000"})
assert status == 200, row
assert balance() == before - 100000
assert call(f"/admin/tables/transactions/{row['id']}", method="DELETE")[0] == 200
assert balance() == before
assert call("/admin/tables/transactions", {"account_no": "0000000000", "description": "x",
                                           "channel": "ATM", "amount": "1"})[0] == 400

status, cust = call("/admin/tables/customers", {"cif": "CIFTEST", "full_name": "Uji Coba", "phone": "+6280000000000",
                                                "date_of_birth": "2000-01-01", "city": "Bogor", "pin": "111111"})
assert status == 200, cust
assert call("/admin/tables/customers", {"cif": "CIFTEST", "full_name": "Dup", "phone": "+6280000000001",
                                        "date_of_birth": "2000-01-01", "city": "Bogor", "pin": "111111"})[0] == 400
assert call("/admin/tables/accounts", {"account_no": "9990000001", "customer_id": str(cust["id"]), "product": "TABUNGAN",
                                       "balance": "500000", "status": "ACTIVE", "opened_at": "2026-01-01"})[0] == 200
assert call(f"/admin/tables/customers/{cust['id']}", method="DELETE")[0] == 200  # cascades to the account
assert call("/customers/by-phone/%2B6280000000000")[0] == 404
assert call(f"/admin/tables/customers/{cust['id']}", method="DELETE")[0] == 404

# Wrong-PIN lockout: 3 failures lock the phone, even the right PIN is refused, deleting the attempts unlocks.
DEWI = {"phone": "+6281234567804", "date_of_birth": "1995-01-30", "card_last4": "9999"}  # 9999: nothing gets blocked

def clear_attempts(phone):
    for a in call("/admin/tables/verification_attempts")[1]:
        if a["phone"] == phone:
            call(f"/admin/tables/verification_attempts/{a['id']}", method="DELETE")

clear_attempts(DEWI["phone"])
for _ in range(3):
    assert call("/cards/block", {**DEWI, "pin": "000000"})[0] == 401
assert call("/cards/block", {**DEWI, "pin": "456789"})[0] == 423, "locked after 3 wrong PINs"
clear_attempts(DEWI["phone"])
assert call("/cards/block", {**DEWI, "pin": "456789"})[0] == 404, "unlocked: verification passes, card 9999 not found"
clear_attempts(DEWI["phone"])

# Call log: API calls appear with the PIN masked.
logs = call("/admin/logs")[1]
blocks = [r for r in logs if r["source"] == "API" and r["request"].startswith("POST /cards/block")]
assert blocks and all("456789" not in r["request"] and "******" in r["request"] for r in blocks), blocks[:2]

# Call log: SQL run as sam_agent (the SAM DB connector's login) appears, PIN masked, errors included.
subprocess.run(["docker", "exec", "sam-bank-db", "psql", "-U", "sam_agent", "-d", "bank", "-c",
                "SELECT * FROM block_card('+6281234567804', '1995-01-30', '000001', '9999')",
                "-c", "SELECT * FROM customers"], capture_output=True, check=False)
clear_attempts(DEWI["phone"])
for _ in range(20):  # the log collector flushes asynchronously
    db = [r for r in call("/admin/logs")[1] if r["source"] == "DB"]
    if any("customers" in r["request"] and r["result"].startswith("ERROR") for r in db):
        break
    time.sleep(0.25)
sql = [r for r in db if "block_card('+6281234567804'" in r["request"]]
assert sql and "000001" not in sql[0]["request"] and "'******'" in sql[0]["request"], db[:3]
assert any(r["request"] == "SELECT * FROM customers" and "permission denied" in r["result"] for r in db), db[:3]

# IVR sessions: the PIN is checked once and opens a session; blocking through it needs only the card's last 4 digits
# and only ever touches that customer's cards; after hang-up the token is dead.
AGUS = "+6281234567803"
clear_attempts(AGUS)
assert call("/ivr/sessions", {"phone": "+6280000000009", "pin": "123456"})[0] == 404, "unregistered number"
assert call("/ivr/sessions", {"phone": AGUS, "pin": "12"})[0] == 422
assert call("/ivr/sessions", {"phone": AGUS, "pin": "000000"})[0] == 401
status, session = call("/ivr/sessions", {"phone": AGUS, "pin": "345678"})
assert status == 200 and session["full_name"] == "Agus Wijaya" and len(session["token"]) == 64, session
token = session["token"]

def block_verified(last4):
    out = subprocess.run(["docker", "exec", "sam-bank-db", "psql", "-U", "sam_agent", "-d", "bank", "-Atc",
                          f"SELECT message FROM block_card_verified('{token}', '{last4}')"],
                         capture_output=True, text=True)
    return out.stdout.strip() or out.stderr.strip()

_, agus_cards = call(f"/customers/by-phone/{AGUS.replace('+', '%2B')}/cards")
active = [c["card_last4"] for c in agus_cards if c["card_status"] == "ACTIVE"]
assert block_verified("4821") == "CARD_NOT_FOUND", "Budi's card: not this session's customer"
for last4 in active:  # a fresh database: both of Agus's cards; a re-run: none left
    assert block_verified(last4) == "CARD_BLOCKED"
assert block_verified("7713") == "CARD_ALREADY_BLOCKED"
assert call("/ivr/sessions/end", {"token": token}) == (200, {"ended": True})
assert call("/ivr/sessions/end", {"token": token}) == (200, {"ended": False})
assert block_verified("0000") == "SESSION_INVALID", "hung up: the token no longer works"
clear_attempts(AGUS)
logs = [r for r in call("/admin/logs")[1] if r["request"].startswith("POST /ivr/")]
assert logs and all('"pin": "345678"' not in r["request"] and token not in r["request"] for r in logs), logs[:2]
assert any('"pin": "******"' in r["request"] for r in logs), logs[:3]
assert call("/admin/tables/ivr_sessions")[0] == 200

print("smoke test passed")
