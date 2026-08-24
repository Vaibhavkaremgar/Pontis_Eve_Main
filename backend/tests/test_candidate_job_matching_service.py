import asyncio
import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import candidate_job_matching_service as matcher  # noqa: E402


class FixedDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2024, 1, 1, tzinfo=tz or timezone.utc)


class FakeResult:
    def __init__(self, rows=None, scalar_value=None):
        self._rows = rows or []
        self._scalar_value = scalar_value

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def scalar(self):
        return self._scalar_value


class FakeSession:
    def __init__(self, state):
        self.state = state

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, statement, params=None):
        query = str(statement)
        params = params or {}

        if "FROM job_descriptions" in query:
            job_ids = [params[key] for key in sorted(params) if key.startswith("jid_")]
            rows = []
            for job_id in job_ids:
                job = self.state["jobs"].get(job_id)
                if job:
                    rows.append(
                        (
                            job_id,
                            job["title"],
                            job["description"],
                            job.get("requirements", ""),
                            job.get("skills", []),
                        )
                    )
            return FakeResult(rows)

        if "FROM candidate_job_recommendations" in query:
            rows = self.state.get("existing", [])
            return FakeResult(rows)

        if "SET hidden_at" in query:
            self.state.setdefault("hidden_updates", []).append(list(params.get("job_ids") or []))
            return FakeResult([])

        if "UPDATE candidate_job_recommendations" in query and "SET match_score" in query:
            self.state.setdefault("updated", []).append(params["rid"])
            self.state.setdefault("upserted", []).append(params["rid"])
            return FakeResult([])

        if "INSERT INTO candidate_job_recommendations" in query:
            self.state.setdefault("inserted", []).append(params["jid"])
            self.state.setdefault("match_reasons", {})[params["jid"]] = params["match_reason"]
            return FakeResult([])

        raise AssertionError(f"Unexpected query: {query}")

    async def commit(self):
        self.state["commits"] = self.state.get("commits", 0) + 1


class FakeSessionFactory:
    def __init__(self, state):
        self.state = state

    def __call__(self):
        return FakeSession(self.state)


@pytest.fixture(autouse=True)
def fixed_datetime(monkeypatch):
    monkeypatch.setattr(matcher, "datetime", FixedDateTime)
    monkeypatch.setattr(matcher, "timezone", timezone)


def test_candidate_total_experience_years_merges_overlaps_and_counts_present():
    candidate = {
        "work_experience": [
            {"start_date": "2020-01-01", "end_date": "2021-01-01"},
            {"start_date": "2020-06-01", "end_date": "2022-01-01"},
            {"start_date": "2022-01-01", "end_date": "Present"},
        ]
    }

    years = matcher._candidate_total_experience_years(candidate)

    assert years == pytest.approx(4.0, rel=0.01)


def test_job_eligibility_filters_experience_and_requires_relevance():
    signals = {
        "skills": ["Java", "Spring Boot"],
        "target_roles": ["Java Backend Engineer"],
    }

    assert matcher._job_is_eligible(
        signals,
        3.0,
        "Java Backend Engineer",
        "Build APIs with Java and Spring Boot",
        "2-3 years of experience",
        ["Java", "Spring Boot"],
    )

    assert not matcher._job_is_eligible(
        signals,
        3.0,
        "Senior Python Engineer",
        "Build ML systems with Python",
        "4+ years of experience",
        ["Python", "ML"],
    )

    assert not matcher._job_is_eligible(
        signals,
        3.0,
        "Content Writer",
        "Create blog content and edit copy",
        "4+ years of experience",
        ["SEO", "Writing"],
    )

