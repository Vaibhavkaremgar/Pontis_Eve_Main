import asyncio
import json
import sys
import types

from app.job_ingestion.job_skill_extraction import extract_missing_job_skills
from app.job_ingestion.job_ingestion_service import _metadata_params
from app.job_ingestion.normalize import normalize_ashby


def _job(description):
    return normalize_ashby({"id": "new-job", "title": "Engineer", "descriptionHtml": description}, "Acme")


def test_deterministic_skills_are_shared_by_skills_and_skills_required():
    job = _job("Required skills: Salesforce, Workday, Excel, Postgres, Go, Python")
    assert job["skills"] == job["skills_required"]
    assert {"Salesforce", "Workday", "Excel", "PostgreSQL", "Python", "Go"} <= set(job["skills"])


def test_go_false_positive_is_rejected_but_legitimate_go_is_kept():
    assert "Go" in _job("Experience with Go programming and Python")["skills"]
    assert "Go" not in _job("Own go-live and go-to-market activities")["skills"]


def test_empty_jd_does_not_call_llm(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test")
    called = False

    class Forbidden:
        async def create(self, **kwargs):
            nonlocal called
            called = True
            raise AssertionError("LLM should not be called for an empty JD")

    monkeypatch.setitem(sys.modules, "groq_client", types.SimpleNamespace(GroqClientPool=lambda: None))
    job = {"ats_job_id": "empty", "title": "Engineer", "description": "", "requirements": "", "skills": [], "skills_required": []}
    assert asyncio.run(extract_missing_job_skills(job))["skills"] == []
    assert not called


def test_meaningful_jd_uses_mocked_llm_and_populates_both_fields(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test")
    calls = []

    class Completions:
        async def create(self, **kwargs):
            calls.append(kwargs)
            message = types.SimpleNamespace(content=json.dumps({"skills": ["Postgres", "Python"]}))
            return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])

    class Client:
        def __init__(self):
            self.chat = types.SimpleNamespace(completions=Completions())

    monkeypatch.setitem(sys.modules, "groq_client", types.SimpleNamespace(GroqClientPool=Client))
    job = {"ats_job_id": "fallback", "title": "Engineer", "description": "Build reliable services", "requirements": "Build reliable services", "skills": [], "skills_required": []}
    result = asyncio.run(extract_missing_job_skills(job))
    assert len(calls) == 1
    assert result["skills"] == result["skills_required"] == ["PostgreSQL", "Python"]


def test_persistence_params_bind_both_skill_columns():
    params = _metadata_params({"skills": ["Python"], "skills_required": ["Python", "PostgreSQL"]})
    assert json.loads(params["skills"]) == ["Python"]
    assert json.loads(params["skills_required"]) == ["Python", "PostgreSQL"]
