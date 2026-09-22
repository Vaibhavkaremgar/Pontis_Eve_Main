"""Regression coverage for Resume Editor -> canonical profile synchronization."""
import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import server


@pytest.fixture(autouse=True)
def _use_test_updated_resume_directory(monkeypatch, tmp_path):
    """Keep Resume Editor PDF artifacts out of the development document store."""
    monkeypatch.setattr(
        server, "_updated_resume_pdf_path",
        lambda candidate_id: tmp_path / candidate_id / "updated_resume.pdf",
    )


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


def test_skill_normalization_splits_reported_separator_less_skills_and_deduplicates():
    skills = server._normalize_skills([
        "HTML5 Express.js FlaskCSS3",
        "ExpressJS",
        "CSS3",
    ])

    assert skills == ["HTML5", "Express.js", "Flask", "CSS3"]
    assert server._job_missing_requirements(
        ["HTML5", "Express.js", "Flask", "CSS3"],
        "",
        {"skills": skills},
    )["missing_skills"] == []


def test_skill_normalization_repairs_exact_css3_opencv_legacy_case():
    assert server._normalize_skills(["CSS3", "CSS3 OpenCV Computer Vision"]) == [
        "CSS3", "OpenCV", "Computer Vision",
    ]
    assert server._normalize_skills(["Computer Vision"]) == ["Computer Vision"]


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


def test_resume_editor_save_keeps_new_comma_separated_multi_word_skills_as_distinct_candidates_skills(monkeypatch):
    """The save payload, DB column, parsed resume, and gap refresh share one array."""
    state = {}

    async def get_candidate(_candidate_id):
        return _candidate()

    monkeypatch.setattr(server, "_get_candidate_row", get_candidate)
    monkeypatch.setattr(server, "SessionLocal", _SessionFactory(state))

    asyncio.run(server._save_resume_editor_updates("candidate-1", {
        "skills": [
            "OpenCV, Computer Vision, Responsive Web Development, Data Structures",
            "opencv",
        ],
    }))

    saved_skills = json.loads(state["params"]["skills"])
    saved_parse = json.loads(state["params"]["parsed_resume_json"])
    assert saved_skills == [
        "OpenCV", "Computer Vision", "Responsive Web Development", "Data Structures",
    ]
    assert saved_parse["skills"] == saved_skills
    assert server._job_missing_requirements(saved_skills, "", {"skills": saved_skills})["missing_skills"] == []


def test_resume_editor_save_canonicalizes_new_combined_skills_and_refreshes_gaps(monkeypatch):
    """New editor input must be canonical before it reaches candidates.skills."""
    state = {}

    async def get_candidate(_candidate_id):
        return _candidate()

    monkeypatch.setattr(server, "_get_candidate_row", get_candidate)
    monkeypatch.setattr(server, "SessionLocal", _SessionFactory(state))

    # This models the one contentEditable value sent by Resume Editor, rather
    # than repairing a pre-existing database row.
    asyncio.run(server._save_resume_editor_updates("candidate-1", {
        "skills": ["HTML5 Express.js FlaskCSS3"],
    }))

    saved_skills = json.loads(state["params"]["skills"])
    assert saved_skills[-4:] == ["HTML5", "Express.js", "Flask", "CSS3"]
    assert all(skill != "HTML5 Express.js FlaskCSS3" for skill in saved_skills)
    assert server._job_missing_requirements(
        ["HTML5", "Express.js", "Flask", "CSS3"], "", {"skills": saved_skills}
    )["missing_skills"] == []


def test_new_skill_input_supports_all_editor_separators_without_splitting_phrases():
    assert server._normalize_skills(["HTML5", "Express.js", "Flask", "CSS3"]) == [
        "HTML5", "Express.js", "Flask", "CSS3",
    ]
    assert server._normalize_skills([
        "HTML5, Express.js; Flask\n• CSS3|HTML5",
        "Google Cloud Platform",
        "Frontend Development",
        "Object-Oriented Programming",
        "Problem Solving",
    ]) == [
        "HTML5", "Express.js", "Flask", "CSS3", "Google Cloud Platform",
        "Frontend Development", "Object-Oriented Programming", "Problem Solving",
    ]


