"""
Tests for Groq API key rotation and rate-limit failover (groq_client.py).

Covers:
- Single key configured and works normally
- Multiple keys loaded correctly
- First key succeeds → no rotation
- First key 429 → second key used
- First and second 429 → third key used
- Rotation continues until a key succeeds
- All keys rate-limited → AllKeysRateLimitedError
- Non-429 errors do NOT cause rotation
- Rate-limited keys become available again after cooldown
- API keys never exposed in logs or error messages
- Concurrent requests do not corrupt key state
- Backward-compat: GROQ_API_KEY fallback
- server._parse_resume_with_llm returns HTTP 429 when all keys exhausted
"""

import asyncio
import os
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from openai import RateLimitError as OpenAIRateLimitError

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from groq_client import AllKeysRateLimitedError, GroqClientPool, _load_api_keys


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_rate_limit_error() -> OpenAIRateLimitError:
    fake_response = MagicMock()
    fake_response.status_code = 429
    fake_response.headers = {}
    fake_response.text = "rate limited"
    return OpenAIRateLimitError("rate limited", response=fake_response, body=None)


def _make_rate_limit_error_with_retry_after(seconds: float) -> OpenAIRateLimitError:
    fake_response = MagicMock()
    fake_response.status_code = 429
    fake_response.headers = {"retry-after": str(seconds)}
    fake_response.text = "rate limited"
    return OpenAIRateLimitError("rate limited", response=fake_response, body=None)


def _make_success_response(content: str = "ok") -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


def _pool_with_keys(*keys: str) -> GroqClientPool:
    """Build a GroqClientPool with the given keys, bypassing env-var loading."""
    env = {f"GROQ_API_KEY_{i+1}": key for i, key in enumerate(keys)}
    with patch.dict(os.environ, env, clear=False):
        # Clear any existing numbered keys beyond what we set
        for i in range(len(keys) + 1, 8):
            os.environ.pop(f"GROQ_API_KEY_{i}", None)
        os.environ.pop("GROQ_API_KEY", None)
        return GroqClientPool()


# ---------------------------------------------------------------------------
# 1. Key loading
# ---------------------------------------------------------------------------

class TestKeyLoading:
    def test_single_numbered_key_loaded(self):
        with patch.dict(os.environ, {"GROQ_API_KEY_1": "key-a"}, clear=False):
            for i in range(2, 8):
                os.environ.pop(f"GROQ_API_KEY_{i}", None)
            os.environ.pop("GROQ_API_KEY", None)
            keys = _load_api_keys()
        assert keys == ["key-a"]

    def test_multiple_keys_loaded_in_order(self):
        env = {
            "GROQ_API_KEY_1": "key-1",
            "GROQ_API_KEY_2": "key-2",
            "GROQ_API_KEY_3": "key-3",
        }
        with patch.dict(os.environ, env, clear=False):
            for i in range(4, 8):
                os.environ.pop(f"GROQ_API_KEY_{i}", None)
            os.environ.pop("GROQ_API_KEY", None)
            keys = _load_api_keys()
        assert keys == ["key-1", "key-2", "key-3"]

    def test_empty_keys_ignored(self):
        env = {
            "GROQ_API_KEY_1": "key-1",
            "GROQ_API_KEY_2": "",          # empty — must be skipped
            "GROQ_API_KEY_3": "key-3",
        }
        with patch.dict(os.environ, env, clear=False):
            for i in range(4, 8):
                os.environ.pop(f"GROQ_API_KEY_{i}", None)
            os.environ.pop("GROQ_API_KEY", None)
            keys = _load_api_keys()
        assert keys == ["key-1", "key-3"]

    def test_legacy_groq_api_key_fallback(self):
        """If no numbered keys exist, GROQ_API_KEY is used as fallback."""
        env = {"GROQ_API_KEY": "legacy-key"}
        with patch.dict(os.environ, env, clear=False):
            for i in range(1, 8):
                os.environ.pop(f"GROQ_API_KEY_{i}", None)
            keys = _load_api_keys()
        assert keys == ["legacy-key"]

    def test_numbered_keys_take_precedence_over_legacy(self):
        env = {"GROQ_API_KEY": "legacy-key", "GROQ_API_KEY_1": "numbered-key"}
        with patch.dict(os.environ, env, clear=False):
            for i in range(2, 8):
                os.environ.pop(f"GROQ_API_KEY_{i}", None)
            keys = _load_api_keys()
        assert keys == ["numbered-key"]

    def test_up_to_seven_keys_supported(self):
        env = {f"GROQ_API_KEY_{i}": f"key-{i}" for i in range(1, 8)}
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("GROQ_API_KEY", None)
            keys = _load_api_keys()
        assert len(keys) == 7
        assert keys[0] == "key-1"
        assert keys[6] == "key-7"

    def test_no_keys_raises_on_pool_init(self):
        with patch.dict(os.environ, {}, clear=False):
            for i in range(1, 8):
                os.environ.pop(f"GROQ_API_KEY_{i}", None)
            os.environ.pop("GROQ_API_KEY", None)
            with pytest.raises(RuntimeError, match="No Groq API keys"):
                GroqClientPool()

    def test_pool_key_count_matches_loaded_keys(self):
        pool = _pool_with_keys("k1", "k2", "k3")
        assert pool.key_count == 3


