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
                # Allow older callers that build event dictionaries directly.
                event.setdefault("session_id", None)
                event.setdefault("vapi_call_id", None)
                event.setdefault("is_fallback", False)
                await conn.execute(text("""
                    INSERT INTO llm_usage_events
                    (created_at, provider, model, workflow, function_name, candidate_id,
                     user_id, job_id, endpoint, request_id, attempt, key_id, status,
                     success, input_tokens, output_tokens, total_tokens, latency_ms,
                     cost_usd, error_type, provider_request_id, total_attempts,
                     is_retry, previous_attempt_failed, session_id, vapi_call_id, is_fallback)
                    VALUES
                    (:created_at, :provider, :model, :workflow, :function_name,
                     :candidate_id, :user_id, :job_id, :endpoint, :request_id,
                     :attempt, :key_id, :status, :success, :input_tokens,
                     :output_tokens, :total_tokens, :latency_ms, :cost_usd,
                     :error_type, :provider_request_id, :total_attempts,
                     :is_retry, :previous_attempt_failed, :session_id, :vapi_call_id, :is_fallback)
                """), event)
                # A resume parse can finish before a candidate exists.  The
                # durable link handles either ordering between this async write
                # and the later successful candidate creation.
                await _reconcile_events_for_request(conn, event.get("request_id"))
    except Exception:
        logger.exception("LLM telemetry write failed; preserving LLM result")
    finally:
        if engine is not None:
            await engine.dispose()


def submit(events: list[dict[str, Any]]) -> None:
    if not events or not enabled() or os.environ.get("PYTEST_CURRENT_TEST"):
        return
    task = None
    try:
        task = write_events(events)
        asyncio.create_task(task)
    except RuntimeError:
        if task is not None:
            task.close()
        logger.debug("No running event loop for LLM telemetry", exc_info=True)


async def associate_candidate(request_id: str | None, candidate_id: str | None) -> None:
    """Best-effort exact request-to-candidate attribution; never affects product flow."""
    if not request_id or not candidate_id or not enabled() or os.environ.get("PYTEST_CURRENT_TEST"):
        return
    engine = None
    try:
        database_url = os.environ.get("DATABASE_URL")
        if not database_url:
            return
        engine = create_async_engine(database_url, pool_pre_ping=True)
        async with engine.begin() as conn:
            await _associate_candidate_on_connection(conn, request_id, candidate_id)
    except Exception:
        logger.exception("LLM telemetry candidate association failed; preserving product flow")
    finally:
        if engine is not None:
            await engine.dispose()


async def _associate_candidate_on_connection(conn: Any, request_id: str, candidate_id: str) -> None:
    """Persist exact correlation and update events only through its stored link."""
    await conn.execute(text("""
        INSERT INTO llm_usage_request_candidate_links (request_id, candidate_id)
        VALUES (:request_id, :candidate_id)
        ON CONFLICT (request_id) DO NOTHING
    """), {"request_id": request_id, "candidate_id": candidate_id})
    stored = await conn.execute(text("""
        SELECT candidate_id FROM llm_usage_request_candidate_links
        WHERE request_id = :request_id
    """), {"request_id": request_id})
    row = stored.fetchone()
    if not row or str(row[0]) != str(candidate_id):
        logger.warning("LLM telemetry request-candidate association conflict; preserving existing attribution")
        return
    await conn.execute(text("""
        UPDATE llm_usage_events e
        SET candidate_id = l.candidate_id
        FROM llm_usage_request_candidate_links l
        WHERE e.request_id = l.request_id
          AND e.request_id = :request_id
          AND l.candidate_id = :candidate_id
          AND e.candidate_id IS NULL
    """), {"request_id": request_id, "candidate_id": candidate_id})


async def _reconcile_events_for_request(conn: Any, request_id: str | None) -> None:
    """Apply an existing exact link only to events from this telemetry request."""
    if not request_id:
        return
    await conn.execute(text("""
        UPDATE llm_usage_events e
        SET candidate_id = l.candidate_id
        FROM llm_usage_request_candidate_links l
        WHERE e.request_id = l.request_id
          AND e.request_id = :request_id
          AND e.candidate_id IS NULL
    """), {"request_id": request_id})


async def associate_candidate_in_transaction(db: Any, request_id: str | None, candidate_id: str | None) -> None:
    """Best-effort association within the caller's transaction when available."""
    if not request_id or not candidate_id or not enabled():
        return
    try:
        async with db.begin_nested():
            await _associate_candidate_on_connection(db, request_id, candidate_id)
    except Exception:
        logger.exception("LLM telemetry candidate association failed; preserving product flow")


def submit_candidate_association(request_id: str | None, candidate_id: str | None) -> None:
    task = None
    try:
        task = associate_candidate(request_id, candidate_id)
        asyncio.create_task(task)
    except RuntimeError:
        if task is not None:
            task.close()
        logger.debug("No running event loop for LLM telemetry association", exc_info=True)
