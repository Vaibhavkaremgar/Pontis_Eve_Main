import pytest
from app.job_ingestion import scheduler
from groq_client import AllKeysRateLimitedError
from app.job_ingestion.job_skill_extraction import extract_missing_job_skills


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def all(self):
        return self.rows


class _Session:
    def __init__(self, row):
        self.row = row
        self.commits = 0
        self.rollbacks = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def execute(self, statement, params=None):
        assert params == {"batch_size": 25}
        return _Result([self.row])

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


def _pending_row():
    return {
        "id": "existing-id", "ats_type": "greenhouse", "ats_job_id": "ats-1",
        "title": "Engineer", "company_name": "Acme", "description": "Build APIs",
        "requirements": "Python", "skills": ["Python"], "skills_required": ["Python"],
        "structured_data": {"metadata_extraction_status": "pending_retry", "old": "keep"},
    }


@pytest.mark.asyncio
async def test_pending_metadata_retry_updates_existing_job_after_success(monkeypatch):
    session = _Session(_pending_row())
    upserted = []

    async def extract(job):
        job["structured_data"]["metadata_extraction_status"] = "complete"
        job["skills"] = ["Python", "FastAPI"]
        return job

    async def upsert(_, job):
        upserted.append(job)
        return job["id"]

    monkeypatch.setattr(scheduler, "_get_session_local", lambda: lambda: session)
    monkeypatch.setattr("app.job_ingestion.job_skill_extraction.extract_missing_job_skills", extract)
    monkeypatch.setattr("app.job_ingestion.job_ingestion_service.upsert_ats_job", upsert)

    assert await scheduler.retry_pending_metadata_extraction() == 1
    assert len(upserted) == 1
    assert upserted[0]["description"] == "Build APIs"
    assert session.commits == 1


@pytest.mark.asyncio
async def test_pending_metadata_retry_does_not_update_when_still_unavailable(monkeypatch):
    session = _Session(_pending_row())
    upserted = []

    async def extract(job):
        # This is the extractor's unavailable result: status remains pending.
        return job

    async def upsert(_, job):
        upserted.append(job)

    monkeypatch.setattr(scheduler, "_get_session_local", lambda: lambda: session)
    monkeypatch.setattr("app.job_ingestion.job_skill_extraction.extract_missing_job_skills", extract)
    monkeypatch.setattr("app.job_ingestion.job_ingestion_service.upsert_ats_job", upsert)

    assert await scheduler.retry_pending_metadata_extraction() == 0
    assert upserted == []
    assert session.commits == 0


@pytest.mark.asyncio
async def test_production_shaped_area_sales_manager_metadata_retry_is_in_place(monkeypatch):
    """Rate limiting queues the existing job; recovery enriches that same row."""
    job_id = "3833ab4b-9082-46c1-b68a-5013c239ebc7"
    original = {
        "id": job_id,
        "ats_type": "greenhouse",
        "ats_job_id": "3833ab4b-9082-46c1-b68a-5013c239ebc7",
        "title": "Area Sales Manager",
        "company_name": "Production Fixture Co",
        "description": "Lead regional sales growth and coach field teams.",
        "requirements": "5+ years in sales leadership; CRM experience required.",
        "skills": ["Salesforce", "Negotiation"],
        "skills_required": ["Salesforce", "Negotiation"],
        "experience_required": "5+ years",
        "salary_range": "₹12,00,000-₹18,00,000",
        "employment_type": "Full-time",
        "structured_data": {"source": "production-shaped", "metadata_extraction_status": "pending"},
    }

    # Simulate all Groq keys being unavailable through the real extractor.
    class RateLimitedPool:
        def __init__(self):
            raise AllKeysRateLimitedError("fixture: all Groq keys rate limited")

    monkeypatch.setattr("groq_client.GroqClientPool", RateLimitedPool)
    queued = await extract_missing_job_skills(original)
    assert queued["id"] == job_id
    assert queued["structured_data"]["metadata_extraction_status"] == "pending_retry"
    assert queued["skills"] == ["Salesforce", "Negotiation"]
    assert queued["skills_required"] == ["Salesforce", "Negotiation"]
    assert queued["experience_required"] == "5+ years"
    assert queued["salary_range"] == "₹12,00,000-₹18,00,000"
    assert queued["employment_type"] == "Full-time"

    # The retry reads the existing row and persists one in-place enrichment.
    retry_row = dict(queued)
    retry_row["structured_data"] = dict(queued["structured_data"])
    session = _Session(retry_row)
    upserted = []

    async def successful_extract(job):
        job["skills"] = ["Salesforce", "Negotiation", "Pipeline Management"]
        job["skills_required"] = job["skills"]
        job["structured_data"]["metadata_extraction_status"] = "complete"
        job["structured_data"]["skill_extraction"] = {"provider_skills": ["Salesforce", "Negotiation"]}
        return job

    async def upsert(_, job):
        upserted.append(job)
        return job["id"]

    monkeypatch.setattr(scheduler, "_get_session_local", lambda: lambda: session)
    monkeypatch.setattr("app.job_ingestion.job_skill_extraction.extract_missing_job_skills", successful_extract)
    monkeypatch.setattr("app.job_ingestion.job_ingestion_service.upsert_ats_job", upsert)

    assert await scheduler.retry_pending_metadata_extraction() == 1
    assert len(upserted) == 1
    persisted = upserted[0]
    assert persisted["id"] == job_id
    assert persisted["structured_data"]["metadata_extraction_status"] == "complete"
    assert persisted["skills_required"] == ["Salesforce", "Negotiation", "Pipeline Management"]
    assert persisted["experience_required"] == "5+ years"
    assert persisted["salary_range"] == "₹12,00,000-₹18,00,000"
    assert persisted["employment_type"] == "Full-time"
    assert session.commits == 1
