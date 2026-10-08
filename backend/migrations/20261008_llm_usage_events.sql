CREATE TABLE IF NOT EXISTS llm_usage_events (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    provider TEXT NOT NULL,
    model TEXT,
    workflow TEXT NOT NULL,
    function_name TEXT,
    candidate_id TEXT,
    user_id TEXT,
    job_id TEXT,
    endpoint TEXT,
    request_id TEXT,
    attempt INTEGER NOT NULL,
    key_id TEXT,
    status INTEGER,
    success BOOLEAN NOT NULL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    total_tokens INTEGER,
    latency_ms DOUBLE PRECISION,
    cost_usd NUMERIC,
    error_type TEXT,
    provider_request_id TEXT,
    total_attempts INTEGER NOT NULL,
    is_retry BOOLEAN NOT NULL,
    previous_attempt_failed BOOLEAN NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_llm_usage_created_at ON llm_usage_events (created_at);
CREATE INDEX IF NOT EXISTS idx_llm_usage_workflow ON llm_usage_events (workflow);
CREATE INDEX IF NOT EXISTS idx_llm_usage_candidate ON llm_usage_events (candidate_id);
CREATE INDEX IF NOT EXISTS idx_llm_usage_job ON llm_usage_events (job_id);
CREATE INDEX IF NOT EXISTS idx_llm_usage_status_success ON llm_usage_events (status, success);
