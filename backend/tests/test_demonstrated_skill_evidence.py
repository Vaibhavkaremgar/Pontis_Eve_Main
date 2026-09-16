"""Regression coverage for direct Chat/Voice demonstrated-skill evidence."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from profile_strength_service import (
    EVIDENCE_CLAIMED,
    EVIDENCE_DEMONSTRATED,
    build_attribute_evidence,
    calculate_profile_strength_v2,
)
from server import _append_demonstrated_skill_evidence, _existing_skills_explicitly_used


STATEMENT = "I personally used Python and FastAPI to build REST APIs for our job aggregation system."


def _profile(raw=None):
    return {
        "name": "Jane Doe", "email": "jane@example.com", "location": "Remote",
        "current_role": "Backend Engineer", "skills": ["Python", "FastAPI", "PostgreSQL"],
        "work_experience": [{"title": "Engineer", "company": "Acme", "description": "Built services."}],
        "raw_data": raw or {},
    }


def test_chat_answer_explicitly_describing_existing_skill_usage_is_demonstrated():
    raw = _append_demonstrated_skill_evidence({}, _profile()["skills"], STATEMENT, "eve_chat")
    evidence = build_attribute_evidence(_profile(raw))
    assert raw["demonstrated_skill_evidence"][0]["skills"] == ["Python", "FastAPI"]
    assert evidence["skills"]["evidence_level"] == EVIDENCE_DEMONSTRATED


def test_voice_answer_explicitly_describing_existing_skill_usage_is_demonstrated():
    raw = _append_demonstrated_skill_evidence({}, _profile()["skills"], STATEMENT, "eve_voice")
    evidence = build_attribute_evidence(_profile(raw))
    assert raw["demonstrated_skill_evidence"][0]["source"] == "eve_voice"
    assert evidence["skills"]["evidence_level"] == EVIDENCE_DEMONSTRATED


def test_only_explicitly_supported_existing_skills_become_demonstrated():
    assert _existing_skills_explicitly_used(
        ["Python", "FastAPI", "PostgreSQL"], STATEMENT
    ) == ["Python", "FastAPI"]
    assert _existing_skills_explicitly_used(
        ["Python", "FastAPI"], "Project technologies: Python, FastAPI."
    ) == []


def test_resume_only_skills_remain_claimed():
    evidence = build_attribute_evidence(_profile())
    assert evidence["skills"]["evidence_level"] == EVIDENCE_CLAIMED


def test_evidence_does_not_create_duplicate_skills():
    raw = _append_demonstrated_skill_evidence({}, _profile()["skills"], STATEMENT, "eve_chat")
    repeated = _append_demonstrated_skill_evidence(raw, _profile()["skills"], STATEMENT, "eve_chat")
    assert _profile()["skills"] == ["Python", "FastAPI", "PostgreSQL"]
    assert len(repeated["demonstrated_skill_evidence"]) == 1


def test_existing_formula_increases_skills_and_overall_score():
    claimed = _profile()
    demonstrated = _profile(_append_demonstrated_skill_evidence({}, claimed["skills"], STATEMENT, "eve_chat"))
    before = calculate_profile_strength_v2(claimed, claimed["raw_data"])
    after = calculate_profile_strength_v2(demonstrated, demonstrated["raw_data"])
    assert after["dimensions"]["skills_capability"]["score"] > before["dimensions"]["skills_capability"]["score"]
    assert after["percent"] > before["percent"]
