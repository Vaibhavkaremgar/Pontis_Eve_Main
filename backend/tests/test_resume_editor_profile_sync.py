"""Regression coverage for Resume Editor -> canonical profile synchronization."""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import server


class _Session:
    def __init__(self, state):
        self.state = state

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def execute(self, statement, params=None):
        self.state["statement"] = str(statement)
        self.state["params"] = params or {}

    async def commit(self):
        self.state["committed"] = True


class _SessionFactory:
    def __init__(self, state):
        self.state = state

    def __call__(self):
        return _Session(self.state)


def _candidate():
    return {
        "id": "candidate-1",
        "name": "Candidate",
        "email": "candidate@example.test",
        "phone": "",
        "location": "",
        "current_role": "Engineer",
        "summary": "",
        "experience_years": 2,
        "skills": ["Python", "FastAPI", "PostgreSQL"],
        # This intentionally stale parse caused canonical skills to disappear.
        "parsed_resume_json": json.dumps({"skills": ["Python", "FastAPI"]}),
        "raw_data": json.dumps({"certifications": [], "projects": []}),
        "work_experience": [],
        "education": [],
    }


def test_resume_editor_payload_unions_canonical_and_parsed_skills():
    assert server._resume_editor_payload(_candidate())["skills"] == [
        "Python", "FastAPI", "PostgreSQL"
    ]


def test_resume_editor_save_persists_deduplicated_canonical_skills_and_parse(monkeypatch):
    state = {}
    candidate = _candidate()

    async def get_candidate(_candidate_id):
        return candidate

    monkeypatch.setattr(server, "_get_candidate_row", get_candidate)
    monkeypatch.setattr(server, "SessionLocal", _SessionFactory(state))

    result = asyncio.run(server._save_resume_editor_updates("candidate-1", {
        "skills": ["Python", "FastAPI", "PostgreSQL", "Java", "Spring Boot", "java"],
    }))

    assert result["updated"] is True
    saved_skills = json.loads(state["params"]["skills"])
    saved_parse = json.loads(state["params"]["parsed_resume_json"])
    assert saved_skills == ["Python", "FastAPI", "PostgreSQL", "Java", "Spring Boot"]
    assert saved_parse["skills"] == saved_skills
    assert state["committed"] is True

    # GET /profile derives keySkills from the canonical candidates.skills column.
    profile = server._normalize_for_frontend({**candidate, "skills": saved_skills})
    assert profile["keySkills"] == saved_skills

    # The selected-job matcher/gap calculation receives the same canonical row.
    gaps = server._job_missing_requirements(["Java", "Spring Boot"], "", {"skills": saved_skills})
    assert gaps["missing_skills"] == []
