"""Best-effort Redis cache for candidate rows.

PostgreSQL remains authoritative.  Every operation deliberately fails open so
the cache can never make profile functionality unavailable.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

_redis_client = None
_redis_attempted = False
_CACHE_PREFIX = os.getenv("PROFILE_CACHE_PREFIX", "candidate_profile")
_CACHE_TTL = int(os.getenv("PROFILE_CACHE_TTL_SECONDS", "300"))


def _key(candidate_id: str) -> str:
    return f"{_CACHE_PREFIX}:v1:{candidate_id}"


async def _client():
    global _redis_client, _redis_attempted
    if _redis_attempted:
        return _redis_client
    _redis_attempted = True
    url = os.getenv("REDIS_URL", "").strip()
    if not url:
        logger.info("[profile-cache] disabled: REDIS_URL is not configured")
        return None
    try:
        from redis.asyncio import Redis
        _redis_client = Redis.from_url(url, decode_responses=True,
                                       socket_connect_timeout=1,
                                       socket_timeout=1)
        await _redis_client.ping()
        logger.info("[profile-cache] Redis connection established")
        return _redis_client
    except Exception as exc:
        logger.warning("[profile-cache] Redis unavailable; using PostgreSQL: %s", exc)
        _redis_client = None
        return None


async def get(candidate_id: str) -> Optional[dict[str, Any]]:
    try:
        client = await _client()
        if not client:
            return None
        value = await client.get(_key(candidate_id))
        if not value:
            logger.info("[profile-cache] MISS candidate=%s", candidate_id)
            return None
        data = json.loads(value)
        if isinstance(data, dict):
            logger.info("[profile-cache] HIT candidate=%s", candidate_id)
            return data
        logger.warning("[profile-cache] INVALID candidate=%s; treating as miss", candidate_id)
        return None
    except Exception as exc:
        logger.warning("[profile-cache] read failed for %s: %s", candidate_id, exc)
        return None


async def set(candidate_id: str, profile: dict[str, Any]) -> None:
    try:
        client = await _client()
        if client:
            await client.set(_key(candidate_id), json.dumps(profile, default=str), ex=_CACHE_TTL)
            logger.info("[profile-cache] SET candidate=%s ttl=%ss", candidate_id, _CACHE_TTL)
    except Exception as exc:
        logger.warning("[profile-cache] write failed for %s: %s", candidate_id, exc)


async def invalidate(candidate_id: str) -> None:
    try:
        client = await _client()
        if client:
            await client.delete(_key(candidate_id))
            logger.info("[profile-cache] INVALIDATE candidate=%s", candidate_id)
    except Exception as exc:
        logger.warning("[profile-cache] invalidation failed for %s: %s", candidate_id, exc)

