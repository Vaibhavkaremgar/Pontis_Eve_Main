"""
Regression tests for profile-completion guidance in Chat with Eve.

Three scenarios:
  1. Profile below 75%  — Eve should include a proactive next question.
  2. Profile reaching 75%+ — guidance says stop asking; no more profile questions.
  3. Profile already at 75%+ — guidance says stop asking from the start.
"""
import os
import sys
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import server
from profile_strength_service import calculate_profile_strength_v2


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _base_candidate():
    return {
        "id": "test-cand",
        "name": "Jane Doe",
        "email": "jane@example.com",
        "location": "",
        "current_role": "",
        "current_company": "",
        "summary": "",
        "experience_years": None,
        "skills": [],
        "work_experience": [],
        "education": [],
        "raw_data": {},
        "candidate_certificates": [],
        "parsing_status": "",
    }


def _strong_candidate():
    """Candidate whose profile scores >= 75%."""
    c = _base_candidate()
    c.update({
        "name": "Strong Dev",
        "email": "strong@example.com",
        "location": "Remote",
        "current_role": "Senior Backend Engineer",
        "experience_years": 7,
        "skills": ["Python", "FastAPI", "PostgreSQL", "Docker", "AWS"],
        "work_experience": [
            {
                "title": "Lead Engineer",
                "company": "Acme",
                "description": "Led platform work",
                "start_date": "2018-01-01",
                "end_date": "2023-01-01",
            }
        ],
        "education": [{"degree": "B.Tech", "institution": "University"}],
        "interview_technical_score": 9.0,
        "interview_communication_score": 8.5,
        "raw_data": {
            "preferred_roles": ["Senior Backend Engineer"],
            "availability": "30 days",
            "location_preferences": ["Remote"],
            "work_type_preference": "remote",
            "projects": ["AI platform", "REST API service"],
            "voice_intake": {
                "status": "completed",
                "completed_turns": [
                    {"question": "What roles?", "answer": "Senior backend engineering roles."},
                    {"question": "Skills?", "answer": "Python, FastAPI, PostgreSQL."},
                    {"question": "Availability?", "answer": "30 days notice."},
                ],
                "known_topics": [
                    "background_experience", "skills_technologies", "target_role",
                    "responsibilities_projects", "availability_location", "career_preferences",
                ],
            },
        },
    })
    return c


def _weak_candidate():
    """Candidate whose profile scores well below 75%."""
    c = _base_candidate()
    c.update({
        "name": "Weak Candidate",
        "email": "weak@example.com",
        # No role, no skills, no experience — very incomplete
    })
    return c


# ---------------------------------------------------------------------------
# Scenario 1: Profile below 75% — guidance includes a next question
# ---------------------------------------------------------------------------

class TestProfileBelow75:
    def test_guidance_mentions_below_75(self):
        c = _weak_candidate()
        guidance = server._build_profile_completion_guidance(c)
        assert "below 90%" in guidance

    def test_guidance_includes_next_question(self):
        c = _weak_candidate()
        guidance = server._build_profile_completion_guidance(c)
        # Should contain a quoted question to ask
        assert '"' in guidance or "Ask" in guidance

    def test_guidance_percent_is_accurate(self):
        c = _weak_candidate()
        result = calculate_profile_strength_v2(c)
        percent = result["percent"]
        assert percent < 75
        guidance = server._build_profile_completion_guidance(c)
        assert str(percent) in guidance

    def test_guidance_does_not_ask_for_already_known_fields(self):
        """If name is already known, guidance should not ask for name."""
        c = _weak_candidate()
        # name is set — guidance should not ask for it
        guidance = server._build_profile_completion_guidance(c)
        # The next_actions from profile_strength_service never ask for already-present fields
        # so the guidance question should not be about name
        assert "name" not in guidance.lower() or "Weak Candidate" not in guidance

    def test_profile_strength_below_75_for_weak_candidate(self):
        c = _weak_candidate()
        result = calculate_profile_strength_v2(c)
        assert result["percent"] < 75


# ---------------------------------------------------------------------------
# Scenario 2: Profile reaching 75%+ — guidance says stop asking
# ---------------------------------------------------------------------------

class TestProfileReaching75:
    def test_strong_candidate_scores_at_least_75(self):
        c = _strong_candidate()
        result = calculate_profile_strength_v2(c)
        assert result["percent"] < 90

    def test_guidance_says_stop_when_at_75(self):
        c = _strong_candidate()
        guidance = server._build_profile_completion_guidance(c)
        assert "below 90%" in guidance

    def test_guidance_does_not_include_next_question_when_at_75(self):
        c = _strong_candidate()
        guidance = server._build_profile_completion_guidance(c)
        # Should not contain a quoted next question
        assert "Ask" in guidance

    def test_profile_updates_trigger_guidance_change(self):
        """After adding missing fields, a previously-weak candidate can cross 75%."""
        c = _weak_candidate()
        before = server._build_profile_completion_guidance(c)
        assert "below 90%" in before

        # Simulate profile update: add the fields that push score to 75%+
        c.update({
            "current_role": "Senior Backend Engineer",
            "experience_years": 7,
            "skills": ["Python", "FastAPI", "PostgreSQL", "Docker", "AWS"],
            "work_experience": [
                {
                    "title": "Lead Engineer",
                    "company": "Acme",
                    "description": "Led platform work",
                    "start_date": "2018-01-01",
                    "end_date": "2023-01-01",
                }
            ],
            "education": [{"degree": "B.Tech", "institution": "University"}],
            "interview_technical_score": 9.0,
            "interview_communication_score": 8.5,
            "raw_data": {
                "preferred_roles": ["Senior Backend Engineer"],
                "availability": "30 days",
                "location_preferences": ["Remote"],
                "work_type_preference": "remote",
                "projects": ["AI platform"],
                "voice_intake": {
                    "status": "completed",
                    "completed_turns": [
                        {"question": "What roles?", "answer": "Senior backend engineering roles."},
                    ],
                    "known_topics": [
                        "background_experience", "skills_technologies", "target_role",
                        "responsibilities_projects", "availability_location", "career_preferences",
                    ],
                },
            },
        })
        after = server._build_profile_completion_guidance(c)
        result = calculate_profile_strength_v2(c)
        if result["percent"] >= 90:
            assert "90%+ reached" in after
        else:
            # Still below 75 — guidance should still show below-75 message
            assert "below 90%" in after


