import asyncio
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
def test_historical_employer_is_not_promoted_to_current_job():
    extracted = {
        "current_role": "Software Engineer",
        "current_company": "Viral Bug",
        "work_experience": [{"title": "Software Engineer", "company": "Viral Bug"}],
    }
    validated = server._validate_voice_current_employment(
        extracted,
        "Candidate: I previously worked at Viral Bug as a Software Engineer.",
    )

    assert "current_role" not in validated
    assert "current_company" not in validated
    assert validated["work_experience"] == extracted["work_experience"]


def test_explicit_current_employer_can_update_current_job():
    extracted = {"current_role": "Software Engineer", "current_company": "Viral Bug"}
    validated = server._validate_voice_current_employment(
        extracted,
        "Assistant: What is your current role? Candidate: I am currently a Software Engineer at Viral Bug.",
    )

    assert validated["current_role"] == "Software Engineer"
    assert validated["current_company"] == "Viral Bug"


def test_authoritative_missing_questions_exclude_saved_answered_and_already_asked_topics(monkeypatch):
    async def fake_ledger(_candidate_id):
        return [
            {"topic_id": "expected_salary", "status": "ANSWERED"},
            {"topic_id": "notice_period", "status": "ASKED"},
        ]

    monkeypatch.setattr(server, "_load_intake_ledger", fake_ledger)
    candidate = {
        "id": "candidate-1",
        "current_role": "Engineer",
        "experience_years": 3,
        "skills": ["Python"],
        "education": [{"degree": "B.Tech"}],
        "raw_data": {"preferred_roles": ["Backend Engineer"]},
    }

    questions = asyncio.run(server._build_authoritative_missing_questions(candidate, {}))
    topics = {item["topic_id"] for item in questions}

    assert "current_role" not in topics
    assert "skills" not in topics
    assert "preferred_roles" not in topics
    assert "expected_salary" not in topics
    assert "notice_period" not in topics
    assert "preferred_locations" in topics


def test_certification_normalization_keeps_name_not_candidate_sentence():
    import server

    assert server._normalize_certifications([
        "yes i ahve N8N workflow automation engineer certification",
    ]) == ["N8N workflow automation engineer"]


def test_profile_question_reply_is_replaced_when_topic_is_not_eligible():
    reply = "What is your highest level of education?"
    eligible = [{"topic_id": "projects", "question": "Tell me about a project that best demonstrates your skills."}]
    assert server._validate_profile_question_reply(reply, eligible).startswith(
        "To complete your profile,"
    )
    assert "project that best demonstrates" in server._validate_profile_question_reply(reply, eligible)


def test_profile_question_reply_allows_eligible_topic_and_normal_conversation():
    eligible = [{"topic_id": "education", "question": "What's your highest level of education?"}]
    assert server._validate_profile_question_reply(
        "What's your highest level of education?", eligible
    ) == "What's your highest level of education?"
    assert server._validate_profile_question_reply("What do you think about this job?", []) == "What do you think about this job?"


def test_profile_question_reply_removes_only_ineligible_question_from_mixed_response():
    eligible = [{"topic_id": "projects", "question": "Tell me about a project that best demonstrates your skills."}]
    reply = "Your profile is looking good. What's your highest level of education? Tell me about a project that best demonstrates your skills?"
    result = server._validate_profile_question_reply(reply, eligible)
    assert "highest level of education" not in result
    assert "project that best demonstrates" in result
    assert "Your profile is looking good" in result


def test_profile_question_reply_with_no_eligible_topics_does_not_invent_one():
    result = server._validate_profile_question_reply("What's your education?", [])
    assert "education" not in result.lower()
    assert "What would you like to explore?" in result
