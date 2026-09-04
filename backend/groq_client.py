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
from typing import Any, Optional

from openai import AsyncOpenAI, RateLimitError as OpenAIRateLimitError

logger = logging.getLogger(__name__)

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
        last_exc: Optional[OpenAIRateLimitError] = None

        for attempt in range(len(self._keys)):
            key_index = await self._pick_available_key()
            if key_index is None:
                # All keys are still in cooldown
                break

            client = self._clients[key_index]
            key_label = key_index + 1  # 1-based for logging

            try:
                return await client.chat.completions.create(**kwargs)
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
                # Continue loop — try next available key
            except Exception:
                # Non-429 errors: do NOT rotate, re-raise immediately
                raise

        raise AllKeysRateLimitedError(
            f"All {len(self._keys)} configured Groq API key(s) are currently "
            "rate-limited. Please try again later."
        ) from last_exc

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
