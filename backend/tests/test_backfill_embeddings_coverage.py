import asyncio
import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType


class _Rows:
    def __init__(self, rows): self.rows = rows
    def mappings(self): return self
    def fetchall(self): return self.rows
    def scalar(self): return len(self.rows)


class _Session:
    def __init__(self, jobs): self.jobs = jobs
    async def __aenter__(self): return self
    async def __aexit__(self, *args): return False
    async def execute(self, statement, params=None):
        # The test data includes a non-ATS job: it must be selected as visible.
        assert "WHERE ats_type" not in str(statement)
        assert "is_active IS TRUE" in str(statement)
        return _Rows(self.jobs)


def test_backfill_selects_candidate_visible_non_ats_jobs(monkeypatch):
    jobs = [{"id": "non-ats-job", "ats_type": "custom", "title": "Java Engineer"}]
    server = ModuleType("server")
    server.SessionLocal = lambda: _Session(jobs)
    sqlalchemy = ModuleType("sqlalchemy")
    sqlalchemy.text = lambda value: value
    embedding = ModuleType("app.job_ingestion.embedding_service")
    embedding.generate_job_embedding = lambda job: [.1] * 384
    qdrant = ModuleType("app.job_ingestion.qdrant_service")
    written = []
    qdrant.ensure_collection = lambda: None
    qdrant.upsert_job_embedding = lambda job_id, vector, job, **kwargs: written.append((job_id, job["ats_type"]))

    monkeypatch.setitem(sys.modules, "server", server)
    monkeypatch.setitem(sys.modules, "sqlalchemy", sqlalchemy)
    monkeypatch.setitem(sys.modules, "app.job_ingestion.embedding_service", embedding)
    monkeypatch.setitem(sys.modules, "app.job_ingestion.qdrant_service", qdrant)
    path = Path(__file__).parents[1] / "app" / "job_ingestion" / "backfill_embeddings.py"
    spec = importlib.util.spec_from_file_location("backfill_embeddings_coverage", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    asyncio.run(module.backfill())
    assert written == [("non-ats-job", "custom")]
