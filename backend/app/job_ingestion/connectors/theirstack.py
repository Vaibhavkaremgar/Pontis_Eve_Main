"""Bounded TheirStack jobs API client."""
import logging, os
from dataclasses import dataclass
from typing import Any
import httpx
logger = logging.getLogger(__name__)
BASE_URL = "https://api.theirstack.com"
class TheirStackAPIError(RuntimeError): pass
@dataclass(frozen=True)
class TheirStackConfig:
    api_key: str; limit: int = 25; max_pages: int = 1; max_jobs_per_run: int = 25; timeout: float = 20.0
    @classmethod
    def from_env(cls):
        def integer(name, default):
            try: return max(1, int(os.getenv(name, default)))
            except (TypeError, ValueError): return default
        try: timeout = max(1.0, float(os.getenv("THEIRSTACK_TIMEOUT", "20")))
        except ValueError: timeout = 20.0
        return cls(os.getenv("THEIRSTACK_API_KEY", "").strip(), integer("THEIRSTACK_LIMIT", 25), integer("THEIRSTACK_MAX_PAGES", 1), integer("THEIRSTACK_MAX_JOBS_PER_RUN", 25), timeout)
class TheirStackClient:
    def __init__(self, config=None, client=None): self.config = config or TheirStackConfig.from_env(); self.client = client
    async def fetch_jobs(self) -> list[dict[str, Any]]:
        if not self.config.api_key: raise TheirStackAPIError("THEIRSTACK_API_KEY is not configured")
        owned = self.client is None; client = self.client or httpx.AsyncClient(timeout=self.config.timeout); jobs = []
        try:
            for page in range(self.config.max_pages):
                remaining = self.config.max_jobs_per_run - len(jobs)
                if remaining <= 0: break
                payload = {"limit": min(self.config.limit, remaining), "page": page, "posted_at_max_age_days": 30, "job_country_code_or": ["IN"]}
                try: response = await client.post(f"{BASE_URL}/v1/jobs/search", json=payload, headers={"Authorization": f"Bearer {self.config.api_key}", "Accept": "application/json", "Content-Type": "application/json"})
                except httpx.TimeoutException as exc: raise TheirStackAPIError("TheirStack request timed out") from exc
                except httpx.RequestError as exc: raise TheirStackAPIError("TheirStack request failed") from exc
                if response.status_code == 429: raise TheirStackAPIError("TheirStack rate limit reached")
                if response.status_code in (401, 403): raise TheirStackAPIError(f"TheirStack authorization failed (HTTP {response.status_code})")
                if response.status_code >= 500: raise TheirStackAPIError(f"TheirStack server error (HTTP {response.status_code})")
                try: response.raise_for_status(); data = response.json()
                except (httpx.HTTPStatusError, ValueError) as exc: raise TheirStackAPIError("TheirStack returned a malformed response") from exc
                batch = data.get("jobs", data.get("data", [])) if isinstance(data, dict) else []
                if not isinstance(batch, list): batch = []
                jobs.extend(x for x in batch if isinstance(x, dict))
                if len(batch) < payload["limit"]: break
            return jobs[:self.config.max_jobs_per_run]
        finally:
            if owned: await client.aclose()