# ---------------------------------------------------------------------------
# 2. Successful requests — no rotation
# ---------------------------------------------------------------------------

class TestSuccessNoRotation:
    def test_first_key_succeeds_no_rotation(self):
        pool = _pool_with_keys("k1", "k2")
        success = _make_success_response("hello")

        call_count = [0]

        async def fake_create(**kwargs):
            call_count[0] += 1
            return success

        for client in pool._clients:
            client.chat = SimpleNamespace(
                completions=SimpleNamespace(create=AsyncMock(side_effect=fake_create))
            )

        result = asyncio.run(pool.chat_completions_create(model="m", messages=[]))
        assert result.choices[0].message.content == "hello"
        assert call_count[0] == 1

    def test_chat_namespace_shim_works(self):
        pool = _pool_with_keys("k1")
        success = _make_success_response("shim-ok")
        pool._clients[0].chat = SimpleNamespace(
            completions=SimpleNamespace(create=AsyncMock(return_value=success))
        )
        result = asyncio.run(pool.chat.completions.create(model="m", messages=[]))
        assert result.choices[0].message.content == "shim-ok"


# ---------------------------------------------------------------------------
# 3. Rate-limit rotation
# ---------------------------------------------------------------------------

class TestRateLimitRotation:
    def test_first_key_429_uses_second_key(self):
        pool = _pool_with_keys("k1", "k2")
        rate_err = _make_rate_limit_error()
        success = _make_success_response("from-key-2")

        pool._clients[0].chat = SimpleNamespace(
            completions=SimpleNamespace(create=AsyncMock(side_effect=rate_err))
        )
        pool._clients[1].chat = SimpleNamespace(
            completions=SimpleNamespace(create=AsyncMock(return_value=success))
        )

        result = asyncio.run(pool.chat_completions_create(model="m", messages=[]))
        assert result.choices[0].message.content == "from-key-2"

    def test_first_and_second_429_uses_third_key(self):
        pool = _pool_with_keys("k1", "k2", "k3")
        rate_err = _make_rate_limit_error()
        success = _make_success_response("from-key-3")

        pool._clients[0].chat = SimpleNamespace(
            completions=SimpleNamespace(create=AsyncMock(side_effect=rate_err))
        )
        pool._clients[1].chat = SimpleNamespace(
            completions=SimpleNamespace(create=AsyncMock(side_effect=rate_err))
        )
        pool._clients[2].chat = SimpleNamespace(
            completions=SimpleNamespace(create=AsyncMock(return_value=success))
        )

        result = asyncio.run(pool.chat_completions_create(model="m", messages=[]))
        assert result.choices[0].message.content == "from-key-3"

    def test_rotation_continues_until_success(self):
        """Keys 1-5 rate-limited, key 6 succeeds."""
        pool = _pool_with_keys("k1", "k2", "k3", "k4", "k5", "k6")
        rate_err = _make_rate_limit_error()
        success = _make_success_response("from-key-6")

        for i in range(5):
            pool._clients[i].chat = SimpleNamespace(
                completions=SimpleNamespace(create=AsyncMock(side_effect=rate_err))
            )
        pool._clients[5].chat = SimpleNamespace(
            completions=SimpleNamespace(create=AsyncMock(return_value=success))
        )

        result = asyncio.run(pool.chat_completions_create(model="m", messages=[]))
        assert result.choices[0].message.content == "from-key-6"

    def test_all_keys_rate_limited_raises_all_keys_error(self):
        pool = _pool_with_keys("k1", "k2", "k3")
        rate_err = _make_rate_limit_error()

        for client in pool._clients:
            client.chat = SimpleNamespace(
                completions=SimpleNamespace(create=AsyncMock(side_effect=rate_err))
            )

        with pytest.raises(AllKeysRateLimitedError):
            asyncio.run(pool.chat_completions_create(model="m", messages=[]))

    def test_all_keys_rate_limited_error_message_safe(self):
        """Error message must not contain any API key value."""
        pool = _pool_with_keys("super-secret-key-abc123", "another-secret-xyz")
        rate_err = _make_rate_limit_error()

        for client in pool._clients:
            client.chat = SimpleNamespace(
                completions=SimpleNamespace(create=AsyncMock(side_effect=rate_err))
            )

        with pytest.raises(AllKeysRateLimitedError) as exc_info:
            asyncio.run(pool.chat_completions_create(model="m", messages=[]))

        error_msg = str(exc_info.value)
        assert "super-secret-key-abc123" not in error_msg
        assert "another-secret-xyz" not in error_msg


