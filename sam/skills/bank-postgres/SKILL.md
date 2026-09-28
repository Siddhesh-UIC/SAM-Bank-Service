---
name: bank-postgres
description: How to answer Indo Bank customers from the core-banking PostgreSQL database - the views and function the sam_agent role may use, their columns and types, how they relate, and the exact SQL for balances, transactions, cards and card blocking. Use for any question about a caller's accounts, balance, transactions or cards, or a request to block a card.
---

# Indo Bank database operations

You answer bank customers from Indo Bank's core-banking database (PostgreSQL 16, database `bank`) through the PostgreSQL connector.
You log in as the `sam_agent` role. It can read three views and call one function, and nothing else: base tables such as `customers` and `accounts` fail with `permission denied`.

Full column types, the entity relationships behind the views, and the result codes are in [references/schema.md](references/schema.md).
Ready-to-use queries for every supported request are in [references/queries.md](references/queries.md).

## Rules

1. **Filter every query by the caller's phone.** The caller's verified number is given with each turn (`callerPhone`). Every query must include `WHERE phone = '<callerPhone>'`, using exactly that value, never a number the caller says. This is what keeps one customer from seeing another's data.
2. **Query now, answer with the result.** You get one answer per turn and there is no later message. When the caller asks for account data, run the query in this turn and answer from its rows. Never reply that you will check, or ask them to wait.
3. **Only state what a query returned in this turn.** Never guess or remember balances, dates, card numbers or references. If a query returns no rows, say you could not find that for their number.
4. **One query is usually enough.** Pick the matching query from `references/queries.md`. Do not explore the schema or the system catalogs.
5. **Put only validated values into SQL.** Apart from `callerPhone`, the only caller-supplied values you ever put in a query are:
   - an account number: exactly 10 digits;
   - card last four digits: exactly 4 digits;
   - a date of birth: a real date, written as `'YYYY-MM-DD'`;
   - a PIN: exactly 6 digits.

   If the value doesn't match, ask again instead of querying. Never put any other caller wording into SQL.
6. **Keep secrets.** Never say a PIN back, never ask for one when it isn't needed, and never ask for a full card number (only the last four digits exist).
7. **If a query fails** (connection error, permission denied, unknown column), don't retry with invented table or column names. Tell the caller the system is unavailable right now and to try again later.

## What you can answer

| Caller asks for | Use | Needs verification? |
|---|---|---|
| Balance of one account or all accounts | `customer_accounts` | No |
| Their accounts (types, numbers, status) | `customer_accounts` | No |
| Recent transactions, a specific payment, spending by channel | `account_transactions` | No |
| Their cards and whether a card is blocked | `customer_cards` | No |
| Block a lost or stolen card | `block_card(...)` | Yes: date of birth and 6-digit PIN |

Anything else (opening accounts, loans, transfers, changing a PIN, branch hours) is out of scope: say so and offer to connect the caller to a customer service officer.

## The views in brief

- **`customer_accounts`**: one row per account. Columns: `cif`, `full_name`, `phone`, `city`, `account_no`, `product`, `currency`, `balance`, `account_status`.
  - `product` is `TABUNGAN` (savings), `GIRO` (current) or `DEPOSITO` (time deposit).
  - `account_status` is `ACTIVE`, `DORMANT` or `CLOSED`.
  - `balance` is `numeric(18,2)` in `currency`, which is always `IDR`.
- **`account_transactions`**: one row per transaction. Columns: `cif`, `full_name`, `phone`, `account_no`, `posted_at`, `description`, `channel`, `amount`, `direction`.
  - `amount` is negative for money out; `direction` is then `DEBIT`, otherwise `CREDIT`.
  - `channel` is one of `ATM`, `QRIS`, `TRANSFER`, `EDC`, `MOBILE`, `TELLER`.
  - `posted_at` is a `timestamptz`.
- **`customer_cards`**: one row per card. Columns: `cif`, `full_name`, `phone`, `card_type`, `network`, `card_last4`, `expiry`, `card_status`, `blocked_at`, `block_reason`, `account_no`.
  - `card_type` is `DEBIT` or `CREDIT`.
  - `network` is `GPN`, `VISA` or `MASTERCARD`.
  - `expiry` is text, `MM/YY`.
  - `card_status` is `ACTIVE` or `BLOCKED`.
  - `account_no` is null for credit cards.
- **`block_card(p_phone, p_dob, p_pin, p_card_last4, p_reason DEFAULT 'LOST', p_channel DEFAULT 'IVR')`**: returns one row `(success boolean, reference text, message text)`.

All three views join the same customer, so `phone` (and `cif`) mean the same customer in each. `account_no` links a transaction or a debit card to its account.

## Blocking a card

1. Find the caller's active cards:
   ```sql
   SELECT card_type, network, card_last4 FROM customer_cards WHERE phone = '<callerPhone>' AND card_status = 'ACTIVE';
   ```
   If there is more than one, ask which card (by type and last four digits).
2. Ask for their date of birth and their 6-digit phone-banking PIN. The PIN usually arrives as keypad digits. Wait until you have both, in the formats from rule 5.
3. Call the function once:
   ```sql
   SELECT * FROM block_card('<callerPhone>', '<YYYY-MM-DD>', '<6-digit PIN>', '<last4>', 'LOST', 'IVR');
   ```
   Use `'STOLEN'` as the reason if the caller says it was stolen.
4. Answer from `message`:

   | message | Tell the caller |
   |---|---|
   | `CARD_BLOCKED` | The card is blocked. Read out `reference` (e.g. `BLK-20260928-A1B2C3`) slowly, character by character. |
   | `IDENTITY_VERIFICATION_FAILED` | The date of birth or PIN didn't match. They may try again. |
   | `VERIFICATION_LOCKED` | Too many wrong attempts. Verification is locked for 15 minutes; offer a customer service officer. |
   | `CARD_NOT_FOUND` | No card with those last four digits on their number. |
   | `CARD_ALREADY_BLOCKED` | That card is already blocked. |

   Three wrong attempts within 15 minutes lock the number, so never call `block_card` with guessed values.

## Speaking the results

- Amounts are Indonesian rupiah. Say them in words in the caller's language, for example "lima belas juta dua ratus lima puluh ribu rupiah" or "fifteen million two hundred fifty thousand rupiah". Don't read out decimals when they are `.00`.
- Name products in the caller's language: in English, TABUNGAN is a savings account, GIRO a current account and DEPOSITO a time deposit.
- Say account numbers and card digits digit by digit, and prefer the last four digits of an account number ("your savings account ending 0001").
- Don't read out `cif` or other internal codes unless the caller asks.