def test_resume_editor_save_does_not_restore_legacy_members_omitted_by_editor(monkeypatch):
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
    assert saved_skills == ["React.js", "Node.js", "HTML5", "SQL"]
    assert server._job_missing_requirements(saved_skills, "", {"skills": saved_skills})["missing_skills"] == []


def test_resume_editor_save_repairs_exact_legacy_case_before_persisting(monkeypatch):
    state = {}

    async def get_candidate(_candidate_id):
        candidate = _candidate()
        candidate["skills"] = ["CSS3", "CSS3 OpenCV Computer Vision"]
        return candidate

    monkeypatch.setattr(server, "_get_candidate_row", get_candidate)
    monkeypatch.setattr(server, "SessionLocal", _SessionFactory(state))

    # This is the complete editor document returned from the legacy candidate.
    asyncio.run(server._save_resume_editor_updates("candidate-1", {
        "skills": ["CSS3", "CSS3 OpenCV Computer Vision"],
    }))

    assert json.loads(state["params"]["skills"]) == ["CSS3", "OpenCV", "Computer Vision"]


def test_missing_skills_uses_normalized_canonical_profile_skills():
    gaps = server._job_missing_requirements(
        ["React.js", "Node.js", "Type Script", "Docker", "React JS"],
        "",
        {"skills": [" reactjs ", "NODE JS", "TypeScript"]},
    )

    # Case, surrounding whitespace, and punctuation/spacing variants are
    # represented by the already-saved canonical candidate skills.
    assert gaps["missing_skills"] == ["Docker"]


def test_missing_skills_uses_selected_ats_job_skills_required_and_resume_evidence():
    """ATS jobs populate skills_required, not the legacy jd.skills column."""
    candidate = {
        "skills": ["Python"],
        "parsed_resume_json": json.dumps({"skills": ["React.js"]}),
        "raw_data": json.dumps({"skills": ["PostgreSQL"]}),
    }

    gaps = server._job_missing_requirements(
        [], "", candidate,
        skills_required="Python, React JS, PostgreSQL, Docker",
    )

    assert gaps["missing_skills"] == ["Docker"]


def test_missing_skills_unions_partial_structured_skills_with_rich_text_jd_requirements():
    """A real ATS-style row can declare only its primary language in JSON.

    The remaining explicitly required skills live in one Markdown JD string;
    they must not disappear merely because ``skills_required`` is non-empty.
    """
    description = """**Job Title: Java Developer**
**Required Qualifications:** * 2+ years of professional experience in Java development.
* Hands-on experience with the Spring Boot framework.
* Strong working knowledge of relational databases (e.g., MySQL, PostgreSQL).
**Why Join Us?** Build great products."""
    gaps = server._job_missing_requirements(
        None, None, {"skills": ["Java", "MySQL", "PostgreSQL"]},
        skills_required=["Java"], description=description,
        structured_data={"skills": ["Java"]},
    )
    assert "Spring Boot" in gaps["missing_skills"]


def test_missing_skills_uses_jd_requirement_list_when_selected_job_has_no_skills_column():
    gaps = server._job_missing_requirements(
        [], "Requirements: Python, FastAPI, Kubernetes", {"skills": ["Python"]}
    )

    assert gaps["missing_skills"] == ["FastAPI", "Kubernetes"]


def test_match_improvement_endpoint_extracts_competencies_from_live_failure_shape(monkeypatch):
    """Regression: the live Level AI recommendation had no structured skills.

    Its only declared skill list was a ``Competencies:`` line in ``description``;
    the GET endpoint must return those gaps to the Improve Your Match modal.
    """
    candidate = {
        "id": "candidate-live-shape",
        "skills": ["Python", "PostgreSQL", "SQL", "REST APIs", "Redis", "Docker"],
        "parsed_resume_json": "{}",
        "raw_data": "{}",
    }
    row = {
        "id": "job-live-shape",
        "match_score": 0.53,
        "title": "Senior Backend Engineer -PE",
        "job_url": "https://example.test/jobs/backend",
        "skills": None,
        "skills_required": [],
        "structured_data": {},
        "requirements": None,
        "experience_required": None,
        "description": (
            "About the role.\n\nCompetencies : Python, Django, "
            "Relational database understanding (viz: PostgreSQL, SQL), ETL, "
            "Database design, Strong experience in designing REST APIs, Cache, "
            "Redis, Celery, CI/CD, GCP, Kubernetes and Docker."
        ),
    }

    async def get_candidate(candidate_id):
        assert candidate_id == "candidate-live-shape"
        return candidate

    async def get_row(candidate_id, rec_id):
        assert (candidate_id, rec_id) == ("candidate-live-shape", "rec-live-shape")
        return row

    async def get_profile(_candidate_id):
        return {}

    monkeypatch.setattr(server, "_get_candidate_row", get_candidate)
    monkeypatch.setattr(server, "_get_job_match_improvement_row", get_row)
    monkeypatch.setattr(server, "_get_candidate_profile_payload", get_profile)

    response = asyncio.run(server.get_job_match_improvement("candidate-live-shape", "rec-live-shape"))

    assert response["missing_skills"]
    assert {"Django", "ETL", "Celery", "GCP", "Kubernetes"}.issubset(response["missing_skills"])
    assert "REST APIs" not in response["missing_skills"]
    assert not any("Relational database understanding" in skill for skill in response["missing_skills"])