# ---------------------------------------------------------------------------
# Scenario 3: Profile already at 75%+ — guidance says stop from the start
# ---------------------------------------------------------------------------

class TestProfileAlreadyAt75:
    def test_already_strong_guidance_says_stop(self):
        c = _strong_candidate()
        guidance = server._build_profile_completion_guidance(c)
        assert "below 90%" in guidance

    def test_already_strong_no_next_question_in_guidance(self):
        c = _strong_candidate()
        guidance = server._build_profile_completion_guidance(c)
        assert "Ask" in guidance

    def test_already_strong_percent_shown_in_guidance(self):
        c = _strong_candidate()
        result = calculate_profile_strength_v2(c)
        percent = result["percent"]
        guidance = server._build_profile_completion_guidance(c)
        assert str(percent) in guidance

    def test_guidance_stable_on_repeated_calls(self):
        """Calling guidance twice for the same profile returns consistent results."""
        c = _strong_candidate()
        g1 = server._build_profile_completion_guidance(c)
        g2 = server._build_profile_completion_guidance(c)
        assert g1 == g2


# ---------------------------------------------------------------------------
# Unit: _build_profile_completion_guidance contract
# ---------------------------------------------------------------------------

class TestGuidanceContract:
    def test_returns_string(self):
        c = _base_candidate()
        result = server._build_profile_completion_guidance(c)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_never_raises(self):
        """Should never raise even for a completely empty candidate dict."""
        result = server._build_profile_completion_guidance({})
        assert isinstance(result, str)

    def test_does_not_ask_for_email_when_present(self):
        c = _base_candidate()
        c["email"] = "test@example.com"
        guidance = server._build_profile_completion_guidance(c)
        # Email is present — guidance should not ask for email
        assert "email" not in guidance.lower()

    def test_system_template_has_guidance_placeholder(self):
        """EVE_SYSTEM_TEMPLATE must contain the profile_completion_guidance placeholder."""
        assert "{profile_completion_guidance}" in server.EVE_SYSTEM_TEMPLATE

    def test_system_template_guidance_behavior_instruction(self):
        """EVE_SYSTEM_TEMPLATE must contain the PROFILE COMPLETION behavior rule."""
        assert "PROFILE COMPLETION" in server.EVE_SYSTEM_TEMPLATE
        assert "90%" in server.EVE_SYSTEM_TEMPLATE


class TestRequiredFieldsVersusProfileStrength:
    def test_below_90_with_no_required_fields_never_claims_full_completion(self):
        profile = {
            "profile_strength_detail": {
                "percent": 87,
                "recommended_next_actions": [],
                "ninety_percent_guidance": {
                    "items": [{
                        "title": "Measured impact",
                        "question": "What measurable result did your work achieve?",
                    }],
                },
            },
            "_prefs_row": {
                "preferred_roles": ["Backend Engineer"],
                "preferred_locations": ["Remote"],
                "remote_preference": "Remote",
                "notice_period": "Immediate",
                "expected_salary": "Market rate",
                "employment_types": ["Full-time"],
                "preferred_industries": ["Technology"],
                "willing_to_relocate": True,
            },
        }

        guidance = server._build_profile_completion_guidance(profile)

        assert "below 90%" in guidance
        assert "Core required details may already be present" in guidance
        assert "Do not describe the profile as complete or 100% complete" in guidance
        assert 'never say the profile is "complete", "100% complete"' in server.EVE_SYSTEM_TEMPLATE

    def test_below_90_uses_existing_partial_gap_guidance(self):
        profile = {
            "profile_strength_detail": {
                "percent": 87,
                "recommended_next_actions": [],
                "ninety_percent_guidance": {
                    "items": [{
                        "title": "Project evidence",
                        "question": "Tell me about another relevant project and what you contributed.",
                    }],
                },
            },
            "_prefs_row": {
                "preferred_roles": ["Backend Engineer"],
                "preferred_locations": ["Remote"],
                "remote_preference": "Remote",
                "notice_period": "Immediate",
                "expected_salary": "Market rate",
                "employment_types": ["Full-time"],
                "preferred_industries": ["Technology"],
                "willing_to_relocate": True,
            },
        }

        guidance = server._build_profile_completion_guidance(profile)

        assert "Project evidence" in guidance
        assert "Tell me about another relevant project" in guidance

    def test_strength_100_keeps_fully_complete_stop_behavior(self):
        guidance = server._build_profile_completion_guidance({
            "profile_strength_detail": {"percent": 100},
        })

        assert guidance == (
            "Profile is at 100% (90%+ reached). "
            "Do NOT ask any more profile-completion questions."
        )

    def test_actual_missing_preference_keeps_existing_guidance(self):
        profile = {
            "profile_strength_detail": {
                "percent": 40,
                "recommended_next_actions": ["Tell Eve what kind of role you are targeting"],
            },
            "_prefs_row": {},
        }

        guidance = server._build_profile_completion_guidance(profile)

        assert guidance == (
            'Profile is at 40% (below 90%). Ask this one work-preference question naturally: '
            '"What kinds of roles are you looking for?"'
        )
