import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.job_ingestion import scheduler
from app.job_ingestion.job_ingestion_service import upsert_ats_job


class FakeResult:
    def __init__(self, rows=None):
        self._rows = rows or []

    def first(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def mappings(self):
        return self

    def scalar_one(self):
        row = self.first()
        if row is None:
            raise AssertionError("scalar_one() called with no rows")
        if isinstance(row, dict):
            if "id" in row:
                return row["id"]
            return next(iter(row.values()))
        return row[0]


class FakeSession:
    def __init__(self, state):
        self.state = state
        self.execute_calls = []
        self.commit_count = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, statement, params=None):
        sql = str(statement)
        params = params or {}
        sql_lower = " ".join(sql.lower().split())
        self.execute_calls.append((sql, params))

        if "from job_descriptions" in sql_lower and "select id, job_url" in sql_lower:
            row = self.state["job_row"]
            if row and row["ats_type"] == params["ats_type"] and row["ats_job_id"] == params["ats_job_id"]:
                return FakeResult([(row["id"], row["job_url"])])
            return FakeResult([])

        if "update job_descriptions" in sql_lower:
            forbidden_fields = (
                "title =",
                "description =",
                "location =",
                "salary_range =",
                "company_name =",
                "department =",
                "employment_type =",
                "agency_id =",
                "company_registry_id =",
                "ats_job_id =",
                "ats_type =",
            )
            for field in forbidden_fields:
                assert field not in sql_lower, f"Unexpected field update in SQL: {field}"

            row = self.state["job_row"]
            assert row is not None, "UPDATE job_descriptions executed without an existing row"
            row["job_url"] = params["job_url"]
            row["updated_at"] = "updated-now"
            row["last_synced_at"] = "synced-now"
            self.state["update_statements"].append((sql, params))
            return FakeResult([])

        if "from company_registry" in sql_lower and "select id, company_name, ats_type, identifier" in sql_lower:
            return FakeResult([
                {
                    "id": "company-1",
                    "company_name": "Jumio",
                    "ats_type": "greenhouse",
                    "identifier": "jumio",
                }
            ])

        if "from company_registry" in sql_lower and "select id" in sql_lower:
            return FakeResult([("company-1",)])

        if "update company_registry" in sql_lower:
            self.state["company_registry_updated"] = True
            return FakeResult([])

        if "insert into job_descriptions" in sql_lower:
            self.state["insert_called"] = True
            return FakeResult([("new-job-id",)])

        raise AssertionError(f"Unexpected SQL: {sql}")

    async def commit(self):
        self.commit_count += 1

    async def rollback(self):
        return None


class SessionFactory:
    def __init__(self, state):
        self.state = state

    def __call__(self):
        return FakeSession(self.state)


