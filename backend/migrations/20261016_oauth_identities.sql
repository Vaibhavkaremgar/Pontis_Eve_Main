CREATE TABLE IF NOT EXISTS oauth_identities (
    id UUID PRIMARY KEY,
    provider TEXT NOT NULL,
    provider_subject TEXT NOT NULL,
    candidate_id UUID NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT oauth_identities_provider_subject_unique UNIQUE (provider, provider_subject),
    CONSTRAINT oauth_identities_candidate_provider_unique UNIQUE (candidate_id, provider),
    CONSTRAINT oauth_identities_provider_check CHECK (provider IN ('google', 'linkedin'))
);

-- This migration may run against databases where the table and its original
-- LinkedIn-only constraint already exist. Rebuild the constraint idempotently
-- so Google identities are accepted without changing existing mappings.
DO $$
BEGIN
    ALTER TABLE oauth_identities
        DROP CONSTRAINT IF EXISTS oauth_identities_provider_check;
    ALTER TABLE oauth_identities
        ADD CONSTRAINT oauth_identities_provider_check
        CHECK (provider IN ('google', 'linkedin'));
END
$$;

CREATE INDEX IF NOT EXISTS idx_oauth_identities_candidate
    ON oauth_identities (candidate_id);
