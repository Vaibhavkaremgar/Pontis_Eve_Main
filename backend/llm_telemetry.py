"""Best-effort, metadata-only LLM usage telemetry."""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

logger = logging.getLogger(__name__)


def enabled() -> bool:
    return os.environ.get("LLM_TELEMETRY_ENABLED", "true").lower() not in {"0", "false", "no", "off"}


def cost_usd(input_tokens: int | None, output_tokens: int | None) -> float | None:
    try:
        inp = float(os.environ["LLM_INPUT_PRICE_PER_1M_TOKENS"])
        out = float(os.environ["LLM_OUTPUT_PRICE_PER_1M_TOKENS"])
        if input_tokens is None or output_tokens is None:
            return None
        return input_tokens / 1_000_000 * inp + output_tokens / 1_000_000 * out
    except (KeyError, TypeError, ValueError):
        return None


async def write_events(events: list[dict[str, Any]]) -> None:
    """Persist events without allowing telemetry failures to affect callers."""
    if not events or not enabled() or os.environ.get("PYTEST_CURRENT_TEST"):
        return
    engine = None
    try:
        database_url = os.environ.get("DATABASE_URL")
        if not database_url:
            return
        engine = create_async_engine(database_url, pool_pre_ping=True)
        async with engine.begin() as conn:
            for event in events:
                await conn.execute(text("""
                    INSERT INTO llm_usage_events
                    (created_at, provider, model, workflow, function_name, candidate_id,
                     user_id, job_id, endpoint, request_id, attempt, key_id, status,
                     success, input_tokens, output_tokens, total_tokens, latency_ms,
                     cost_usd, error_type, provider_request_id, total_attempts,
                     is_retry, previous_attempt_failed)
                    VALUES
                    (:created_at, :provider, :model, :workflow, :function_name,
                     :candidate_id, :user_id, :job_id, :endpoint, :request_id,
                     :attempt, :key_id, :status, :success, :input_tokens,
                     :output_tokens, :total_tokens, :latency_ms, :cost_usd,
                     :error_type, :provider_request_id, :total_attempts,
                     :is_retry, :previous_attempt_failed)
                """), event)
    except Exception:
        logger.exception("LLM telemetry write failed; preserving LLM result")
    finally:
        if engine is not None:
            await engine.dispose()


def submit(events: list[dict[str, Any]]) -> None:
    if not events or not enabled() or os.environ.get("PYTEST_CURRENT_TEST"):
        return
    try:
        asyncio.create_task(write_events(events))
    except RuntimeError:
        logger.debug("No running event loop for LLM telemetry", exc_info=True)
