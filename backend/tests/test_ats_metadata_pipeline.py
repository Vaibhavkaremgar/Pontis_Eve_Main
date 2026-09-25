"""Regression coverage for public ATS payload -> normalized job -> API fields."""
import asyncio
import os
from datetime import date, datetime, timezone

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://unused:unused@localhost/unused")

from app.job_ingestion.normalize import normalize_ashby, normalize_greenhouse, normalize_lever, normalize_workable, parse_ats_datetime
from app.job_ingestion.job_ingestion_service import _metadata_params, upsert_ats_job
import server


def test_representative_public_ats_payloads_preserve_explicit_metadata():
    ashby = normalize_ashby({"id": "a", "title": "Engineer", "descriptionHtml": "<p>Experience Level: Senior</p><p>Required Skills: Python, SQL</p>", "department": {"name": "Engineering"}, "location": "Remote", "employmentType": "Full-time", "isRemote": True, "publishedAt": "2026-01-02T12:00:00Z", "compensation": {"currency": "USD", "minValue": 120000, "maxValue": 160000, "interval": "year"}, "jobUrl": "https://jobs.example/a"}, "Acme")
    lever = normalize_lever({"id": "l", "text": "Developer", "descriptionPlain": "Experience Required: 5 years", "categories": {"team": "Platform", "location": "Bengaluru", "commitment": "Full-time", "allLocations": ["Bengaluru", "Remote"]}, "workplaceType": "hybrid", "createdAt": 1760000000000, "hostedUrl": "https://jobs.example/l"}, "Acme")
    greenhouse = normalize_greenhouse({"id": 1, "title": "Analyst", "content": "<p>Remote Policy: Remote</p><p>Salary Range: $90k-$110k</p>", "departments": [{"name": "Data"}], "location": {"name": "New York"}, "updated_at": "2026-01-03T12:00:00Z", "absolute_url": "https://jobs.example/g"}, "Acme")
    workable = normalize_workable({"id": "w", "title": "Designer", "description": "Skills Required: Figma, CSS", "department": "Design", "employment_type": "contract", "location": {"city": "London", "telecommuting": True}, "published_on": "2026-01-04T12:00:00Z", "salary_range": "£50k-£70k", "url": "https://jobs.example/w"}, "Acme")
    assert ashby["salary_range"] == "USD 120000 - 160000 year" and ashby["skills_required"] == ["Python", "SQL"]
    assert lever["employment_type"] == "Full-time" and lever["remote_policy"] == "hybrid"
    assert greenhouse["remote_policy"] == "Remote" and greenhouse["salary_range"] == "$90k-$110k"
    assert workable["remote_policy"] == "Remote" and workable["skills_required"] == ["Figma", "CSS"]


def test_ats_dates_are_bind_safe_datetimes_and_preserve_offsets():
    jobs = [
        normalize_ashby({"id": "a", "title": "A", "publishedAt": "2026-07-16T22:10:27.434Z"}, "Acme"),
        normalize_lever({"id": "l", "text": "L", "createdAt": 1784248827434}, "Acme"),
        normalize_greenhouse({"id": "g", "title": "G", "created_at": "2026-07-16T22:10:27.434000+05:30"}, "Acme"),
        normalize_workable({"id": "w", "title": "W", "published_on": "2026-07-16"}, "Acme"),
    ]
    for job in jobs:
        assert isinstance(job["created_at"], datetime)
        assert job["created_at"].tzinfo is not None
        assert isinstance(_metadata_params(job)["created_at"], datetime)
    assert jobs[2]["created_at"].utcoffset().total_seconds() == 19800
    assert jobs[3]["created_at"] == datetime(2026, 7, 16, tzinfo=timezone.utc)


