#!/usr/bin/env python3
"""
bank_tools.py — SAM toolset that exposes the SAM Bank Service REST API as agent tools.

SAM STR protocol (same as the workshop travel_planner.py):
  --schema              → print JSON schema for all tools, then exit
  <runner_args.json>    → read args, execute tool, write result to result_file path

Base URL defaults to http://localhost:8000; override with the BANK_API_URL env var.
"""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE_URL = os.environ.get("BANK_API_URL", "http://localhost:8000").rstrip("/")

PHONE = {"type": "string", "description": "Caller phone number in E.164 format, e.g. +6281234567801"}


def tool(description, properties, required):
    return {"description": description,
            "parameters": {"type": "object", "properties": properties, "required": required},
            "artifact_params": {}, "instructions": ""}


SCHEMA = {"tools": {
    "get_customer": tool("Look up the customer (CIF, name, city) for a caller phone number.",
                         {"phone": PHONE}, ["phone"]),
    "get_balances": tool("List the customer's accounts with current balances in IDR.",
                         {"phone": PHONE}, ["phone"]),
    "get_recent_transactions": tool(
        "Most recent transactions for one account, newest first. Negative amount = debit.",
        {"account_no": {"type": "string", "description": "10-digit account number"},
         "limit": {"type": "integer", "description": "Number of transactions (1-50, default 5)"}},
        ["account_no"]),
    "get_cards": tool("List the customer's debit/credit cards with last 4 digits and status.",
                      {"phone": PHONE}, ["phone"]),
    "block_card": tool(
        "Block a card. Requires identity verification: the caller's date of birth and 6-digit PIN.",
        {"phone": PHONE,
         "date_of_birth": {"type": "string", "description": "YYYY-MM-DD"},
         "pin": {"type": "string", "description": "6-digit phone-banking PIN"},
         "card_last4": {"type": "string", "description": "Last 4 digits of the card"},
         "reason": {"type": "string", "description": "LOST, STOLEN or SUSPECTED_FRAUD (default LOST)"}},
        ["phone", "date_of_birth", "pin", "card_last4"]),
}}


def http(method, path, body=None):
    req = urllib.request.Request(BASE_URL + path, method=method,
                                 data=json.dumps(body).encode() if body else None,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return {"result": {"status": "success", "data": json.load(r)}, "error": ""}
    except urllib.error.HTTPError as e:
        # 4xx carries a business outcome (e.g. IDENTITY_VERIFICATION_FAILED) the agent must relay
        return {"result": None, "error": f"HTTP {e.code}: {json.load(e).get('detail')}"}
    except urllib.error.URLError as e:
        return {"result": None, "error": f"Bank backend unreachable at {BASE_URL}: {e.reason}"}


def q(value):
    return urllib.parse.quote(str(value), safe="")


TOOLS = {
    "get_customer": lambda a: http("GET", f"/customers/by-phone/{q(a['phone'])}"),
    "get_balances": lambda a: http("GET", f"/customers/by-phone/{q(a['phone'])}/accounts"),
    "get_recent_transactions": lambda a: http(
        "GET", f"/accounts/{q(a['account_no'])}/transactions?limit={int(a.get('limit') or 5)}"),
    "get_cards": lambda a: http("GET", f"/customers/by-phone/{q(a['phone'])}/cards"),
    "block_card": lambda a: http("POST", "/cards/block", {
        k: a[k] for k in ("phone", "date_of_birth", "pin", "card_last4", "reason") if a.get(k)}),
}


def main():
    args = sys.argv[1:]
    if args and args[0] == "--schema":
        print(json.dumps(SCHEMA))
        return
    if not args:
        sys.exit("usage: bank_tools.py --schema | <runner_args.json>")

    with open(args[0]) as f:
        runner_args = json.load(f)
    name = runner_args.get("tool_name", "")
    handler = TOOLS.get(name)
    try:
        result = handler(runner_args.get("args", {})) if handler else {"result": None, "error": f"tool '{name}' not found"}
    except Exception as e:
        result = {"result": None, "error": f"tool '{name}' raised an exception: {e}"}

    if runner_args.get("result_file"):
        with open(runner_args["result_file"], "w") as f:
            json.dump(result, f, default=str)
    else:
        print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
