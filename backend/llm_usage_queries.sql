-- Replace CURRENT_DATE with a reporting timezone boundary when needed.
SELECT COUNT(*) AS calls_today, SUM(input_tokens) AS input_tokens_today,
       SUM(output_tokens) AS output_tokens_today, SUM(total_tokens) AS total_tokens_today,
       SUM(cost_usd) AS estimated_cost_today
FROM llm_usage_events WHERE created_at >= CURRENT_DATE;

-- Calls/workflow, tokens/workflow, errors, retries, latency, and candidate usage.
SELECT workflow, COUNT(*) calls, SUM(total_tokens) total_tokens,
       AVG(input_tokens) avg_input_tokens, AVG(output_tokens) avg_output_tokens,
       AVG(latency_ms) avg_latency_ms, COUNT(*) FILTER (WHERE is_retry) retry_count,
       COUNT(*) FILTER (WHERE status = 429) errors_429,
       COUNT(*) FILTER (WHERE status = 401) errors_401
FROM llm_usage_events GROUP BY workflow ORDER BY total_tokens DESC NULLS LAST;

-- Required time-window examples:
SELECT COUNT(*) FROM llm_usage_events WHERE created_at >= now() - interval '7 days';
SELECT SUM(total_tokens) FROM llm_usage_events WHERE created_at >= CURRENT_DATE - interval '30 days';
SELECT candidate_id, COUNT(*) calls, SUM(total_tokens) total_tokens
FROM llm_usage_events WHERE candidate_id IS NOT NULL GROUP BY candidate_id ORDER BY total_tokens DESC NULLS LAST;

-- Candidate-level report.  `provider_attempts` is intentionally the raw event
-- count; `logical_requests` deduplicates retries and fallback provider calls
-- by non-NULL opaque request_id. `uncorrelated_attempts` exposes attempts
-- omitted from that metric because their request_id is NULL. NULL token values
-- remain unknown rather than zero.
SELECT
    candidate_id,
    workflow,
    COUNT(*) AS provider_attempts,
    COUNT(DISTINCT request_id) AS logical_requests,
    COUNT(*) FILTER (WHERE request_id IS NULL) AS uncorrelated_attempts,
    COUNT(*) FILTER (WHERE is_retry OR previous_attempt_failed) AS retry_attempts,
    COUNT(*) FILTER (WHERE is_fallback) AS fallback_attempts,
    COUNT(*) FILTER (WHERE total_tokens IS NULL) AS unknown_token_attempts,
    SUM(input_tokens) AS input_tokens,
    SUM(output_tokens) AS output_tokens,
    SUM(total_tokens) AS total_tokens,
    SUM(cost_usd) AS calculated_cost_usd,
    ARRAY_AGG(DISTINCT model) FILTER (WHERE model IS NOT NULL) AS models,
    ARRAY_AGG(DISTINCT endpoint) FILTER (WHERE endpoint IS NOT NULL) AS endpoints
FROM llm_usage_events
WHERE candidate_id IS NOT NULL
  AND created_at >= :start_at
  AND created_at < :end_at
GROUP BY candidate_id, workflow
ORDER BY candidate_id, workflow;