def test_legacy_normalized_iso_date_is_coerced_before_database_binding():
    params = _metadata_params({"created_at": "2026-07-16T22:10:27.434000+00:00"})
    assert params["created_at"] == datetime(2026, 7, 16, 22, 10, 27, 434000, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-07-16T22:10:27.434000+00:00", datetime(2026, 7, 16, 22, 10, 27, 434000, tzinfo=timezone.utc)),
        ("2026-07-16T22:10:27.434000-04:00", datetime.fromisoformat("2026-07-16T22:10:27.434000-04:00")),
        ("2026-07-16T22:10:27.434Z", datetime(2026, 7, 16, 22, 10, 27, 434000, tzinfo=timezone.utc)),
        ("2026-07-16", datetime(2026, 7, 16, tzinfo=timezone.utc)),
        (date(2026, 7, 16), datetime(2026, 7, 16, tzinfo=timezone.utc)),
        (datetime(2026, 7, 16, 12, tzinfo=timezone.utc), datetime(2026, 7, 16, 12, tzinfo=timezone.utc)),
        ("not-a-timestamp", None),
        (None, None),
    ],
)
def test_ats_timestamp_parser_always_returns_a_bind_safe_datetime_or_none(value, expected):
    parsed = parse_ats_datetime(value)
    assert parsed == expected
    assert parsed is None or isinstance(parsed, datetime)
    assert _metadata_params({"created_at": value})["created_at"] == expected


@pytest.mark.parametrize(
    ("normalizer", "payload"),
    [
        (normalize_ashby, {"id": "a", "title": "A", "publishedAt": "not-a-timestamp"}),
        (normalize_lever, {"id": "l", "text": "L", "createdAt": "not-a-timestamp"}),
        (normalize_greenhouse, {"id": "g", "title": "G", "created_at": "not-a-timestamp"}),
        (normalize_workable, {"id": "w", "title": "W", "published_on": "not-a-timestamp"}),
    ],
)
def test_each_ats_source_drops_invalid_timestamps_before_persistence(normalizer, payload):
    job = normalizer(payload, "Acme")
    assert job["created_at"] is None
    assert _metadata_params(job)["created_at"] is None


class _Result:
    def __init__(self, rows=()): self.rows = list(rows)
    def first(self): return self.rows[0] if self.rows else None

class _Session:
    def __init__(self): self.params = None
    async def execute(self, statement, params=None):
        if "SELECT id, job_url, description" in str(statement): return _Result([("db-job", None, "old")])
        self.params = params; return _Result()
    async def commit(self): pass

def test_existing_sync_writes_normalized_metadata(monkeypatch):
    job = normalize_ashby({"id": "a", "title": "Engineer", "descriptionHtml": "Required Skills: Python, SQL", "employmentType": "Full-time", "isRemote": True, "jobUrl": "https://jobs.example/a"}, "Acme")
    job["job_url"] = None  # Keep this storage-unit test independent of Qdrant.
    session = _Session()
    monkeypatch.setattr("app.job_ingestion.job_ingestion_service._valid_http_url", lambda value: value)
    asyncio.run(upsert_ats_job(session, job))
    assert session.params["employment_type"] == "Full-time"
    assert session.params["remote_policy"] == "Remote"
    assert session.params["skills_required"] == '["Python", "SQL"]'


def test_candidate_jobs_serializes_normalized_metadata_without_changing_matching(monkeypatch):
    row = {"rec_id": "r", "job_id": "j", "match_score": 0.8, "recommendation_rank": 1, "match_reason": None, "tracked_at": None, "applied_at": None, "hidden_at": None, "viewed_at": None, "application_status": None, "application_agency_id": None, "application_job_role": None, "title": "Engineer", "company_name": "Acme", "location": "Remote", "salary_range": "$100k-$120k", "employment_type": "Full-time", "remote_policy": "Remote", "experience_level": "Senior", "experience_required": None, "created_at": "2026-01-01T00:00:00+00:00", "skills_required": ["Python"], "description": "", "requirements": "", "skills": [], "company_logo_url": None, "job_url": "https://jobs.example/a"}
    class Result:
        def __init__(self, scalar=None): self.scalar_value = scalar
        def scalar(self): return self.scalar_value
        def mappings(self): return self
        def fetchall(self): return [row]
    class Session:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def execute(self, statement, params=None): return Result(1) if "COUNT(*)" in str(statement) else Result()
    async def candidate(_): return {"subscription_active": True}
    async def strength(*_): return 90
    monkeypatch.setattr(server, "_get_candidate_row", candidate); monkeypatch.setattr(server, "_effective_profile_strength_percent", strength); monkeypatch.setattr(server, "SessionLocal", lambda: Session())
    response = asyncio.run(server.get_candidate_jobs("c"))
    assert response[0]["employment_type"] == "Full-time" and response[0]["skills_required"] == ["Python"] and response[0]["posted_at"] == "2026-01-01T00:00:00+00:00"
