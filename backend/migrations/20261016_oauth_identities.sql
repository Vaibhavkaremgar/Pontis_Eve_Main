CREATE TABLE IF NOT EXISTS oauth_identities (
    id UUID PRIMARY KEY,
    provider TEXT NOT NULL,
    provider_subject TEXT NOT NULL,
    candidate_id UUID NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT oauth_identities_provider_subject_unique UNIQUE (provider, provider_subject),
    CONSTRAINT oauth_identities_candidate_provider_unique UNIQUE (candidate_id, provider),
    CONSTRAINT oauth_identities_provider_check CHECK (provider IN ('linkedin'))
);

CREATE INDEX IF NOT EXISTS idx_oauth_identities_candidate
    ON oauth_identities (candidate_id);
