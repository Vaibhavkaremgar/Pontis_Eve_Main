import pytest

from app.job_ingestion.scheduler import _complete_board_payload, reconcile_missing_ats_jobs


class Result:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class DB:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    async def execute(self, statement, params=None):
        sql = str(statement).lower()
        self.calls.append((sql, params))
        if "select id, ats_job_id" in sql:
            return Result(self.rows)
        return Result([])


@pytest.mark.asyncio
async def test_complete_snapshot_closes_missing_job():
    db = DB([("missing", "old"), ("present", "new")])
    closed = await reconcile_missing_ats_jobs(
        db, company_registry_id="company", ats_type="ashby", current_ats_ids={"new"}
    )
    assert closed == ["missing"]
    assert any("update job_descriptions" in sql for sql, _ in db.calls)
    assert any("update candidate_job_recommendations" in sql for sql, _ in db.calls)


@pytest.mark.asyncio
async def test_complete_snapshot_present_job_remains_active():
    db = DB([("present", "new")])
    closed = await reconcile_missing_ats_jobs(
        db, company_registry_id="company", ats_type="lever", current_ats_ids={"new"}
    )
    assert closed == []
    assert not any("update job_descriptions" in sql for sql, _ in db.calls)


@pytest.mark.asyncio
async def test_non_snapshot_provider_never_reconciles_absence():
    db = DB([("missing", "old")])
    closed = await reconcile_missing_ats_jobs(
        db, company_registry_id="company", ats_type="fantastic", current_ats_ids=set()
    )
    assert closed == []
    assert not any("update job_descriptions" in sql for sql, _ in db.calls)


def test_incomplete_board_payload_is_rejected_before_reconciliation():
    assert _complete_board_payload([{"ats_job_id": "a"}]) == (True, {"a"})
    assert _complete_board_payload([{"title": "missing id"}]) == (False, set())
    assert _complete_board_payload(None) == (False, set())
