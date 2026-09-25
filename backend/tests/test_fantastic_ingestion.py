import asyncio
from datetime import datetime, timezone

import httpx
import pytest

from app.job_ingestion.connectors.fantastic import FantasticAPIError, FantasticClient, FantasticConfig
from app.job_ingestion.normalize import normalize_fantastic
from app.job_ingestion.job_ingestion_service import _metadata_params, upsert_ats_job
from app.job_ingestion.scheduler import _log_fantastic_upsert_error


def _record(**overrides):
    record = {"id": "fj-1", "organization": "Acme", "title": "Backend Engineer",
              "description_text": "Build APIs", "url": "https://jobs.example/fj-1",
              "locations_derived": ["Bengaluru", "India"], "date_posted": "2020-07-16T22:10:27+05:30", "date_created": "2026-07-16T22:10:27+05:30",
              "ai_employment_type": "Full-time", "ai_experience_level": "Senior",
              "ai_requirements_summary": "5 years", "ai_work_arrangement": "Hybrid",
              "ai_key_skills": ["Python", "FastAPI"], "salary_range": "₹20L-₹30L",
              "source": "greenhouse", "source_type": "ats"}
    record.update(overrides); return record


def test_normalization_preserves_fields_and_binds_datetime():
    job = normalize_fantastic(_record())
    assert job["ats_type"] == "fantastic" and job["location"] == "Bengaluru, India"
    assert job["skills_required"] == ["Python", "FastAPI"] and job["employment_type"] == "Full-time"
    assert isinstance(job["created_at"], datetime)
    assert isinstance(_metadata_params(job)["created_at"], datetime)  # asyncpg regression guard
    assert job["created_at"].year == 2020
    assert job["structured_data"]["source"] == "greenhouse"


def test_normalization_falls_back_locations_and_rejects_bad_url():
    job = normalize_fantastic(_record(locations_derived=None, locations_alt="Remote", url="javascript:bad",
                                     ai_key_skills="Python, SQL", date_posted=None))
    assert job["location"] == "Remote" and job["job_url"] is None
    assert job["skills_required"] == ["Python", "SQL"] and job["created_at"] is None


def test_normalization_uses_source_date_posted_not_fantastic_record_created_at():
    job = normalize_fantastic(_record(date_posted="2020-01-02T03:04:05Z", date_created="2026-01-02T03:04:05Z"))
    assert job["created_at"] == datetime(2020, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


def test_client_paginates_with_caps():
    calls = []
    def handler(request):
        calls.append(request)
        offset = int(request.url.params["offset"])
        return httpx.Response(200, json={"jobs": [_record(id=str(offset)), _record(id=str(offset + 1))]})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await FantasticClient(FantasticConfig("key", limit=2, max_pages=3, max_jobs_per_run=3, max_requests_per_run=2), client).fetch_active_ats()
    assert len(asyncio.run(run())) == 3 and len(calls) == 2


@pytest.mark.parametrize("status", [401, 429, 500])
def test_client_stops_on_provider_errors(status):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(status))) as client:
            await FantasticClient(FantasticConfig("key"), client).fetch_active_ats()
    with pytest.raises(FantasticAPIError): asyncio.run(run())


def test_global_insert_bypasses_company_scoped_ats_validation(monkeypatch):
    class Result:
        def first(self): return None
        def scalar_one(self): return "new-id"
    class Session:
        def __init__(self): self.insert = None
        async def execute(self, statement, params=None):
            sql = str(statement)
            assert "FROM company_registry" not in sql
            if "INSERT" in sql: self.insert = params
            return Result()
        async def commit(self): pass
    async def company_scoped_agency(*_):
        raise AssertionError("Fantastic must not use company-scoped ATS agency validation")
    monkeypatch.setattr("app.job_ingestion.job_ingestion_service.get_or_create_ats_agency", company_scoped_agency)
    session = Session(); job = normalize_fantastic(_record()); job["job_url"] = None
    assert asyncio.run(upsert_ats_job(session, job)) == "new-id"
    assert session.insert["company_registry_id"] is None
    assert session.insert["agency_id"] is None
    assert session.insert["ats_type"] == "fantastic"
    assert session.insert["ats_job_id"] == "fj-1"


def test_fantastic_upsert_error_log_preserves_postgres_diagnostics(caplog):
    """Regression guard for Railway logs: do not collapse asyncpg diagnostics."""
    class UniqueViolationError(Exception):
        sqlstate = "23505"
        constraint_name = "uq_job_descriptions_ats_type_ats_job_id"
        column_name = None

        def __str__(self):
            return "duplicate key value violates unique constraint"

    with caplog.at_level("WARNING"):
        _log_fantastic_upsert_error(
            UniqueViolationError(),
            {"ats_job_id": "fj-1", "title": "Backend Engineer", "structured_data": {"secret": "never logged"}},
        )

    message = caplog.messages[-1]
    assert "exception_class=UniqueViolationError" in message
    assert "exception_message=duplicate key value violates unique constraint" in message
    assert "sqlstate=23505" in message
    assert "constraint=uq_job_descriptions_ats_type_ats_job_id" in message
    assert "column=None" in message
    assert "id=fj-1" in message and "title='Backend Engineer'" in message
    assert "never logged" not in message