def test_missing_skills_reads_nested_structured_required_skills_and_voice_evidence():
    candidate = {
        "skills": ["python"],
        "raw_data": json.dumps({"voice_intake": {"extracted": {"technical_skills": ["React JS"]}}}),
    }
    gaps = server._job_missing_requirements(
        [], "", candidate,
        structured_data={"job": {"qualification": {"required_skills": [{"name": "Python"}, {"name": "React.js"}, {"name": "Terraform"}]}}},
    )
    assert gaps["missing_skills"] == ["Terraform"]


def test_missing_skills_jd_fallback_ignores_non_skill_requirements():
    gaps = server._job_missing_requirements(
        [],
        "Responsibilities:\\nBuild reporting.\\nRequirements:\\n- SQL, Tableau, Airflow\\n- Remote in Canada; travel 20%; bachelor's degree required",
        {"parsed_resume_json": json.dumps({"technical_skills": ["sql"]})},
    )
    assert gaps["missing_skills"] == ["Tableau", "Airflow"]


def test_missing_skills_returns_empty_only_when_candidate_has_every_required_skill():
    gaps = server._job_missing_requirements(
        ["Go", "Docker", "Kubernetes"], "",
        {"skills": ["GO", "docker", "Kubernetes"]},
    )
    assert gaps["missing_skills"] == []


def test_missing_skills_handles_malformed_job_skill_data_without_non_skill_gaps():
    gaps = server._job_missing_requirements(
        "{not-json", "Requirements: location: London; salary: competitive; clearance required",
        {"skills": []}, structured_data={"metadata": {"country": "UK"}},
    )
    assert gaps["missing_skills"] == []


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


def test_updated_resume_download_serves_the_pdf_generated_by_resume_editor_save(monkeypatch):
    state = {}
    candidate = _candidate()

    async def get_candidate(_candidate_id):
        return candidate

    monkeypatch.setattr(server, "_get_candidate_row", get_candidate)
    monkeypatch.setattr(server, "SessionLocal", _SessionFactory(state))

    asyncio.run(server._save_resume_editor_updates("candidate-1", {"skills": ["Python", "FastAPI"]}))
    saved_raw = json.loads(state["params"]["raw_data"])
    candidate["raw_data"] = saved_raw
    response = asyncio.run(server.download_updated_resume("candidate-1"))

    assert response.path == saved_raw["updated_resume_file_path"]
    assert Path(response.path).read_bytes().startswith(b"%PDF")
    assert response.headers["content-type"] == "application/pdf"


def test_updated_resume_download_returns_404_when_generated_pdf_is_missing(monkeypatch, tmp_path):
    missing_path = tmp_path / "candidate-1" / "updated_resume.pdf"

    async def get_candidate(_candidate_id):
        return {"id": "candidate-1", "name": "Candidate", "raw_data": {"updated_resume_file_path": str(missing_path)}}

    monkeypatch.setattr(server, "_get_candidate_row", get_candidate)
    monkeypatch.setattr(server, "_updated_resume_pdf_path", lambda _candidate_id: missing_path)

    with pytest.raises(server.HTTPException) as exc_info:
        asyncio.run(server.download_updated_resume("candidate-1"))

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Updated resume PDF not found."
