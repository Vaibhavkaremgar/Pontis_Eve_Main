import json
import sys
import types

import pytest

from app.job_ingestion import scheduler


class _Session:
    async def __aenter__(self): return self
    async def __aexit__(self, *_args): return False
    async def rollback(self): pass


def _existing(provider, description="Build APIs", skills_required=None, status="complete"):
    return {
        "id": "existing-id", "ats_type": provider, "ats_job_id": "provider-1",
        "job_url": "https://jobs.example/provider-1", "description": description,
        "requirements": None, "skills": ["Python"],
        "skills_required": ["Python"] if skills_required is None else skills_required,
        "experience_required": "5 years", "salary_range": "$100k-$120k",
        "employment_type": "Full-time", "structured_data": {"metadata_extraction_status": status},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["fantastic", "theirstack"])
@pytest.mark.parametrize(
    ("existing", "expected_calls"),
    [
        (_existing("PROVIDER"), 0),
        (_existing("PROVIDER", description="Old description"), 1),
        (_existing("PROVIDER", skills_required=[]), 1),
        (_existing("PROVIDER", status="pending_retry"), 1),
    ],
)
async def test_global_provider_pre_extraction_gate(monkeypatch, provider, existing, expected_calls):
    """Complete unchanged rows avoid the mocked LLM; eligible rows still call it."""
    existing["ats_type"] = provider
    calls, upserts = [], []

    async def lookup(_db, requested_provider, _ats_job_id, _url):
        assert requested_provider == provider
        return existing

    async def extract(job):
        calls.append(job["ats_job_id"])
        return job

    async def upsert(_db, job, *, existing_job):
        upserts.append((job, existing_job))

    monkeypatch.setattr(scheduler, "_get_session_local", lambda: _Session)
    monkeypatch.setattr("app.job_ingestion.job_ingestion_service.find_existing_ats_job_by_id_or_url", lookup)
    monkeypatch.setattr("app.job_ingestion.job_ingestion_service.upsert_ats_job", upsert)
    monkeypatch.setattr("app.job_ingestion.job_skill_extraction.extract_missing_job_skills", extract)

    if provider == "fantastic":
        monkeypatch.setenv("FANTASTIC_ENABLED", "true")
        class Client:
            last_fetch_stats = {}
            async def fetch_active_ats(self):
                return [{"id": "provider-1", "organization": "Acme", "title": "Engineer", "description_text": "Build APIs", "url": "https://jobs.example/provider-1", "locations_derived": ["Bengaluru", "India"]}]
            async def fetch_active_job_boards(self): return []
        monkeypatch.setattr("app.job_ingestion.connectors.fantastic.FantasticClient", Client)
        await scheduler.sync_fantastic_jobs()
    else:
        monkeypatch.setenv("THEIRSTACK_ENABLED", "true")
        class Client:
            async def fetch_jobs(self):
                return [{"id": "provider-1", "title": "Engineer", "description": "Build APIs", "url": "https://jobs.example/provider-1", "location": "Bengaluru", "job_country": "India", "technology_slugs": ["python"]}]
        monkeypatch.setattr("app.job_ingestion.connectors.theirstack.TheirStackClient", Client)
        await scheduler.sync_theirstack_jobs()

    assert len(calls) == expected_calls
    assert upserts and upserts[0][1] is existing


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["fantastic", "theirstack"])
@pytest.mark.parametrize(
    ("description", "skills_required", "status", "expected_llm_calls"),
    [
        ("Build APIs", ["Python"], "complete", 0),
        ("Revised APIs", ["Python"], "complete", 1),
        ("Build APIs", [], "complete", 1),
        ("Build APIs", ["Python"], "pending", 1),
        ("Build APIs", ["Python"], "pending_retry", 1),
    ],
)
async def test_global_scheduler_gate_uses_real_extractor_without_live_llm(
    monkeypatch, provider, description, skills_required, status, expected_llm_calls
):
    """The scheduler decision and extractor both run against a local fake client."""
    existing = _existing(provider, skills_required=skills_required, status=status)
    calls, persisted = [], []

    class Completions:
        async def create(self, **_kwargs):
            calls.append(1)
            message = types.SimpleNamespace(content=json.dumps({"skills": ["Python"]}))
            return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])

    client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=Completions()))
    monkeypatch.setitem(sys.modules, "groq_client", types.SimpleNamespace(GroqClientPool=lambda: client))
    monkeypatch.setattr(scheduler, "_get_session_local", lambda: _Session)
    async def lookup(*_args): return existing
    async def upsert(_db, job, *, existing_job): persisted.append((job, existing_job))
    monkeypatch.setattr("app.job_ingestion.job_ingestion_service.find_existing_ats_job_by_id_or_url", lookup)
    monkeypatch.setattr("app.job_ingestion.job_ingestion_service.upsert_ats_job", upsert)

    if provider == "fantastic":
        monkeypatch.setenv("FANTASTIC_ENABLED", "true")
        class Client:
            last_fetch_stats = {}
            async def fetch_active_ats(self):
                return [{"id": "provider-1", "organization": "Acme", "title": "Engineer", "description_text": description, "url": "https://jobs.example/provider-1", "locations_derived": ["Bengaluru", "India"]}]
            async def fetch_active_job_boards(self): return []
        monkeypatch.setattr("app.job_ingestion.connectors.fantastic.FantasticClient", Client)
        await scheduler.sync_fantastic_jobs()
    else:
        monkeypatch.setenv("THEIRSTACK_ENABLED", "true")
        class Client:
            async def fetch_jobs(self):
                return [{"id": "provider-1", "title": "Engineer", "description": description, "url": "https://jobs.example/provider-1", "location": "Bengaluru", "job_country": "India"}]
        monkeypatch.setattr("app.job_ingestion.connectors.theirstack.TheirStackClient", Client)
        await scheduler.sync_theirstack_jobs()

    assert len(calls) == expected_llm_calls
    assert persisted and persisted[0][1] is existing
