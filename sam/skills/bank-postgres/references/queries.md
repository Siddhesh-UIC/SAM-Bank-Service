# Queries for each request

Replace `<phone>` with the turn's `callerPhone`, exactly as given. Every query here filters on it; keep that filter in any query you adapt.

## Balances and accounts

All accounts with balances ("what's my balance?"):
```sql
SELECT account_no, product, currency, balance, account_status
FROM customer_accounts WHERE phone = '<phone>' ORDER BY product, account_no;
```

One kind of account ("my savings balance"). `product` is `TABUNGAN`, `GIRO` or `DEPOSITO`:
```sql
SELECT account_no, balance, currency, account_status
FROM customer_accounts WHERE phone = '<phone>' AND product = 'TABUNGAN';
```

One account, by the last digits the caller gives:
```sql
SELECT account_no, product, balance, currency, account_status
FROM customer_accounts WHERE phone = '<phone>' AND account_no LIKE '%0001';
```

Total across all active accounts:
```sql
SELECT currency, SUM(balance) AS total, COUNT(*) AS accounts
FROM customer_accounts WHERE phone = '<phone>' AND account_status = 'ACTIVE' GROUP BY currency;
```

The customer's name (to greet them, or to check the right person is calling):
```sql
SELECT DISTINCT full_name, city FROM customer_accounts WHERE phone = '<phone>';
```

## Transactions

Last five transactions on any account:
```sql
SELECT account_no, posted_at, description, channel, amount, direction
FROM account_transactions WHERE phone = '<phone>' ORDER BY posted_at DESC LIMIT 5;
```

Last five on one account:
```sql
SELECT posted_at, description, channel, amount, direction
FROM account_transactions WHERE phone = '<phone>' AND account_no = '1230000001'
ORDER BY posted_at DESC LIMIT 5;
```

The last 30 days, money out only:
```sql
SELECT posted_at, description, channel, amount
FROM account_transactions
WHERE phone = '<phone>' AND amount < 0 AND posted_at >= now() - interval '30 days'
ORDER BY posted_at DESC;
```

Search by merchant or description ("did my Tokopedia payment go through?"):
```sql
SELECT posted_at, account_no, description, amount, direction
FROM account_transactions WHERE phone = '<phone>' AND description ILIKE '%tokopedia%'
ORDER BY posted_at DESC LIMIT 5;
```

Money in and out this month:
```sql
SELECT SUM(amount) FILTER (WHERE amount > 0) AS money_in,
       SUM(-amount) FILTER (WHERE amount < 0) AS money_out
FROM account_transactions WHERE phone = '<phone>' AND posted_at >= date_trunc('month', now());
```

Spending by channel:
```sql
SELECT channel, COUNT(*) AS count, SUM(-amount) AS spent
FROM account_transactions WHERE phone = '<phone>' AND amount < 0
GROUP BY channel ORDER BY spent DESC;
```

The largest recent debit:
```sql
SELECT posted_at, description, channel, amount
FROM account_transactions WHERE phone = '<phone>' AND amount < 0
ORDER BY amount ASC LIMIT 1;
```

## Cards

All cards and their status:
```sql
SELECT card_type, network, card_last4, expiry, card_status, blocked_at, block_reason, account_no
FROM customer_cards WHERE phone = '<phone>' ORDER BY card_type;
```

Active cards only (step 1 of blocking):
```sql
SELECT card_type, network, card_last4 FROM customer_cards
WHERE phone = '<phone>' AND card_status = 'ACTIVE';
```

Is one card blocked?
```sql
SELECT card_status, blocked_at, block_reason FROM customer_cards
WHERE phone = '<phone>' AND card_last4 = '4821';
```

## Block a card

Only once the caller has chosen the card and told you their date of birth (see SKILL.md, "Blocking a card"). Never ask for a PIN: the call is already verified.
```sql
SELECT * FROM block_card_verified('<sessionToken>', '4821', '1985-03-12', 'LOST');
```
The result is one row, `success | reference | message`. Answer from `message`.

## Mistakes to avoid

| Wrong | Why | Right |
|---|---|---|
| `SELECT * FROM customers ...` / `accounts` / `cards` | Base tables: permission denied | Use the views |
| `JOIN ... ON a.customer_id = c.id` | The views have no id columns | Filter each view by `phone` directly |
| A query with no `WHERE phone = ...` | Returns other customers' data | Always filter by the caller's phone |
| `balance` from `account_transactions` | Transactions have no balance | Balance is in `customer_accounts` |
| `WHERE phone = '081234567801'` | Phones are stored in E.164 | Use `callerPhone` exactly: `'+6281234567801'` |
| `SELECT block_card_verified(...)` | Returns one composite value | `SELECT * FROM block_card_verified(...)` |
| `block_card('<phone>', ..., '<pin>', ...)`, or asking for the PIN | The call is already verified; the PIN is never needed | `block_card_verified('<sessionToken>', ...)` |
