import pytest

from app.job_ingestion import scheduler


class _Result:
    def first(self):
        return None


class _Session:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, *_args, **_kwargs):
        return _Result()

    async def rollback(self):
        pass


@pytest.mark.asyncio
async def test_theirstack_sync_calls_job_skill_extractor(monkeypatch):
    monkeypatch.setenv("THEIRSTACK_ENABLED", "true")

    class _Client:
        async def fetch_jobs(self):
            return [{"id": "ts-1", "title": "Engineer", "url": "https://example.test/ts-1", "location": "Bengaluru", "job_country": "India"}]

    async def _extract(job):
        job["extracted"] = True
        return job

    persisted = []
    monkeypatch.setattr("app.job_ingestion.connectors.theirstack.TheirStackClient", _Client)
    monkeypatch.setattr("app.job_ingestion.job_skill_extraction.extract_missing_job_skills", _extract)
    monkeypatch.setattr(scheduler, "_get_session_local", lambda: _Session)
    monkeypatch.setattr(scheduler, "upsert_ats_job", lambda *_args, **_kwargs: None, raising=False)

    async def _upsert(_db, job, **_kwargs):
        persisted.append(job)

    monkeypatch.setattr("app.job_ingestion.job_ingestion_service.upsert_ats_job", _upsert)

    result = await scheduler.sync_theirstack_jobs()

    assert result["inserted"] == 1
    assert result["failed"] == 0
    assert persisted[0]["extracted"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("existing_type", "existing_id", "expected_updated", "expected_skipped"),
    [
        ("theirstack", "ts-older", 0, 1),  # same-provider URL collision
        ("theirstack", "ts-1", 1, 1),      # stable-ID match updates, but skips complete extraction
        ("fantastic", "ts-1", 0, 1),       # cross-provider URL collision
    ],
)
async def test_theirstack_url_match_never_overrides_provider_stable_identity(
    monkeypatch, existing_type, existing_id, expected_updated, expected_skipped
):
    monkeypatch.setenv("THEIRSTACK_ENABLED", "true")

    class _Client:
        async def fetch_jobs(self):
            return [{"id": "ts-1", "title": "Engineer", "url": "https://example.test/shared", "location": "Bengaluru", "job_country": "India"}]

    existing = {
        "id": "existing-row", "ats_type": existing_type, "ats_job_id": existing_id,
        "job_url": "https://example.test/shared", "description": "Old", "skills": ["Python"],
        "skills_required": ["Python"], "structured_data": {"metadata_extraction_status": "complete"},
    }
    persisted = []
    async def lookup(*_args): return existing
    async def upsert(_db, job, **kwargs): persisted.append((job, kwargs["existing_job"]))

    monkeypatch.setattr("app.job_ingestion.connectors.theirstack.TheirStackClient", _Client)
    monkeypatch.setattr(scheduler, "_get_session_local", lambda: _Session)
    monkeypatch.setattr("app.job_ingestion.job_ingestion_service.find_existing_ats_job_by_id_or_url", lookup)
    monkeypatch.setattr("app.job_ingestion.job_ingestion_service.upsert_ats_job", upsert)

    result = await scheduler.sync_theirstack_jobs()

    assert result["updated"] == expected_updated
    assert result["skipped"] == expected_skipped
    assert len(persisted) == expected_updated
