-- Additive, backward-compatible upgrade for databases that already ran the
-- initial llm_usage_events migration.
ALTER TABLE llm_usage_events ADD COLUMN IF NOT EXISTS session_id TEXT;
ALTER TABLE llm_usage_events ADD COLUMN IF NOT EXISTS vapi_call_id TEXT;
-- On PostgreSQL 11+, a constant default is catalog-only for existing rows.
-- Keep this nullable in the additive migration: adding NOT NULL would scan the
-- existing table while holding a stronger lock. New writes receive FALSE;
-- a later, separately scheduled migration may backfill and validate NOT NULL.
ALTER TABLE llm_usage_events ADD COLUMN IF NOT EXISTS is_fallback BOOLEAN DEFAULT FALSE;
CREATE INDEX IF NOT EXISTS idx_llm_usage_request_id ON llm_usage_events (request_id);
CREATE INDEX IF NOT EXISTS idx_llm_usage_session_id ON llm_usage_events (session_id);
CREATE INDEX IF NOT EXISTS idx_llm_usage_vapi_call_id ON llm_usage_events (vapi_call_id);

CREATE TABLE IF NOT EXISTS llm_usage_request_candidate_links (
    request_id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL,
    linked_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