# ---------------------------------------------------------------------------
# 4. Non-429 errors do NOT cause rotation
# ---------------------------------------------------------------------------

class TestNonRateLimitErrors:
    def test_auth_error_not_rotated(self):
        pool = _pool_with_keys("k1", "k2")
        auth_error = Exception("Invalid API key")

        call_count = [0]

        async def fake_create(**kwargs):
            call_count[0] += 1
            raise auth_error

        pool._clients[0].chat = SimpleNamespace(
            completions=SimpleNamespace(create=AsyncMock(side_effect=fake_create))
        )
        pool._clients[1].chat = SimpleNamespace(
            completions=SimpleNamespace(create=AsyncMock(return_value=_make_success_response()))
        )

        with pytest.raises(Exception, match="Invalid API key"):
            asyncio.run(pool.chat_completions_create(model="m", messages=[]))

        # Second client must NOT have been called
        assert call_count[0] == 1
        pool._clients[1].chat.completions.create.assert_not_called()

    def test_json_parse_error_not_rotated(self):
        pool = _pool_with_keys("k1", "k2")
        json_error = ValueError("JSON decode error")

        pool._clients[0].chat = SimpleNamespace(
            completions=SimpleNamespace(create=AsyncMock(side_effect=json_error))
        )
        pool._clients[1].chat = SimpleNamespace(
            completions=SimpleNamespace(create=AsyncMock(return_value=_make_success_response()))
        )

        with pytest.raises(ValueError, match="JSON decode error"):
            asyncio.run(pool.chat_completions_create(model="m", messages=[]))

        pool._clients[1].chat.completions.create.assert_not_called()

    def test_network_error_not_rotated(self):
        pool = _pool_with_keys("k1", "k2")
        net_error = ConnectionError("network failure")

        pool._clients[0].chat = SimpleNamespace(
            completions=SimpleNamespace(create=AsyncMock(side_effect=net_error))
        )
        pool._clients[1].chat = SimpleNamespace(
            completions=SimpleNamespace(create=AsyncMock(return_value=_make_success_response()))
        )

        with pytest.raises(ConnectionError):
            asyncio.run(pool.chat_completions_create(model="m", messages=[]))

        pool._clients[1].chat.completions.create.assert_not_called()


# ---------------------------------------------------------------------------
# 5. Cooldown / key recovery
# ---------------------------------------------------------------------------

