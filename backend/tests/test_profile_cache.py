import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import profile_cache


@pytest.mark.asyncio
async def test_cache_disabled_without_redis_url(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    profile_cache._redis_attempted = False
    profile_cache._redis_client = None
    assert await profile_cache.get("candidate-1") is None


@pytest.mark.asyncio
async def test_cache_round_trip(monkeypatch):
    class FakeRedis:
        def __init__(self):
            self.data = {}
        async def ping(self):
            return True
        async def get(self, key):
            return self.data.get(key)
        async def set(self, key, value, ex):
            self.data[key] = value
        async def delete(self, key):
            self.data.pop(key, None)

    fake = FakeRedis()
    monkeypatch.setenv("REDIS_URL", "redis://test")
    profile_cache._redis_client = fake
    profile_cache._redis_attempted = True
    profile = {"id": "candidate-1", "skills": ["Python"]}
    await profile_cache.set("candidate-1", profile)
    assert await profile_cache.get("candidate-1") == profile
    await profile_cache.invalidate("candidate-1")
    assert await profile_cache.get("candidate-1") is None
