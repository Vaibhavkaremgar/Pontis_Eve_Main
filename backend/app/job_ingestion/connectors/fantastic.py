"""Async, budget-conscious client for Fantastic.jobs' global active ATS feed."""
import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)
BASE_URL = "https://data.fantastic.jobs/v1"


class FantasticAPIError(RuntimeError):
    """A Fantastic response that should stop the current conservative sync."""


@dataclass(frozen=True)
class FantasticConfig:
    api_key: str
    time_frame: str = "24h"
    limit: int = 25
    max_pages: int = 1
    max_jobs_per_run: int = 25
    max_requests_per_run: int = 1
    timeout: float = 20.0

    @classmethod
    def from_env(cls) -> "FantasticConfig":
        def integer(name: str, default: int, minimum: int = 1) -> int:
            try:
                return max(minimum, int(os.getenv(name, default)))
            except ValueError:
                return default
        try:
            timeout = max(1.0, float(os.getenv("FANTASTIC_TIMEOUT", "20")))
        except ValueError:
            timeout = 20.0
        return cls(
            api_key=os.getenv("FANTASTIC_JOBS_API_KEY", "").strip(),
            time_frame=os.getenv("FANTASTIC_TIME_FRAME", "24h").strip() or "24h",
            limit=integer("FANTASTIC_LIMIT", 25),
            max_pages=integer("FANTASTIC_MAX_PAGES", 1),
            max_jobs_per_run=integer("FANTASTIC_MAX_JOBS_PER_RUN", 25),
            max_requests_per_run=integer("FANTASTIC_MAX_REQUESTS_PER_RUN", 1),
            timeout=timeout,
        )


def _jobs_from_payload(payload: Any) -> list[dict[str, Any]]:
    """Accept documented collection envelopes without accepting malformed jobs."""
    if isinstance(payload, list):
        jobs = payload
    elif isinstance(payload, dict):
        jobs = payload.get("jobs", payload.get("data", payload.get("results", [])))
    else:
        return []
    return [job for job in jobs if isinstance(job, dict)] if isinstance(jobs, list) else []


class FantasticClient:
    def __init__(self, config: FantasticConfig | None = None, client: httpx.AsyncClient | None = None):
        self.config = config or FantasticConfig.from_env()
        self.client = client

    async def fetch_active_ats(self) -> list[dict[str, Any]]:
        if not self.config.api_key:
            raise FantasticAPIError("FANTASTIC_JOBS_API_KEY is not configured")
        owned_client = self.client is None
        client = self.client or httpx.AsyncClient(timeout=self.config.timeout)
        jobs: list[dict[str, Any]] = []
        try:
            for page in range(min(self.config.max_pages, self.config.max_requests_per_run)):
                remaining = self.config.max_jobs_per_run - len(jobs)
                if remaining <= 0:
                    break
                params = {"time_frame": self.config.time_frame, "limit": min(self.config.limit, remaining),
                          "offset": page * self.config.limit, "description_format": "text"}
                logger.info("[fantastic] requesting active-ats page=%d request=%d", page + 1, page + 1)
                try:
                    response = await client.get(f"{BASE_URL}/active-ats", params=params,
                                                headers={"Authorization": f"Bearer {self.config.api_key}"})
                except httpx.TimeoutException as exc:
                    raise FantasticAPIError("Fantastic request timed out") from exc
                except httpx.RequestError as exc:
                    raise FantasticAPIError(f"Fantastic request failed: {exc}") from exc
                headers = {key: value for key, value in response.headers.items()
                           if key.lower().startswith(("x-api-", "x-rate", "rate-limit", "retry-after"))}
                logger.info("[fantastic] response status=%d headers=%s", response.status_code, headers)
                if response.status_code == 429:
                    # A single bounded retry respects Retry-After without burning request credits.
                    retry_after = response.headers.get("Retry-After")
                    try: delay = min(float(retry_after or 0), 30.0)
                    except ValueError: delay = 0
                    if delay and page == 0:
                        await asyncio.sleep(delay)
                        response = await client.get(f"{BASE_URL}/active-ats", params=params,
                                                    headers={"Authorization": f"Bearer {self.config.api_key}"})
                    if response.status_code == 429:
                        raise FantasticAPIError("Fantastic rate limit reached")
                if response.status_code in {401, 403}:
                    raise FantasticAPIError(f"Fantastic authorization failed (HTTP {response.status_code})")
                if response.status_code >= 500:
                    raise FantasticAPIError(f"Fantastic server error (HTTP {response.status_code})")
                try:
                    response.raise_for_status(); batch = _jobs_from_payload(response.json())
                except (httpx.HTTPStatusError, ValueError) as exc:
                    raise FantasticAPIError("Fantastic returned a malformed response") from exc
                jobs.extend(batch)
                logger.info("[fantastic] fetched %d jobs (total=%d)", len(batch), len(jobs))
                if len(batch) < params["limit"]:
                    break
            return jobs[:self.config.max_jobs_per_run]
        finally:
            if owned_client:
                await client.aclose()
