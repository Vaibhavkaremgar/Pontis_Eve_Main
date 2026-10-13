CREATE TABLE IF NOT EXISTS oauth_transactions (
    id UUID PRIMARY KEY,
    provider TEXT NOT NULL,
    state_hash CHAR(64) NOT NULL UNIQUE,
    pkce_verifier TEXT NOT NULL,
    state_expires_at TIMESTAMPTZ NOT NULL,
    state_consumed_at TIMESTAMPTZ,
    exchange_code_hash CHAR(64) UNIQUE,
    exchange_payload JSONB,
    exchange_expires_at TIMESTAMPTZ,
    exchange_consumed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT oauth_transactions_exchange_fields_check CHECK (
        (exchange_code_hash IS NULL AND exchange_payload IS NULL AND exchange_expires_at IS NULL)
        OR
        (exchange_code_hash IS NOT NULL AND exchange_payload IS NOT NULL AND exchange_expires_at IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_oauth_transactions_state_expiry
    ON oauth_transactions (state_expires_at);
CREATE INDEX IF NOT EXISTS idx_oauth_transactions_exchange_expiry
    ON oauth_transactions (exchange_expires_at)
    WHERE exchange_expires_at IS NOT NULL;
