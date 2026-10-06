"""Pure deterministic validation for the production matching factors."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import candidate_job_matching_service as matcher
from location_matching import candidate_location, country_eligible, location_preference_score


INDIA_CANDIDATE = {
    "location": "Hyderabad, India",
    "raw_data": {"country": "India", "country_code": "IN", "city": "Hyderabad"},
}


@pytest.mark.parametrize(
    ("job", "eligible"),
    [
        ({"country": "India"}, True),
        ({"country": "United States"}, False),
        ({"country": "United Kingdom"}, False),
        ({"country": "", "remote_policy": "global"}, True),
        ({"country": ""}, False),
    ],
)
def test_country_boundary_matrix(job, eligible):
    assert country_eligible(INDIA_CANDIDATE, job) is eligible


def test_city_only_never_infers_country_and_fails_closed():
    candidate = {"location": "Hyderabad", "raw_data": {}}
    assert candidate_location(candidate) == {
        "country_code": "", "city": "hyderabad", "state": "", "preferred": ""
    }
    assert not country_eligible(candidate, {"country": "India"})


@pytest.mark.parametrize(
    ("job", "score", "eligible"),
    [
        ({"city": "Hyderabad", "country": "India"}, 1.0, True),
        ({"city": "Bangalore", "country": "India"}, 0.5, True),
        ({"city": "Pune", "country": "India"}, 0.5, True),
        ({"country": "India", "remote": True}, 0.5, True),
        ({"country": "USA", "remote": True}, 0.0, False),
        ({"city": "Hyderabad", "country": "USA"}, 0.0, False),
    ],
)
def test_location_matrix(job, score, eligible):
    assert country_eligible(INDIA_CANDIDATE, job) is eligible
    assert location_preference_score(INDIA_CANDIDATE, job) == score


def test_skill_matrix_exact_partial_alias_missing_and_none():
    skills = ["Python", "FastAPI", "PostgreSQL", "Docker", "Qdrant"]
    intelligence = {"evidence": {"skills": {"evidence_level": 3}}}
    assert matcher._evidence_weighted_skills_score(
        skills, "", intelligence, skills
    ) == 1.0
    assert matcher._evidence_weighted_skills_score(
        skills, "", intelligence, ["Python", "FastAPI", "AWS", "Docker"]
    ) == 0.75
    assert matcher._evidence_weighted_skills_score(
        ["postgres"], "", intelligence, ["PostgreSQL"]
    ) == 1.0
    assert matcher._evidence_weighted_skills_score(
        skills, "", intelligence, ["Rust", "Kafka"]
    ) == 0.0


def test_claimed_skill_evidence_is_weighted_at_point_six():
    assert matcher._evidence_weighted_skills_score(
        ["Python"], "", {"evidence": {"skills": {"evidence_level": 1}}}, ["Python"]
    ) == 0.6


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("AI Automation Engineer", 1.0),
        ("Python Backend Engineer", 1.0),
        ("Backend Developer", 0.5),
        ("Frontend Developer", 0.0),
        ("Data Analyst", 0.0),
    ],
)
def test_target_role_matrix(title, expected):
    roles = ["AI Automation Engineer", "Python Backend Engineer"]
    assert matcher._target_role_score(roles, title, title) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("requirement", "eligible", "fit"),
    [
        ("2-4 years of experience", True, 1.0),
        ("3-5 years of experience", True, 1.0),
        ("5-8 years of experience", False, pytest.approx(1 / 3)),
        ("0-2 years of experience", False, pytest.approx(2 / 3)),
        ("", True, 0.5),
    ],
)
def test_experience_matrix(requirement, eligible, fit):
    assert matcher.experience_eligibility(3.0, requirement)["eligible"] is eligible
    assert matcher._experience_fit_score(3.0, requirement) == fit


def test_employment_type_filter_matrix():
    signals = {"employment_types": ["Full-time"], "_candidate": INDIA_CANDIDATE}
    base = {"country": "India", "title": "Python Engineer", "description": "Python"}
    for value, expected in [("Full-time", True), ("Contract", False), ("Part-time", False), ("", True)]:
        job = {**base, "employment_type": value}
        assert matcher._preference_eligibility(signals, job, 3.0)[0] is expected


def test_remote_only_does_not_bypass_country_and_rejects_onsite_or_hybrid():
    signals = {"remote_preference": "Remote only", "_candidate": INDIA_CANDIDATE}
    base = {"title": "Python Engineer", "description": "Python"}
    assert matcher._preference_eligibility(signals, {**base, "country": "India", "remote": True}, 3.0)[0]
    assert not matcher._preference_eligibility(signals, {**base, "country": "India", "remote_policy": "onsite"}, 3.0)[0]
    assert not matcher._preference_eligibility(signals, {**base, "country": "India", "remote_policy": "hybrid"}, 3.0)[0]
    assert not matcher._preference_eligibility(signals, {**base, "country": "USA", "remote": True}, 3.0)[0]


def test_relevance_gate_skill_or_role():
    signals = {"skills": ["Python"], "target_roles": ["Backend Engineer"]}
    assert matcher._job_passes_skills_or_role(signals, "Unrelated", "Python services")
    assert matcher._job_passes_skills_or_role(signals, "Backend Engineer", "No matching technology")
    assert not matcher._job_passes_skills_or_role(signals, "Data Analyst", "SQL reporting")


def test_relevant_history_requires_complete_role_phrase():
    roles = ["Python Developer", "Backend Engineer", "AI Automation Engineer"]
    assert matcher._experience_score(roles, "Python Developer", "") == 1.0
    assert matcher._experience_score(roles, "Backend Developer", "") == 0.0
    assert matcher._experience_score(roles, "Data Analyst", "") == 0.0


def test_hybrid_formula_and_semantic_weight(monkeypatch):
    monkeypatch.setattr(matcher, "_target_role_score", lambda *args: 1.0)
    monkeypatch.setattr(matcher, "_evidence_weighted_skills_score", lambda *args: 1.0)
    monkeypatch.setattr(matcher, "_experience_score", lambda *args: 1.0)
    monkeypatch.setattr(matcher, "_experience_fit_score", lambda *args: 1.0)
    monkeypatch.setattr(matcher, "_preference_score", lambda *args: (1.0, {}))
    monkeypatch.setattr(matcher, "_check_hard_constraints", lambda *args: (1.0, []))
    monkeypatch.setattr(matcher, "_extract_job_required_skills", lambda *args: [])
    monkeypatch.setattr(matcher, "_compute_job_specific_confidence", lambda *args: {})
    signals = {"target_roles": [], "skills": [], "past_roles": [], "total_experience_years": 3}
    full, _ = matcher._hybrid_score(signals, "", "", "", [], 1.0)
    lower_semantic, _ = matcher._hybrid_score(signals, "", "", "", [], 0.3)
    assert full == pytest.approx(1.0)
    assert full - lower_semantic == pytest.approx(0.10 * 0.7)


def test_constraint_penalty_multiplies_final_score(monkeypatch):
    monkeypatch.setattr(matcher, "_target_role_score", lambda *args: 1.0)
    monkeypatch.setattr(matcher, "_evidence_weighted_skills_score", lambda *args: 1.0)
    monkeypatch.setattr(matcher, "_experience_score", lambda *args: 1.0)
    monkeypatch.setattr(matcher, "_experience_fit_score", lambda *args: 1.0)
    monkeypatch.setattr(matcher, "_preference_score", lambda *args: (1.0, {}))
    monkeypatch.setattr(matcher, "_check_hard_constraints", lambda *args: (0.5, ["test"]))
    monkeypatch.setattr(matcher, "_extract_job_required_skills", lambda *args: [])
    monkeypatch.setattr(matcher, "_compute_job_specific_confidence", lambda *args: {})
    signals = {"target_roles": [], "skills": [], "past_roles": [], "total_experience_years": 3}
    score, components = matcher._hybrid_score(signals, "", "", "", [], 1.0)
    assert score == pytest.approx(0.5)
    assert components["constraint_penalty"] == 0.5


def test_ats_type_has_no_scoring_effect():
    signals = {"target_roles": ["Python Backend Engineer"], "skills": ["Python"], "past_roles": [], "total_experience_years": 3}
    common = {"country": "India", "employment_type": "Full-time"}
    fantastic = matcher._preference_score(signals, {**common, "ats_type": "fantastic"})
    other = matcher._preference_score(signals, {**common, "ats_type": "greenhouse"})
    assert fantastic == other
