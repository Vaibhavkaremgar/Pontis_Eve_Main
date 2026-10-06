import pytest

from app.job_ingestion import scheduler


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
