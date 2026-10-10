"""
Centralized Groq API key pool with automatic rate-limit failover.

Reads up to 7 keys from GROQ_API_KEY_1 … GROQ_API_KEY_7 (plus the legacy
GROQ_API_KEY as a fallback for GROQ_API_KEY_1).  On HTTP 429 / RateLimitError
the current key is cooled down and the next available key is tried transparently.
All other errors are re-raised immediately without rotation.
"""

import asyncio
import logging
import os
import time
import inspect
import uuid
from typing import Any, Optional

from openai import AsyncOpenAI, RateLimitError as OpenAIRateLimitError

logger = logging.getLogger(__name__)

_WORKFLOW_BY_FUNCTION = {
    "_parse_resume_with_llm": "resume_parsing",
    "_llm_analyze_intake": "voice_intake_analysis",
    "_extract_voice_info": "voice_info_extraction",
    "_extract_preferred_roles_from_message": "preferred_role_extraction",
    "_extract_and_merge_chat_facts": "chat_fact_extraction",
    "_extract_multi_field_updates_from_answer": "chat_profile_extraction",
    "extract_missing_job_skills": "job_skill_extraction",
}


def _call_metadata(kwargs: dict[str, Any]) -> dict[str, Any]:
    metadata = dict(kwargs.pop("_telemetry", {}) or {})
    # The requested model is authoritative.  In particular, do not lose it
    # when callers supplied workflow metadata but omitted model metadata.
    metadata.setdefault("model", kwargs.get("model"))
    if metadata.get("workflow"):
        return metadata
    for frame in inspect.stack()[2:]:
        workflow = _WORKFLOW_BY_FUNCTION.get(frame.function)
        if workflow:
            metadata.update({"workflow": workflow, "function_name": frame.function})
            return metadata
    metadata.update({"workflow": "chat_generation", "function_name": "chat_completions_create"})
    return metadata


def _usage(response: Any) -> tuple[int | None, int | None, int | None]:
    usage = getattr(response, "usage", None)
    return (getattr(usage, "prompt_tokens", None), getattr(usage, "completion_tokens", None), getattr(usage, "total_tokens", None))

_DEFAULT_COOLDOWN_SECONDS = 60  # fallback when Retry-After header is absent


def _load_api_keys() -> list[str]:
    """Return non-empty Groq API keys from environment variables."""
    keys: list[str] = []
    for i in range(1, 8):
        key = os.environ.get(f"GROQ_API_KEY_{i}", "").strip()
        if key:
            keys.append(key)
    # Backward-compat: if no numbered keys, fall back to legacy GROQ_API_KEY
    if not keys:
        legacy = os.environ.get("GROQ_API_KEY", "").strip()
        if legacy:
            keys.append(legacy)
    return keys


def _parse_retry_after(exc: OpenAIRateLimitError) -> float:
    """Extract Retry-After seconds from a RateLimitError, or return the default."""
    try:
        headers = getattr(exc, "response", None)
        if headers is not None:
            header_val = getattr(headers, "headers", {}).get("retry-after") or \
                         getattr(headers, "headers", {}).get("x-ratelimit-reset-requests") or \
                         getattr(headers, "headers", {}).get("x-ratelimit-reset-tokens")
            if header_val:
                return max(1.0, float(header_val))
    except Exception:
        pass
    return float(_DEFAULT_COOLDOWN_SECONDS)


