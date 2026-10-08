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
