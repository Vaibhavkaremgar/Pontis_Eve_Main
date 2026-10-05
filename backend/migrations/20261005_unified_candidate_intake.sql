-- Additive migration: unified candidate intake ledger and idempotent Vapi events.
-- Existing candidate, voice, chat, resume, and recommendation data is untouched.
BEGIN;

CREATE TABLE IF NOT EXISTS candidate_intake_ledger (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    candidate_id UUID NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,
    topic_id TEXT NOT NULL,
    question_id TEXT NOT NULL,
    channel TEXT NOT NULL CHECK (channel IN ('vapi', 'chat', 'system')),
    question_text TEXT,
    answer_text TEXT,
    status TEXT NOT NULL CHECK (status IN
        ('NOT_ASKED','ASKED','ANSWERED','PARTIALLY_ANSWERED','SKIPPED','INVALIDATED')),
    source_event_id TEXT,
    evidence_reference TEXT,
    asked_at TIMESTAMPTZ,
    answered_at TIMESTAMPTZ,
    supersedes UUID REFERENCES candidate_intake_ledger(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (candidate_id, topic_id)
);

CREATE INDEX IF NOT EXISTS idx_cil_source_event
ON candidate_intake_ledger(candidate_id, source_event_id)
WHERE source_event_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS candidate_voice_provider_events (
    provider_event_id TEXT PRIMARY KEY,
    candidate_id UUID NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,
    vapi_call_id TEXT,
    transcript_hash TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'processing',
    processed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_cvpe_candidate_call
ON candidate_voice_provider_events(candidate_id, vapi_call_id);

COMMIT;
