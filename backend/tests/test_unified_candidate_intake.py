import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import pytest
from fastapi import HTTPException

import server


def test_cross_candidate_session_is_rejected():
    candidate_a = "11111111-1111-1111-1111-111111111111"
    candidate_b = "22222222-2222-2222-2222-222222222222"
    token = server._issue_candidate_session_token(candidate_a)
    with pytest.raises(HTTPException) as exc:
        server._authorize_candidate(candidate_b, f"Bearer {token}")
    assert exc.value.status_code == 403


def test_matching_candidate_session_is_accepted():
    candidate_id = "11111111-1111-1111-1111-111111111111"
    token = server._issue_candidate_session_token(candidate_id)
    assert server._authorize_candidate(candidate_id, f"Bearer {token}") is None


@pytest.mark.parametrize("wording", [
    "What is your current role?",
    "What role are you currently working in?",
    "Tell me about your current position.",
])
def test_rephrased_role_questions_have_stable_topic(wording):
    assert server._stable_intake_topic(wording) == "current_role"


def test_vague_experience_answer_is_partial():
    assert server._intake_answer_status("experience_years", "I've worked for a few years") == "PARTIALLY_ANSWERED"
    assert server._intake_answer_status("experience_years", "I have 4 years of experience") == "ANSWERED"


def test_profile_strength_override_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("EVE_ENABLE_PROFILE_STRENGTH_TEST_OVERRIDE", raising=False)
    original = {"percent": 62, "label": "Developing", "profile_strength": {"percent": 62}}
    candidate = {"id": "53a744f8-3292-4339-8533-f9a2f2f93e96"}
    assert server._apply_profile_strength_test_override(candidate, original) is original
