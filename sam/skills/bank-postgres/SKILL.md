---
name: bank-postgres
description: Load this before any query to the Indo Bank database. It has the exact SQL for balances, the last 3 transactions, cards and card blocking on a verified call, and the real view and column names (customer_accounts, account_transactions, customer_cards; account numbers are account_no). Without it, column names get guessed and the query fails.
---

# Indo Bank database

Query only these three views and `block_card_verified`. The caller is already verified: never ask for a PIN, a date of birth or anything else.

## Queries (use as they are)

Replace `<callerPhone>` and `<sessionToken>` with the values from the turn.

- **Balance** (menu 1):
  `SELECT account_no, product, balance, currency, account_status FROM customer_accounts WHERE phone = '<callerPhone>' ORDER BY account_no;`
- **Last 3 transactions** (menu 2):
  `SELECT account_no, posted_at, description, amount, direction FROM account_transactions WHERE phone = '<callerPhone>' ORDER BY posted_at DESC LIMIT 3;`
- **Cards** (menu 3, before blocking):
  `SELECT card_type, network, card_last4, card_status FROM customer_cards WHERE phone = '<callerPhone>';`
- **Block a card:**
  `SELECT * FROM block_card_verified('<sessionToken>', '<last4>', 'LOST');`
  - Use `'STOLEN'` as the reason if the caller says it was stolen.
  - Results: `CARD_BLOCKED` (read `reference` out character by character), `CARD_NOT_FOUND`, `CARD_ALREADY_BLOCKED`, `SESSION_INVALID` (ask them to call again).

## Columns

- `customer_accounts`: `phone`, `account_no`, `product` (TABUNGAN = savings, GIRO = current, DEPOSITO = time deposit), `currency` (IDR), `balance`, `account_status`.
- `account_transactions`: `phone`, `account_no`, `posted_at`, `description`, `channel`, `amount` (negative = money out), `direction` (DEBIT or CREDIT).
- `customer_cards`: `phone`, `card_type` (DEBIT or CREDIT), `network`, `card_last4`, `expiry`, `card_status` (ACTIVE or BLOCKED), `account_no`.

## Rules

1. **Filter by the caller.** Every query has `WHERE phone = '<callerPhone>'`, using exactly that value. Never use a number the caller says.
2. **Make a real tool call.** Query with a real tool call, never as text. Answer only from the rows returned in this turn. Never guess or remember numbers.
3. **Only the last 4 digits go into SQL.** The only caller value you ever put in a query is a card's last 4 digits, and only if it's exactly 4 digits. If several cards are active, ask which one; if only one, name it and confirm.
4. **Wrong column:** fix it from the lists above and run the query once more. If it still fails, say the system is unavailable and to try again later.
5. **Speak the results:**
   - amounts in words in the caller's language, without `.00`;
   - accounts by their last 4 digits ("savings account ending 0001");
   - digits one by one;
   - never read out the `sessionToken` or internal codes.