class TestCooldownRecovery:
    def test_rate_limited_key_becomes_available_after_cooldown(self):
        pool = _pool_with_keys("k1")
        # Mark key as rate-limited with a very short cooldown
        pool.mark_key_rate_limited(0, cooldown=0.05)
        assert not pool.is_key_available(0)

        time.sleep(0.1)
        assert pool.is_key_available(0)

    def test_retry_after_header_respected(self):
        pool = _pool_with_keys("k1", "k2")
        rate_err = _make_rate_limit_error_with_retry_after(120)
        success = _make_success_response("ok")

        pool._clients[0].chat = SimpleNamespace(
            completions=SimpleNamespace(create=AsyncMock(side_effect=rate_err))
        )
        pool._clients[1].chat = SimpleNamespace(
            completions=SimpleNamespace(create=AsyncMock(return_value=success))
        )

        asyncio.run(pool.chat_completions_create(model="m", messages=[]))

        # Key 0 should be cooled down for ~120s
        assert not pool.is_key_available(0)
        remaining = pool._available_after[0] - time.monotonic()
        assert remaining > 60  # well above 0

    def test_reset_key_makes_it_immediately_available(self):
        pool = _pool_with_keys("k1")
        pool.mark_key_rate_limited(0, cooldown=9999)
        assert not pool.is_key_available(0)
        pool.reset_key(0)
        assert pool.is_key_available(0)

    def test_previously_rate_limited_key_used_again_after_cooldown(self):
        pool = _pool_with_keys("k1", "k2")
        rate_err = _make_rate_limit_error()
        success = _make_success_response("recovered")

        # First call: key 0 rate-limited, key 1 succeeds
        pool._clients[0].chat = SimpleNamespace(
            completions=SimpleNamespace(create=AsyncMock(side_effect=rate_err))
        )
        pool._clients[1].chat = SimpleNamespace(
            completions=SimpleNamespace(create=AsyncMock(return_value=success))
        )
        asyncio.run(pool.chat_completions_create(model="m", messages=[]))

        # Manually expire the cooldown on key 0
        pool.reset_key(0)

        # Second call: key 0 is available again and should be tried first
        pool._clients[0].chat = SimpleNamespace(
            completions=SimpleNamespace(create=AsyncMock(return_value=_make_success_response("key-0-again")))
        )
        result = asyncio.run(pool.chat_completions_create(model="m", messages=[]))
        assert result.choices[0].message.content == "key-0-again"


# ---------------------------------------------------------------------------
# 6. Logging safety — keys never logged
# ---------------------------------------------------------------------------

class TestLoggingSafety:
    def test_key_not_in_warning_log(self, caplog):
        import logging
        pool = _pool_with_keys("VERY_SECRET_KEY_12345")
        rate_err = _make_rate_limit_error()
        success = _make_success_response()

        pool._clients[0].chat = SimpleNamespace(
            completions=SimpleNamespace(create=AsyncMock(side_effect=rate_err))
        )
        # Only one key — will raise AllKeysRateLimitedError
        with caplog.at_level(logging.WARNING, logger="groq_client"):
            with pytest.raises(AllKeysRateLimitedError):
                asyncio.run(pool.chat_completions_create(model="m", messages=[]))

        for record in caplog.records:
            assert "VERY_SECRET_KEY_12345" not in record.getMessage()

    def test_error_message_contains_key_count_not_key_value(self):
        pool = _pool_with_keys("secret-key-abc")
        rate_err = _make_rate_limit_error()
        pool._clients[0].chat = SimpleNamespace(
            completions=SimpleNamespace(create=AsyncMock(side_effect=rate_err))
        )

        with pytest.raises(AllKeysRateLimitedError) as exc_info:
            asyncio.run(pool.chat_completions_create(model="m", messages=[]))

        msg = str(exc_info.value)
        assert "secret-key-abc" not in msg
        assert "1" in msg  # key count is safe to include


# ---------------------------------------------------------------------------
# 7. Concurrency safety
# ---------------------------------------------------------------------------

