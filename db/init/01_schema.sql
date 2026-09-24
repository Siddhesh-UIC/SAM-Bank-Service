-- Mock core-banking schema for the Bahasa Indonesia IVR PoC.
-- PoC scope: balance enquiry, recent transactions, card block (with identity verification).

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE customers (
    id            SERIAL PRIMARY KEY,
    cif           TEXT UNIQUE NOT NULL,          -- Customer Information File number
    full_name     TEXT NOT NULL,
    phone         TEXT UNIQUE NOT NULL,          -- caller ID (ANI), E.164
    email         TEXT,
    date_of_birth DATE NOT NULL,
    city          TEXT NOT NULL,
    pin_hash      TEXT NOT NULL,                 -- 6-digit phone-banking PIN, bcrypt
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE accounts (
    id          SERIAL PRIMARY KEY,
    account_no  TEXT UNIQUE NOT NULL,
    customer_id INT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    product    TEXT NOT NULL CHECK (product IN ('TABUNGAN', 'GIRO', 'DEPOSITO')),
    currency    TEXT NOT NULL DEFAULT 'IDR',
    balance     NUMERIC(18,2) NOT NULL,
    status      TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'DORMANT', 'CLOSED')),
    opened_at   DATE NOT NULL
);

CREATE TABLE transactions (
    id          BIGSERIAL PRIMARY KEY,
    account_id  INT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    posted_at   TIMESTAMPTZ NOT NULL,
    description TEXT NOT NULL,
    channel     TEXT NOT NULL,                   -- ATM, QRIS, TRANSFER, EDC, MOBILE, TELLER
    amount      NUMERIC(18,2) NOT NULL           -- negative = debit, positive = credit
);
CREATE INDEX ON transactions (account_id, posted_at DESC);

CREATE TABLE cards (
    id           SERIAL PRIMARY KEY,
    customer_id  INT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    account_id   INT REFERENCES accounts(id) ON DELETE SET NULL,
    card_type    TEXT NOT NULL CHECK (card_type IN ('DEBIT', 'CREDIT')),
    network      TEXT NOT NULL,                  -- GPN, VISA, MASTERCARD
    card_last4   TEXT NOT NULL,
    expiry       TEXT NOT NULL,                  -- MM/YY
    status       TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'BLOCKED')),
    blocked_at   TIMESTAMPTZ,
    block_reason TEXT,
    UNIQUE (customer_id, card_last4)
);

CREATE TABLE card_block_requests (
    id         SERIAL PRIMARY KEY,
    reference  TEXT UNIQUE NOT NULL,
    card_id    INT NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    reason     TEXT NOT NULL,
    channel    TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── Agent-facing views (never expose pin_hash) ──────────────────────────────

CREATE VIEW customer_accounts AS
SELECT c.cif, c.full_name, c.phone, c.city,
       a.account_no, a.product, a.currency, a.balance, a.status AS account_status
FROM customers c JOIN accounts a ON a.customer_id = c.id;

CREATE VIEW account_transactions AS
SELECT c.cif, c.full_name, c.phone, a.account_no,
       t.posted_at, t.description, t.channel, t.amount,
       CASE WHEN t.amount < 0 THEN 'DEBIT' ELSE 'CREDIT' END AS direction
FROM transactions t
JOIN accounts a ON a.id = t.account_id
JOIN customers c ON c.id = a.customer_id;

CREATE VIEW customer_cards AS
SELECT c.cif, c.full_name, c.phone, k.card_type, k.network, k.card_last4, k.expiry,
       k.status AS card_status, k.blocked_at, k.block_reason, a.account_no
FROM cards k
JOIN customers c ON c.id = k.customer_id
LEFT JOIN accounts a ON a.id = k.account_id;

-- ── Write path: card block, gated by identity verification ─────────────────
-- Single source of truth — used by both the REST API and the SAM DB connector.

CREATE FUNCTION verify_customer(p_phone TEXT, p_dob DATE, p_pin TEXT)
RETURNS INT LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
    SELECT id FROM customers
    WHERE phone = p_phone AND date_of_birth = p_dob AND pin_hash = crypt(p_pin, pin_hash);
$$;

CREATE FUNCTION block_card(p_phone TEXT, p_dob DATE, p_pin TEXT, p_card_last4 TEXT,
                           p_reason TEXT DEFAULT 'LOST', p_channel TEXT DEFAULT 'IVR')
RETURNS TABLE (success BOOLEAN, reference TEXT, message TEXT)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
    v_customer INT;
    v_card     cards%ROWTYPE;
    v_ref      TEXT;
BEGIN
    v_customer := verify_customer(p_phone, p_dob, p_pin);
    IF v_customer IS NULL THEN
        RETURN QUERY SELECT false, NULL::TEXT, 'IDENTITY_VERIFICATION_FAILED';
        RETURN;
    END IF;

    SELECT * INTO v_card FROM cards WHERE customer_id = v_customer AND card_last4 = p_card_last4;
    IF NOT FOUND THEN
        RETURN QUERY SELECT false, NULL::TEXT, 'CARD_NOT_FOUND';
        RETURN;
    END IF;
    IF v_card.status = 'BLOCKED' THEN
        RETURN QUERY SELECT false, NULL::TEXT, 'CARD_ALREADY_BLOCKED';
        RETURN;
    END IF;

    UPDATE cards SET status = 'BLOCKED', blocked_at = now(), block_reason = p_reason WHERE id = v_card.id;
    v_ref := 'BLK-' || to_char(now(), 'YYYYMMDD') || '-' || upper(substr(md5(random()::text), 1, 6));
    INSERT INTO card_block_requests (reference, card_id, reason, channel) VALUES (v_ref, v_card.id, p_reason, p_channel);
    RETURN QUERY SELECT true, v_ref, 'CARD_BLOCKED';
END;
$$;

-- ── Least-privilege role for the SAM PostgreSQL connector ──────────────────
-- Can read the three views and call block_card(); cannot touch base tables.

CREATE ROLE sam_agent LOGIN PASSWORD 'sam_agent123';
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM sam_agent;
GRANT SELECT ON customer_accounts, account_transactions, customer_cards TO sam_agent;
REVOKE EXECUTE ON FUNCTION verify_customer(TEXT, DATE, TEXT) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION block_card(TEXT, DATE, TEXT, TEXT, TEXT, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION block_card(TEXT, DATE, TEXT, TEXT, TEXT, TEXT) TO sam_agent;
