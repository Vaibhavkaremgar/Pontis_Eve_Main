import asyncio
from datetime import datetime

import httpx
import pytest

from app.job_ingestion.connectors.fantastic import FantasticAPIError, FantasticClient, FantasticConfig
from app.job_ingestion.normalize import normalize_fantastic
from app.job_ingestion.job_ingestion_service import _metadata_params, upsert_ats_job


def _record(**overrides):
    record = {"id": "fj-1", "organization": "Acme", "title": "Backend Engineer",
              "description_text": "Build APIs", "url": "https://jobs.example/fj-1",
              "locations_derived": ["Bengaluru", "India"], "date_created": "2026-07-16T22:10:27+05:30",
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
    assert job["structured_data"]["source"] == "greenhouse"


def test_normalization_falls_back_locations_and_rejects_bad_url():
    job = normalize_fantastic(_record(locations_derived=None, locations_alt="Remote", url="javascript:bad",
                                     ai_key_skills="Python, SQL", date_created=None))
    assert job["location"] == "Remote" and job["job_url"] is None
    assert job["skills_required"] == ["Python", "SQL"] and job["created_at"] is None


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


def test_global_insert_does_not_query_or_create_company_registry(monkeypatch):
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
    async def agency(*_): return "fantastic-agency"
    monkeypatch.setattr("app.job_ingestion.job_ingestion_service.get_or_create_ats_agency", agency)
    session = Session(); job = normalize_fantastic(_record()); job["job_url"] = None
    assert asyncio.run(upsert_ats_job(session, job)) == "new-id"
    assert session.insert["company_registry_id"] is None
