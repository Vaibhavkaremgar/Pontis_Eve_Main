import pytest

from app.job_ingestion import scheduler


def test_delete_job_embeddings_attempts_all_ids_after_failure(monkeypatch, caplog):
    calls = []

    def delete_job_embedding(job_id):
        calls.append(job_id)
        if job_id == "job-2":
            raise RuntimeError("qdrant unavailable")

    monkeypatch.setattr(
        "app.job_ingestion.qdrant_service.delete_job_embedding",
        delete_job_embedding,
    )

    scheduler._delete_job_embeddings(
        ["job-1", "job-2", "job-3"],
        log_context="[job-lifecycle] provider=fantastic",
    )

    assert calls == ["job-1", "job-2", "job-3"]
    assert "job-2" in caplog.text
    assert "provider=fantastic" in caplog.text


def test_delete_job_embeddings_preserves_scheduler_reconciliation_log_format(monkeypatch, caplog):
    def delete_job_embedding(_job_id):
        raise RuntimeError("qdrant unavailable")

    monkeypatch.setattr(
        "app.job_ingestion.qdrant_service.delete_job_embedding",
        delete_job_embedding,
    )

    scheduler._delete_job_embeddings(
        ["job-1"],
        log_context="[job-scheduler]",
        log_message="[job-scheduler] Qdrant delete failed for job_id=%s: %s",
    )

    assert "[job-scheduler] Qdrant delete failed for job_id=job-1: qdrant unavailable" in caplog.text


def test_delete_job_embeddings_handles_qdrant_import_failure_per_job(monkeypatch, caplog):
    import builtins

    import_calls = []
    real_import = builtins.__import__

    def failing_qdrant_import(name, *args, **kwargs):
        if name == "app.job_ingestion.qdrant_service":
            import_calls.append(name)
            raise ImportError("qdrant unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", failing_qdrant_import)

    scheduler._delete_job_embeddings(
        ["job-1", "job-2"],
        log_context="[job-lifecycle] provider=fantastic",
    )

    assert len(import_calls) == 2
    assert "job_id=job-1" in caplog.text
    assert "job_id=job-2" in caplog.text


class Result:
    def __init__(self, rows): self.rows = rows
    def fetchall(self): return self.rows


class DB:
    def __init__(self): self.calls = []
    async def execute(self, statement, params=None):
        sql = str(statement).lower()
        self.calls.append((sql, params or {}))
        if "select id, ats_job_id" in sql: return Result([("db-1", "f-1")])
        return Result([])
    async def commit(self): pass


@pytest.mark.asyncio
async def test_confirmed_provider_id_closes_and_hides_job():
    db = DB()
    closed = await scheduler._close_confirmed_jobs(db, ats_type="fantastic", ats_job_ids={"f-1"})
    assert closed == ["db-1"]
    assert any("update job_descriptions" in sql for sql, _ in db.calls)
    assert any("update candidate_job_recommendations" in sql for sql, _ in db.calls)


@pytest.mark.asyncio
async def test_unrelated_provider_id_changes_nothing():
    class EmptyDB(DB):
        async def execute(self, statement, params=None):
            sql = str(statement).lower()
            self.calls.append((sql, params or {}))
            return Result([])
    db = EmptyDB()
    assert await scheduler._close_confirmed_jobs(db, ats_type="fantastic", ats_job_ids={"other"}) == []
    assert not any("update job_descriptions" in sql for sql, _ in db.calls)


@pytest.mark.asyncio
async def test_theirstack_client_batches_exact_ids(monkeypatch):
    calls = []

    class Client:
        async def fetch_jobs_by_ids(self, ids):
            calls.append(ids)
            return [{"id": ids[0], "closed_at": "2026-10-06T00:00:00Z"}]

    class Session:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def commit(self): pass
        async def execute(self, *_args, **_kwargs):
            return Result([(str(i),) for i in range(205)])

    monkeypatch.setattr(scheduler, "_get_session_local", lambda: lambda: Session())
    monkeypatch.setattr("app.job_ingestion.connectors.theirstack.TheirStackClient", Client)
    monkeypatch.setattr(scheduler, "_close_confirmed_jobs", lambda *args, **kwargs: _empty_async())
    await scheduler.cleanup_theirstack_closed_jobs()
    assert [len(batch) for batch in calls] == [100, 100, 5]


async def _empty_async():
    return []
