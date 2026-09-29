-- Verified IVR call sessions: the voice pipe verifies the caller's PIN once, at the start of the call, and gets a
-- session token. Everything after that in the call acts for that one customer through the token, so the agent never
-- asks for the PIN again. Idempotent: an existing database can apply it with psql -f.

CREATE TABLE IF NOT EXISTS ivr_sessions (
    token       TEXT PRIMARY KEY,                -- 256-bit random, hex
    customer_id INT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    channel     TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at  TIMESTAMPTZ NOT NULL,
    ended_at    TIMESTAMPTZ                      -- set when the call hangs up
);

-- Same rule as block_card(): 3 wrong attempts within 15 minutes (since the last success) lock the phone number.
CREATE OR REPLACE FUNCTION phone_locked(p_phone TEXT)
RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
    SELECT count(*) >= 3 FROM verification_attempts
    WHERE phone = p_phone AND outcome = 'FAILED' AND created_at > now() - interval '15 minutes'
      AND created_at > coalesce((SELECT max(created_at) FROM verification_attempts
                                 WHERE phone = p_phone AND outcome = 'SUCCESS'), '-infinity');
$$;

-- Check the PIN for a caller's number and open a session. Results:
--   (true,  token, name, 'VERIFIED')
--   (false, null,  null, 'CUSTOMER_NOT_FOUND' | 'VERIFICATION_LOCKED' | 'IDENTITY_VERIFICATION_FAILED')
CREATE OR REPLACE FUNCTION open_ivr_session(p_phone TEXT, p_pin TEXT, p_channel TEXT DEFAULT 'IVR')
RETURNS TABLE (success BOOLEAN, token TEXT, full_name TEXT, message TEXT)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
    v_customer customers%ROWTYPE;
    v_token    TEXT;
BEGIN
    SELECT * INTO v_customer FROM customers c WHERE c.phone = p_phone;
    IF NOT FOUND THEN
        RETURN QUERY SELECT false, NULL::TEXT, NULL::TEXT, 'CUSTOMER_NOT_FOUND';
        RETURN;
    END IF;
    IF phone_locked(p_phone) THEN
        INSERT INTO verification_attempts (phone, outcome, channel) VALUES (p_phone, 'LOCKED', p_channel);
        RETURN QUERY SELECT false, NULL::TEXT, NULL::TEXT, 'VERIFICATION_LOCKED';
        RETURN;
    END IF;
    IF v_customer.pin_hash <> crypt(p_pin, v_customer.pin_hash) THEN
        INSERT INTO verification_attempts (phone, outcome, channel) VALUES (p_phone, 'FAILED', p_channel);
        RETURN QUERY SELECT false, NULL::TEXT, NULL::TEXT, 'IDENTITY_VERIFICATION_FAILED';
        RETURN;
    END IF;
    INSERT INTO verification_attempts (phone, outcome, channel) VALUES (p_phone, 'SUCCESS', p_channel);
    v_token := encode(gen_random_bytes(32), 'hex');
    INSERT INTO ivr_sessions (token, customer_id, channel, expires_at)
    VALUES (v_token, v_customer.id, p_channel, now() + interval '30 minutes');
    RETURN QUERY SELECT true, v_token, v_customer.full_name, 'VERIFIED';
END;
$$;

CREATE OR REPLACE FUNCTION end_ivr_session(p_token TEXT)
RETURNS BOOLEAN LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
    UPDATE ivr_sessions SET ended_at = now() WHERE token = p_token AND ended_at IS NULL RETURNING true;
$$;

-- Block a card for the customer of a verified session: the caller entered their PIN at the start of the call, so only
-- the card's last 4 digits are needed. The session decides whose cards these are, so the agent cannot block another
-- customer's card whatever it is told. Results:
--   (true, 'BLK-...', 'CARD_BLOCKED')
--   (false, null, 'SESSION_INVALID' | 'CARD_NOT_FOUND' | 'CARD_ALREADY_BLOCKED')
DROP FUNCTION IF EXISTS block_card_verified(TEXT, TEXT, DATE, TEXT);  -- the earlier version also asked for the DOB
CREATE OR REPLACE FUNCTION block_card_verified(p_token TEXT, p_card_last4 TEXT, p_reason TEXT DEFAULT 'LOST')
RETURNS TABLE (success BOOLEAN, reference TEXT, message TEXT)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
    v_customer INT;
    v_channel  TEXT;
    v_card     cards%ROWTYPE;
    v_ref      TEXT;
BEGIN
    SELECT s.customer_id, s.channel INTO v_customer, v_channel FROM ivr_sessions s
    WHERE s.token = p_token AND s.ended_at IS NULL AND s.expires_at > now();
    IF NOT FOUND THEN
        RETURN QUERY SELECT false, NULL::TEXT, 'SESSION_INVALID';
        RETURN;
    END IF;

    SELECT * INTO v_card FROM cards k WHERE k.customer_id = v_customer AND k.card_last4 = p_card_last4;
    IF NOT FOUND THEN
        RETURN QUERY SELECT false, NULL::TEXT, 'CARD_NOT_FOUND';
        RETURN;
    END IF;
    IF v_card.status = 'BLOCKED' THEN
        RETURN QUERY SELECT false, NULL::TEXT, 'CARD_ALREADY_BLOCKED';
        RETURN;
    END IF;
    -- ponytail: same block steps as block_card(); fold both into one helper if a third caller appears
    UPDATE cards SET status = 'BLOCKED', blocked_at = now(), block_reason = p_reason WHERE id = v_card.id;
    v_ref := 'BLK-' || to_char(now(), 'YYYYMMDD') || '-' || upper(substr(md5(random()::text), 1, 6));
    INSERT INTO card_block_requests (reference, card_id, reason, channel) VALUES (v_ref, v_card.id, p_reason, v_channel);
    RETURN QUERY SELECT true, v_ref, 'CARD_BLOCKED';
END;
$$;

-- Only the bank API (the owner) opens and ends sessions; the SAM connector may only block through one.
REVOKE EXECUTE ON FUNCTION phone_locked(TEXT), open_ivr_session(TEXT, TEXT, TEXT), end_ivr_session(TEXT),
                           block_card_verified(TEXT, TEXT, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION block_card_verified(TEXT, TEXT, TEXT) TO sam_agent;
