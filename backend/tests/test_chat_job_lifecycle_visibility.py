"""Chat job results must recheck current job state, even for unhidden recommendations."""
import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://unused:unused@localhost/unused")
os.environ.setdefault("GROQ_API_KEY_1", "local-test-placeholder")

import server
from app.job_ingestion.lifecycle import candidate_visible_where


@pytest.mark.asyncio
async def test_chat_excludes_closed_unhidden_job_and_returns_active_job(monkeypatch):
    jobs = [
        {"id": "closed", "title": "Closed role", "is_active": False,
         "status": "closed", "job_status": "closed", "job_url": "https://example.com/closed"},
        {"id": "active", "title": "Active role", "is_active": True,
         "status": "active", "job_status": "active", "job_url": "https://example.com/active"},
    ]
    captured_sql = []
    prompts = []

    class Result:
        def __init__(self, rows=(), count=None):
            self.rows, self.count = list(rows), count

        def scalar(self):
            return self.count

        def fetchall(self):
            return self.rows

        def mappings(self):
            return self

        def fetchone(self):
            return None

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def execute(self, statement, params=None):
            sql = str(statement)
            if "candidate_preferences" in sql:
                return Result()
            if "SELECT COUNT(*) FROM candidate_job_recommendations" in sql:
                return Result(count=2)
            if "FROM candidate_job_recommendations cjr" in sql:
                captured_sql.append(sql)
                assert "cjr.hidden_at IS NULL" in sql
                assert candidate_visible_where("jd").strip() in sql
                visible = [job for job in jobs if job["is_active"] and job["status"] == "active"
                           and job["job_status"] == "active" and job["job_url"].startswith("https://")]
                if "SELECT cjr.id, jd.title" in sql:
                    return Result([(job["id"], job["title"], "", "", [], [], "") for job in visible])
                return Result([{"match_score": 0.9, "title": job["title"],
                                "company_name": "Example", "location": "India",
                                "salary_range": "", "description": ""}
                               for job in visible if job["id"] in (params or {}).get("eligible_ids", [])])
            raise AssertionError(f"Unexpected SQL: {sql}")

    async def candidate(_candidate_id):
        return {"id": "candidate-1", "raw_data": {}}

    async def empty(*_args, **_kwargs):
        return []

    async def noop(*_args, **_kwargs):
        return None

    async def complete(**kwargs):
        prompts.append(kwargs["messages"][0]["content"])
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="Here are your matches."))])

    monkeypatch.setattr(server, "SessionLocal", Session)
    monkeypatch.setattr(server, "_authorize_candidate", lambda *_args: None)
    monkeypatch.setattr(server, "_get_candidate_row", candidate)
    monkeypatch.setattr(server, "_normalize_for_frontend", lambda _row: {})
    monkeypatch.setattr(server, "_build_profile_context", lambda _profile: ("Profile", []))
    monkeypatch.setattr(server, "_load_intake_ledger", empty)
    monkeypatch.setattr(server, "_load_chat_window", empty)
    monkeypatch.setattr(server, "_save_chat_window", noop)
    monkeypatch.setattr(server, "_chat_profile_preflight", lambda *_args: None)
    monkeypatch.setattr(server, "_voice_intake_completed_for_matching", lambda _row: True)
    monkeypatch.setattr(server, "_effective_profile_strength_percent", lambda *_args: _strength())
    monkeypatch.setattr(server, "_advance_voice_intake_from_chat", noop)
    monkeypatch.setattr(server, "_record_demonstrated_skill_usage", noop)
    monkeypatch.setattr(server, "openai_client", SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=complete))))

    await server.chat(server.ChatRequest(candidate_id="candidate-1", session_id="session-1",
                                   messages=[server.ChatMessageIn(role="user", content="Find me jobs")]))

    assert len(captured_sql) == 2
    assert "Active role" in prompts[0]
    assert "Closed role" not in prompts[0]


async def _strength():
    return 95
