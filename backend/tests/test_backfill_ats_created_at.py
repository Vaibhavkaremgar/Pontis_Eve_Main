import asyncio
from datetime import datetime, timezone

from app.job_ingestion.backfill_ats_created_at import backfill_ats_created_at


class _Result:
    def __init__(self, rows=()): self.rows = list(rows)
    def mappings(self): return self
    def fetchall(self): return self.rows
    def scalar_one_or_none(self): return self.rows[0]["id"] if self.rows else None


class _Session:
    def __init__(self):
        self.rows = [
            {"id": "ashby-row", "ats_type": "ashby", "ats_job_id": "a1", "company_name": "Acme", "identifier": "acme"},
            {"id": "lever-row", "ats_type": "lever", "ats_job_id": "l1", "company_name": "Acme", "identifier": "acme-lever"},
            {"id": "fantastic-row", "ats_type": "fantastic", "ats_job_id": "f1", "company_name": "Acme", "identifier": None},
            {"id": "greenhouse-row", "ats_type": "greenhouse", "ats_job_id": "g1", "company_name": "Acme", "identifier": "acme-gh"},
        ]
        self.updates, self.commits = [], 0
    async def __aenter__(self): return self
    async def __aexit__(self, *_): return False
    async def execute(self, statement, params=None):
        sql = str(statement).lower()
        if "select jd.id" in sql:
            return _Result(self.rows)
        if "update job_descriptions" in sql:
            self.updates.append(params)
            return _Result([{"id": params["id"]}])
        raise AssertionError(sql)
    async def commit(self): self.commits += 1


class _Factory:
    def __init__(self, session): self.session = session
    def __call__(self): return self.session


class _Collector:
    def __init__(self): self.calls = []
    def collect_company_jobs(self, ats_type, identifier, _company):
        self.calls.append((ats_type, identifier))
        return {
            "ashby": [{"id": "a1", "publishedAt": "2020-01-02T03:04:05Z"}],
            "lever": [{"id": "l1", "createdAt": 1577934245000}],
        }[ats_type]


class _Fantastic:
    async def fetch_active_ats(self):
        return [{"id": "f1", "date_posted": "2020-01-02T03:04:05Z", "date_created": "2026-01-01T00:00:00Z"}]


def test_backfill_updates_only_exact_ids_with_provider_original_timestamps():
    session, collector = _Session(), _Collector()
    stats = asyncio.run(backfill_ats_created_at(_Factory(session), collector, _Fantastic(), apply=True))
    assert {update["id"] for update in session.updates} == {"ashby-row", "lever-row", "fantastic-row"}
    assert all(set(update) == {"id", "ats_type", "ats_job_id", "created_at"} for update in session.updates)
    assert all(update["created_at"] == datetime(2020, 1, 2, 3, 4, 5, tzinfo=timezone.utc) for update in session.updates)
    assert collector.calls == [("ashby", "acme"), ("lever", "acme-lever")]
    assert stats["greenhouse_skipped"] == 1 and stats["updated"] == 3 and session.commits == 1


def test_backfill_is_dry_run_by_default_and_skips_invalid_or_missing_dates():
    session = _Session()
    class InvalidCollector(_Collector):
        def collect_company_jobs(self, ats_type, identifier, company):
            if ats_type == "ashby": return [{"id": "a1", "publishedAt": "invalid"}]
            return [{"id": "l1", "createdAt": None}]
    stats = asyncio.run(backfill_ats_created_at(_Factory(session), InvalidCollector(), _Fantastic(), apply=False))
    assert session.updates == [] and session.commits == 0
    assert stats["missing_timestamp"] == 2 and stats["updated"] == 0
