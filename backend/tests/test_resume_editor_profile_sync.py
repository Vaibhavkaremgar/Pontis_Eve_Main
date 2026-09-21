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


def test_skill_normalization_keeps_multi_word_skills_and_repairs_safe_legacy_concatenation():
    skills = server._normalize_skills([
        "React.js Node.jsFrontend DevelopmentAI Applications",
        "Google Cloud Platform; Object-Oriented Programming\nReact.js",
    ])

    assert skills == [
        "React.js", "Node.js", "Frontend Development", "AI Applications",
        "Google Cloud Platform", "Object-Oriented Programming",
    ]
    assert all("Node.jsFrontend" not in skill for skill in skills)


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


def test_resume_editor_save_never_persists_a_delimited_skill_as_one_item(monkeypatch):
    state = {}

    async def get_candidate(_candidate_id):
        return _candidate()

    monkeypatch.setattr(server, "_get_candidate_row", get_candidate)
    monkeypatch.setattr(server, "SessionLocal", _SessionFactory(state))

    asyncio.run(server._save_resume_editor_updates("candidate-1", {
        "skills": ["React.js, Node.js; Frontend Development\nAI Applications"],
    }))

    saved_skills = json.loads(state["params"]["skills"])
    assert all(skill in saved_skills for skill in ["React.js", "Node.js", "Frontend Development", "AI Applications"])
    assert all("React.js, Node.js" not in skill for skill in saved_skills)


def test_resume_editor_save_repairs_the_reported_legacy_concatenated_skills(monkeypatch):
    state = {}

    async def get_candidate(_candidate_id):
        candidate = _candidate()
        candidate["skills"] = [
            "React.js Node.jsFrontend DevelopmentAI Applications Google Cloud Platform Database Design AI Applications",
            "Problem SolvingObject-Oriented ProgrammingSQLHTML5",
        ]
        return candidate

    monkeypatch.setattr(server, "_get_candidate_row", get_candidate)
    monkeypatch.setattr(server, "SessionLocal", _SessionFactory(state))

    asyncio.run(server._save_resume_editor_updates("candidate-1", {
        "skills": ["React.jsNode.js", "HTML5", "SQL"],
    }))

    saved_skills = json.loads(state["params"]["skills"])
    assert saved_skills == [
        "React.js", "Node.js", "Frontend Development", "AI Applications",
        "Google Cloud Platform", "Database Design", "Problem Solving",
        "Object-Oriented Programming", "SQL", "HTML5",
    ]
    assert all("Frontend DevelopmentAI" not in skill for skill in saved_skills)
    assert all("ProgrammingSQL" not in skill for skill in saved_skills)
    assert server._job_missing_requirements(saved_skills, "", {"skills": saved_skills})["missing_skills"] == []


def test_missing_skills_uses_normalized_canonical_profile_skills():
    gaps = server._job_missing_requirements(
        ["React.js", "Node.js", "Type Script", "Docker", "React JS"],
        "",
        {"skills": [" reactjs ", "NODE JS", "TypeScript"]},
    )

    # Case, surrounding whitespace, and punctuation/spacing variants are
    # represented by the already-saved canonical candidate skills.
    assert gaps["missing_skills"] == ["Docker"]


def test_improve_job_match_returns_refreshed_canonical_profile_without_duplicate_recommendation(monkeypatch):
    """Saving an edited resume must return its canonical profile and update only its selected match."""
    state = {
        "candidate": _candidate(),
        "recommendation_ids": ["rec-1"],
        "refresh_calls": [],
    }

    async def get_guidance(candidate_id, rec_id):
        assert (candidate_id, rec_id) == ("candidate-1", "rec-1")
        return {"match_score": 90.0}

    async def get_job_context(candidate_id, rec_id):
        assert (candidate_id, rec_id) == ("candidate-1", "rec-1")
        return {
            "skills": ["Python", "Rust"],
            "requirements": "",
            "experience_required": None,
        }

    async def save_updates(candidate_id, updates):
        assert candidate_id == "candidate-1"
        assert "Rust" in updates["skills"]
        state["candidate"] = {
            **state["candidate"],
            "skills": ["Python", "FastAPI", "PostgreSQL", "Rust"],
        }
        return {"updated": True, "changed": ["skills"]}

    async def get_candidate(candidate_id):
        assert candidate_id == "candidate-1"
        return state["candidate"]

    async def get_profile(candidate_id):
        assert candidate_id == "candidate-1"
        # Model the canonical GET /profile payload, not parsed_resume_json.
        return server._normalize_for_frontend(state["candidate"])

    async def refresh_match(candidate_id, rec_id, candidate, _session_factory):
        state["refresh_calls"].append((candidate_id, rec_id, candidate["skills"]))
        assert candidate_id == "candidate-1"
        assert rec_id == "rec-1"
        assert "Rust" in candidate["skills"]
        # Updating the existing selected recommendation must not insert one.
        state["match_score"] = 84.0

    class _ScoreResult:
        def scalar(self):
            return state["match_score"]

    class _ScoreSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def execute(self, _statement, params=None):
            assert params == {"rid": "rec-1", "cid": "candidate-1"}
            return _ScoreResult()

    import candidate_job_matching_service
    monkeypatch.setattr(server, "get_job_match_improvement", get_guidance)
    monkeypatch.setattr(server, "_get_job_match_improvement_row", get_job_context)
    monkeypatch.setattr(server, "_save_resume_editor_updates", save_updates)
    monkeypatch.setattr(server, "_get_candidate_row", get_candidate)
    monkeypatch.setattr(server, "_get_candidate_profile_payload", get_profile)
    monkeypatch.setattr(server, "SessionLocal", lambda: _ScoreSession())
    monkeypatch.setattr(candidate_job_matching_service, "refresh_candidate_job_match", refresh_match)

    response = asyncio.run(server.improve_job_match(
        "candidate-1",
        "rec-1",
        server.JobMatchImprovementRequest(profile_updates={"skills": ["Python", "Rust"]}),
    ))

    assert response["match_score"] == 84.0
    assert response["profile"]
    assert "Rust" in response["profile"]["keySkills"]
    # The persisted Resume Editor skill is immediately used for refreshed
    # selected-job gap guidance.
    assert response["remaining_missing_skills"] == []
    assert state["refresh_calls"] == [("candidate-1", "rec-1", ["Python", "FastAPI", "PostgreSQL", "Rust"])]
    assert state["recommendation_ids"] == ["rec-1"]