class TestConcurrencySafety:
    def test_concurrent_requests_do_not_corrupt_key_state(self):
        """Multiple concurrent requests must not cause race conditions."""
        pool = _pool_with_keys("k1", "k2", "k3")
        success = _make_success_response("ok")

        for client in pool._clients:
            client.chat = SimpleNamespace(
                completions=SimpleNamespace(create=AsyncMock(return_value=success))
            )

        async def run_many():
            tasks = [
                pool.chat_completions_create(model="m", messages=[])
                for _ in range(20)
            ]
            results = await asyncio.gather(*tasks)
            return results

        results = asyncio.run(run_many())
        assert len(results) == 20
        assert all(r.choices[0].message.content == "ok" for r in results)

    def test_concurrent_rate_limits_handled_safely(self):
        """Concurrent 429s on key 0 must not double-count or corrupt state."""
        pool = _pool_with_keys("k1", "k2")
        rate_err = _make_rate_limit_error()
        success = _make_success_response("ok")

        pool._clients[0].chat = SimpleNamespace(
            completions=SimpleNamespace(create=AsyncMock(side_effect=rate_err))
        )
        pool._clients[1].chat = SimpleNamespace(
            completions=SimpleNamespace(create=AsyncMock(return_value=success))
        )

        async def run_concurrent():
            tasks = [
                pool.chat_completions_create(model="m", messages=[])
                for _ in range(5)
            ]
            return await asyncio.gather(*tasks)

        results = asyncio.run(run_concurrent())
        assert all(r.choices[0].message.content == "ok" for r in results)
        # Key 0 must be cooled down
        assert not pool.is_key_available(0)


# ---------------------------------------------------------------------------
# 8. server.py integration — _parse_resume_with_llm
# ---------------------------------------------------------------------------

class TestServerIntegration:
    def test_parse_resume_returns_429_when_all_keys_exhausted(self):
        """_parse_resume_with_llm must raise HTTPException(429) on AllKeysRateLimitedError."""
        import server
        from fastapi import HTTPException

        all_keys_err = AllKeysRateLimitedError("all keys rate limited")

        with patch.object(
            server.openai_client,
            "chat_completions_create",
            new=AsyncMock(side_effect=all_keys_err),
        ):
            with pytest.raises(HTTPException) as exc_info:
                asyncio.run(server._parse_resume_with_llm("some resume text"))

        assert exc_info.value.status_code == 429
        assert "temporarily unavailable" in exc_info.value.detail.lower() or \
               "rate" in exc_info.value.detail.lower()

    def test_parse_resume_returns_429_on_single_key_rate_limit(self):
        """Existing behaviour: OpenAIRateLimitError still raises HTTPException(429)."""
        import server
        from fastapi import HTTPException

        fake_response = MagicMock()
        fake_response.status_code = 429
        fake_response.headers = {}
        fake_response.text = "rate limited"
        rate_err = OpenAIRateLimitError("rate limited", response=fake_response, body=None)

        with patch.object(
            server.openai_client,
            "chat_completions_create",
            new=AsyncMock(side_effect=rate_err),
        ):
            with pytest.raises(HTTPException) as exc_info:
                asyncio.run(server._parse_resume_with_llm("some resume text"))

        assert exc_info.value.status_code == 429

    def test_server_uses_groq_client_pool(self):
        """server.openai_client must be a GroqClientPool instance."""
        import server
        assert isinstance(server.openai_client, GroqClientPool)

    def test_api_key_not_exposed_in_parse_resume_error(self):
        """The 429 error detail must not contain any API key value."""
        import server
        from fastapi import HTTPException

        all_keys_err = AllKeysRateLimitedError("all keys rate limited")

        with patch.object(
            server.openai_client,
            "chat_completions_create",
            new=AsyncMock(side_effect=all_keys_err),
        ):
            with pytest.raises(HTTPException) as exc_info:
                asyncio.run(server._parse_resume_with_llm("some resume text"))

        # The detail must not contain any raw key material
        detail = exc_info.value.detail
        for key in (os.environ.get(f"GROQ_API_KEY_{i}", "") for i in range(1, 8)):
            if key:
                assert key not in detail
