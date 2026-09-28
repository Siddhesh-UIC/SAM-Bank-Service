# Indo Bank database schema

PostgreSQL 16, database `bank`, schema `public`. The `sam_agent` role can use only the three views and `block_card()`. The base tables are listed so you understand what the views mean; querying them fails with `permission denied`.

## Entity relationships

```
customers 1 ──< accounts 1 ──< transactions
    │               │
    │               └──────< cards (account_id; null for credit cards)
    └─────────────────────< cards (customer_id)
cards 1 ──< card_block_requests
customers.phone ··· verification_attempts.phone   (by value, no foreign key)
```

- A **customer** has one or more **accounts** and zero or more **cards**. `phone` is unique per customer: it is the caller ID.
- An **account** has many **transactions**.
- A **card** belongs to a customer. A debit card is also linked to one account; a credit card has no account.
- Each successful card block writes a **card_block_requests** row with a unique `reference`.
- Every identity check by `block_card()` writes a **verification_attempts** row, which drives the wrong-PIN lockout.

## Views (what sam_agent can read)

### customer_accounts: one row per account

| Column | Type | Notes |
|---|---|---|
| `cif` | text | Customer Information File number, e.g. `CIF0001`. Internal; don't read it out |
| `full_name` | text | Customer's name |
| `phone` | text | E.164 caller ID, e.g. `+6281234567801`. **Always filter on this** |
| `city` | text | Customer's city |
| `account_no` | text | 10 digits, e.g. `1230000001` |
| `product` | text | `TABUNGAN` (savings), `GIRO` (current), `DEPOSITO` (time deposit) |
| `currency` | text | Always `IDR` |
| `balance` | numeric(18,2) | Current balance |
| `account_status` | text | `ACTIVE`, `DORMANT`, `CLOSED` |

Source: `customers JOIN accounts ON accounts.customer_id = customers.id`.

### account_transactions: one row per transaction

| Column | Type | Notes |
|---|---|---|
| `cif`, `full_name`, `phone` | text | As above. **Always filter on `phone`** |
| `account_no` | text | The account the transaction is on |
| `posted_at` | timestamptz | When it was posted; sort newest first with `ORDER BY posted_at DESC` |
| `description` | text | Merchant or narrative, e.g. `Belanja Tokopedia`, `Tarik tunai ATM` |
| `channel` | text | `ATM`, `QRIS`, `TRANSFER`, `EDC`, `MOBILE`, `TELLER` |
| `amount` | numeric(18,2) | Negative = money out (debit), positive = money in (credit) |
| `direction` | text | `DEBIT` or `CREDIT`, derived from the sign of `amount` |

Source: `transactions JOIN accounts JOIN customers`.

### customer_cards: one row per card

| Column | Type | Notes |
|---|---|---|
| `cif`, `full_name`, `phone` | text | As above. **Always filter on `phone`** |
| `card_type` | text | `DEBIT` or `CREDIT` |
| `network` | text | `GPN`, `VISA`, `MASTERCARD` |
| `card_last4` | text | Last four digits; unique per customer. The only card digits stored |
| `expiry` | text | `MM/YY` |
| `card_status` | text | `ACTIVE` or `BLOCKED` |
| `blocked_at` | timestamptz | When it was blocked; null if active |
| `block_reason` | text | e.g. `LOST`, `STOLEN`; null if active |
| `account_no` | text | Linked account for debit cards; null for credit cards |

Source: `cards JOIN customers LEFT JOIN accounts`.

## Function (what sam_agent can call)

```sql
block_card(p_phone text, p_dob date, p_pin text, p_card_last4 text,
           p_reason text DEFAULT 'LOST', p_channel text DEFAULT 'IVR')
RETURNS TABLE (success boolean, reference text, message text)
```

What it does, in order:
1. **Lockout check.** If the phone has 3 or more `FAILED` verification attempts in the last 15 minutes since its last success, it records `LOCKED` and returns `VERIFICATION_LOCKED`.
2. **Identity check.** `phone` + `date_of_birth` + PIN (bcrypt) must all match one customer. It records `SUCCESS` or `FAILED`, and returns `IDENTITY_VERIFICATION_FAILED` on a mismatch.
3. **Card lookup.** It finds the card by `(customer, card_last4)`: `CARD_NOT_FOUND`, or `CARD_ALREADY_BLOCKED` if it is already blocked.
4. **Block.** It sets the card to `BLOCKED` with `blocked_at` and `block_reason`, writes `card_block_requests` with reference `BLK-YYYYMMDD-XXXXXX`, and returns `(true, reference, 'CARD_BLOCKED')`.

| success | reference | message |
|---|---|---|
| true | `BLK-...` | `CARD_BLOCKED` |
| false | null | `IDENTITY_VERIFICATION_FAILED` |
| false | null | `VERIFICATION_LOCKED` |
| false | null | `CARD_NOT_FOUND` |
| false | null | `CARD_ALREADY_BLOCKED` |

It runs as the table owner (`SECURITY DEFINER`), so it can write although `sam_agent` cannot.

## Base tables (not readable by sam_agent)

| Table | Key columns |
|---|---|
| `customers` | `id` serial PK, `cif` unique, `full_name`, `phone` unique, `email`, `date_of_birth` date, `city`, `pin_hash` (bcrypt, never exposed), `created_at` |
| `accounts` | `id` PK, `account_no` unique, `customer_id` → customers, `product`, `currency`, `balance`, `status`, `opened_at` date |
| `transactions` | `id` PK, `account_id` → accounts, `posted_at`, `description`, `channel`, `amount` |
| `cards` | `id` PK, `customer_id` → customers, `account_id` → accounts (nullable), `card_type`, `network`, `card_last4`, `expiry`, `status`, `blocked_at`, `block_reason`; unique `(customer_id, card_last4)` |
| `card_block_requests` | `id` PK, `reference` unique, `card_id` → cards, `reason`, `channel`, `created_at` |
| `verification_attempts` | `id` PK, `phone`, `outcome` (`SUCCESS`/`FAILED`/`LOCKED`), `channel`, `created_at` |

## PostgreSQL notes

- Text literals use single quotes: `'+6281234567801'`. Dates are `'1985-03-12'` (`YYYY-MM-DD`).
- Text comparisons are case-sensitive. Use `ILIKE '%indomaret%'` to search descriptions.
- Time windows: `posted_at >= now() - interval '30 days'`, or `date_trunc('month', now())` for this month.
- Sums of `numeric` stay exact: `SUM(amount)`. Money out is `SUM(-amount) ... WHERE amount < 0`.
