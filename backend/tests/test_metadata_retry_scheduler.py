import pytest

from app.job_ingestion import scheduler


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