def _base_existing_row(job_url):
    return {
        "id": "existing-job-id",
        "ats_type": "greenhouse",
        "ats_job_id": "ats-123",
        "job_url": job_url,
        "title": "Original Title",
        "description": "Original description",
        "location": "Original location",
        "salary_range": "100000-120000",
        "company_name": "Jumio",
        "department": "Engineering",
        "employment_type": "Full-time",
        "updated_at": "original-updated-at",
        "last_synced_at": "original-last-synced",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "existing_url,incoming_url,should_update",
    [
        (None, "https://example.com/new", True),
        ("https://example.com/existing", "https://example.com/new", False),
        (None, None, False),
    ],
)
async def test_upsert_ats_job_only_fills_missing_url(monkeypatch, existing_url, incoming_url, should_update):
    state = {
        "job_row": _base_existing_row(existing_url),
        "update_statements": [],
        "company_registry_updated": False,
        "insert_called": False,
    }

    async def fail_async_if_called(*args, **kwargs):
        raise AssertionError("Unexpected async dependency called for existing ATS job")

    def fail_sync_if_called(*args, **kwargs):
        raise AssertionError("Unexpected sync dependency called for existing ATS job")

    monkeypatch.setattr("app.job_ingestion.job_ingestion_service.get_or_create_ats_agency", fail_async_if_called)

    session_factory = SessionFactory(state)
    async with session_factory() as db:
        job_id = await upsert_ats_job(
            db,
            {
                "ats_type": "greenhouse",
                "ats_job_id": "ats-123",
                "company_name": "Jumio",
                "title": "New Title",
                "description": "New description",
                "location": "New location",
                "salary_range": "200000-250000",
                "department": "Product",
                "employment_type": "Contract",
                "job_url": incoming_url,
            },
        )

    assert job_id == "existing-job-id"
    assert state["job_row"]["title"] == "Original Title"
    assert state["job_row"]["description"] == "Original description"
    assert state["job_row"]["location"] == "Original location"
    assert state["job_row"]["salary_range"] == "100000-120000"
    assert state["job_row"]["company_name"] == "Jumio"
    assert state["job_row"]["department"] == "Engineering"
    assert state["job_row"]["employment_type"] == "Full-time"

    if should_update:
        assert state["job_row"]["job_url"] == incoming_url
        assert state["job_row"]["updated_at"] == "updated-now"
        assert state["job_row"]["last_synced_at"] == "synced-now"
        assert len(state["update_statements"]) == 1
    else:
        assert state["job_row"]["job_url"] == existing_url
        assert state["job_row"]["updated_at"] == "original-updated-at"
        assert state["job_row"]["last_synced_at"] == "original-last-synced"
        assert state["update_statements"] == []


@pytest.mark.asyncio
async def test_scheduler_does_not_pre_skip_existing_ats_jobs(monkeypatch):
    state = {
        "job_row": _base_existing_row("https://example.com/existing"),
        "update_statements": [],
        "company_registry_updated": False,
        "insert_called": False,
    }

    class FakeCollector:
        def collect_company_jobs(self, ats_type, identifier, company_name):
            assert ats_type == "greenhouse"
            assert identifier == "jumio"
            assert company_name == "Jumio"
            return [
                {
                    "ats_type": "greenhouse",
                    "ats_job_id": "ats-123",
                    "company_name": "Jumio",
                    "title": "New Title",
                    "description": "New description",
                    "location": "New location",
                    "salary_range": "200000-250000",
                    "department": "Product",
                    "employment_type": "Contract",
                    "job_url": "https://example.com/new",
                }
            ]

    async def fake_upsert(db, job):
        state["upsert_called"] = state.get("upsert_called", 0) + 1
        assert job["ats_job_id"] == "ats-123"
        return "existing-job-id"

    class SchedulerSessionFactory:
        def __call__(self):
            return SchedulerFakeSession(state)

    class SchedulerFakeSession(FakeSession):
        async def execute(self, statement, params=None):
            sql = str(statement)
            sql_lower = " ".join(sql.lower().split())
            self.execute_calls.append((sql, params or {}))

            if "from company_registry" in sql_lower and "select id, company_name, ats_type, identifier" in sql_lower:
                return FakeResult([
                    {
                        "id": "company-1",
                        "company_name": "Jumio",
                        "ats_type": "greenhouse",
                        "identifier": "jumio",
                    }
                ])

            if "from job_descriptions" in sql_lower:
                raise AssertionError("Scheduler should not pre-skip existing ATS jobs")

            if "update company_registry" in sql_lower:
                self.state["company_registry_updated"] = True
                return FakeResult([])

            raise AssertionError(f"Unexpected scheduler SQL: {sql}")

    monkeypatch.setattr("app.job_ingestion.collect_jobs.JobCollector", lambda: FakeCollector())
    monkeypatch.setattr(scheduler, "_get_session_local", lambda: SchedulerSessionFactory())
    monkeypatch.setattr("app.job_ingestion.job_ingestion_service.upsert_ats_job", fake_upsert)

    await scheduler.sync_jobs()

    assert state.get("upsert_called") == 1
    assert state["company_registry_updated"] is True