class GroqClientPool:
    """
    Thread-safe / async-safe pool of Groq API clients.

    Usage:
        pool = GroqClientPool(base_url="https://api.groq.com/openai/v1")
        response = await pool.chat_completions_create(model=..., messages=..., ...)
    """

    def __init__(self, base_url: str = "https://api.groq.com/openai/v1") -> None:
        self._base_url = base_url
        self._keys = _load_api_keys()
        if not self._keys:
            raise RuntimeError(
                "No Groq API keys configured. "
                "Set GROQ_API_KEY_1 (or GROQ_API_KEY) in your environment."
            )
        # One AsyncOpenAI client per key
        self._clients: list[AsyncOpenAI] = [
            AsyncOpenAI(api_key=key, base_url=base_url) for key in self._keys
        ]
        # Timestamp after which each key is available again (0 = available now)
        self._available_after: list[float] = [0.0] * len(self._keys)
        self._lock = asyncio.Lock()
        logger.info("GroqClientPool initialised with %d key(s).", len(self._keys))

    # ------------------------------------------------------------------
    # Public interface — mirrors openai_client.chat.completions.create
    # ------------------------------------------------------------------

    async def chat_completions_create(self, **kwargs: Any) -> Any:
        """
        Call Groq chat completions with automatic key rotation on 429.

        Raises the last RateLimitError (wrapped as AllKeysRateLimitedError) if
        every key is exhausted, or re-raises non-429 errors immediately.
        """
        from llm_telemetry import cost_usd, submit
        metadata = _call_metadata(kwargs)
        request_id = metadata.get("request_id") or str(uuid.uuid4())
        events: list[dict[str, Any]] = []
        last_exc: Optional[OpenAIRateLimitError] = None

        for attempt in range(len(self._keys)):
            key_index = await self._pick_available_key()
            if key_index is None:
                # All keys are still in cooldown
                break

            client = self._clients[key_index]
            key_label = key_index + 1  # 1-based for logging
            started = time.monotonic()
            attempt = len(events) + 1

            try:
                response = await client.chat.completions.create(**kwargs)
                input_tokens, output_tokens, total_tokens = _usage(response)
                status = getattr(getattr(response, "_response", None), "status_code", None) or 200
                events.append(self._event(metadata, request_id, attempt, key_label, status, True, started, input_tokens, output_tokens, total_tokens, response=response))
                total_attempts = len(events)
                for event in events:
                    event["total_attempts"] = total_attempts
                    event["previous_attempt_failed"] = event["attempt"] > 1
                submit(events)
                return response
            except OpenAIRateLimitError as exc:
                cooldown = _parse_retry_after(exc)
                async with self._lock:
                    self._available_after[key_index] = time.monotonic() + cooldown
                logger.warning(
                    "Groq key %d rate limited, switching to next key (cooldown %.0fs).",
                    key_label,
                    cooldown,
                )
                last_exc = exc
                events.append(self._event(metadata, request_id, attempt, key_label, 429, False, started, None, None, None, exc=exc))
                # Continue loop — try next available key
            except Exception as exc:
                # Non-429 errors: do NOT rotate, re-raise immediately
                events.append(self._event(metadata, request_id, attempt, key_label, getattr(getattr(exc, "response", None), "status_code", None), False, started, None, None, None, exc=exc))
                for event in events:
                    event["total_attempts"] = len(events)
                    event["previous_attempt_failed"] = event["attempt"] > 1
                submit(events)
                raise

        for event in events:
            event["total_attempts"] = len(events)
            event["previous_attempt_failed"] = event["attempt"] > 1
        submit(events)
        raise AllKeysRateLimitedError(
            f"All {len(self._keys)} configured Groq API key(s) are currently "
            "rate-limited. Please try again later."
        ) from last_exc

    @staticmethod
    def _event(metadata: dict[str, Any], request_id: str, attempt: int, key_label: int,
               status: Any, success: bool, started: float, input_tokens: Any,
               output_tokens: Any, total_tokens: Any, response: Any = None,
               exc: Any = None) -> dict[str, Any]:
        from llm_telemetry import cost_usd
        return {"created_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
                "provider": "groq", "model": metadata.get("model"),
                "workflow": metadata.get("workflow", "chat_generation"),
                "function_name": metadata.get("function_name"), "candidate_id": metadata.get("candidate_id"),
                "user_id": metadata.get("user_id"), "job_id": metadata.get("job_id"),
                "endpoint": metadata.get("endpoint"), "request_id": request_id,
                "session_id": metadata.get("session_id"), "vapi_call_id": metadata.get("vapi_call_id"),
                "attempt": attempt, "key_id": f"groq_key_{key_label}", "status": status,
                "success": success, "input_tokens": input_tokens, "output_tokens": output_tokens,
                "total_tokens": total_tokens, "latency_ms": (time.monotonic() - started) * 1000,
                "cost_usd": cost_usd(input_tokens, output_tokens),
                "error_type": type(exc).__name__ if exc else None,
                "provider_request_id": getattr(response, "id", None), "total_attempts": 1,
                "is_retry": attempt > 1, "previous_attempt_failed": attempt > 1,
                "is_fallback": bool(metadata.get("is_fallback"))}

    # ------------------------------------------------------------------
    # Compatibility shim: expose .chat.completions.create attribute path
    # so existing code that does `openai_client.chat.completions.create(...)`
    # can be replaced with `groq_pool.chat.completions.create(...)`.
    # ------------------------------------------------------------------

    @property
    def chat(self) -> "_ChatNamespace":
        return _ChatNamespace(self)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _pick_available_key(self) -> Optional[int]:
        """Return the index of the first available (non-rate-limited) key."""
        now = time.monotonic()
        async with self._lock:
            for i, available_after in enumerate(self._available_after):
                if now >= available_after:
                    return i
        return None

    # ------------------------------------------------------------------
    # Introspection helpers (used by tests)
    # ------------------------------------------------------------------

    @property
    def key_count(self) -> int:
        return len(self._keys)

    def is_key_available(self, index: int) -> bool:
        return time.monotonic() >= self._available_after[index]

    def mark_key_rate_limited(self, index: int, cooldown: float = _DEFAULT_COOLDOWN_SECONDS) -> None:
        """Manually mark a key as rate-limited (used in tests)."""
        self._available_after[index] = time.monotonic() + cooldown

    def reset_key(self, index: int) -> None:
        """Mark a key as immediately available (used in tests)."""
        self._available_after[index] = 0.0


class AllKeysRateLimitedError(Exception):
    """Raised when every configured Groq API key is currently rate-limited."""


# ---------------------------------------------------------------------------
# Compatibility shim objects
# ---------------------------------------------------------------------------

class _CompletionsNamespace:
    def __init__(self, pool: GroqClientPool) -> None:
        self._pool = pool

    async def create(self, **kwargs: Any) -> Any:
        return await self._pool.chat_completions_create(**kwargs)


class _ChatNamespace:
    def __init__(self, pool: GroqClientPool) -> None:
        self.completions = _CompletionsNamespace(pool)
