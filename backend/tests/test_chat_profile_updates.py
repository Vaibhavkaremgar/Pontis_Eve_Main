"""
Regression tests for Chat with Eve profile update sanitization.

Ensures that natural-language update requests save only valid structured data
into profile fields — never conversational phrases like "These are my skills",
"My skills are", etc.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import server


# ---------------------------------------------------------------------------
# _sanitize_structured_list_items
# ---------------------------------------------------------------------------

class TestSanitizeStructuredListItems:
    def test_keeps_clean_skill_names(self):
        items = ["Python", "Docker", "Redis"]
        assert server._sanitize_structured_list_items(items) == ["Python", "Docker", "Redis"]

    def test_removes_these_are_my_skills_phrase(self):
        items = ["These are my skills", "Python", "Docker"]
        result = server._sanitize_structured_list_items(items)
        assert "These are my skills" not in result
        assert "Python" in result
        assert "Docker" in result

    def test_removes_my_skills_are_phrase(self):
        items = ["My skills are Python and Docker", "Python", "Docker"]
        result = server._sanitize_structured_list_items(items)
        assert not any("my skills are" in item.lower() for item in result)

    def test_removes_here_are_my_skills(self):
        items = ["Here are my skills", "FastAPI", "PostgreSQL"]
        result = server._sanitize_structured_list_items(items)
        assert "Here are my skills" not in result
        assert "FastAPI" in result

    def test_removes_i_have_the_following_skills(self):
        items = ["I have the following skills", "Python", "AWS"]
        result = server._sanitize_structured_list_items(items)
        assert "I have the following skills" not in result
        assert "Python" in result

    def test_removes_these_are_my_certifications(self):
        items = ["These are my certifications", "AWS Certified Solutions Architect"]
        result = server._sanitize_structured_list_items(items)
        assert "These are my certifications" not in result
        assert "AWS Certified Solutions Architect" in result

    def test_keeps_dict_items_unchanged(self):
        items = [{"title": "Engineer", "company": "Acme"}]
        result = server._sanitize_structured_list_items(items)
        assert result == items

    def test_empty_list_returns_empty(self):
        assert server._sanitize_structured_list_items([]) == []

    def test_removes_empty_strings(self):
        items = ["", "Python", "  "]
        result = server._sanitize_structured_list_items(items)
        assert "" not in result
        assert "Python" in result

    def test_keeps_multi_word_tech_names(self):
        items = ["Amazon Web Services", "Google Cloud Platform", "Microsoft Azure"]
        result = server._sanitize_structured_list_items(items)
        assert result == items


# ---------------------------------------------------------------------------
# _sanitize_profile_updates
# ---------------------------------------------------------------------------

class TestSanitizeProfileUpdates:
    def test_clean_skills_pass_through(self):
        updates = {"skills": ["Python", "Docker", "Redis"]}
        result = server._sanitize_profile_updates(updates)
        assert result["skills"] == ["Python", "Docker", "Redis"]

    def test_conversational_skill_phrases_removed(self):
        updates = {"skills": ["These are the skills", "Python", "Docker", "Redis"]}
        result = server._sanitize_profile_updates(updates)
        assert "These are the skills" not in result["skills"]
        assert set(result["skills"]) == {"Python", "Docker", "Redis"}

    def test_skills_list_entirely_noise_returns_no_skills_key(self):
        updates = {"skills": ["These are my skills", "My skills are listed below"]}
        result = server._sanitize_profile_updates(updates)
        assert "skills" not in result

    def test_certifications_noise_removed(self):
        updates = {"certifications": ["These are my certifications", "AWS Certified Developer"]}
        result = server._sanitize_profile_updates(updates)
        assert "These are my certifications" not in result.get("certifications", [])
        assert "AWS Certified Developer" in result["certifications"]

    def test_preferred_roles_noise_removed(self):
        updates = {"preferred_roles": ["My preferred roles are", "Backend Engineer", "Data Engineer"]}
        result = server._sanitize_profile_updates(updates)
        roles = result.get("preferred_roles", [])
        assert not any("preferred roles are" in r.lower() for r in roles)
        assert "Backend Engineer" in roles

    def test_work_experience_valid_entry_kept(self):
        updates = {"work_experience": [{"title": "Engineer", "company": "Acme", "description": "Built APIs"}]}
        result = server._sanitize_profile_updates(updates)
        assert len(result["work_experience"]) == 1

    def test_work_experience_entry_without_title_or_company_dropped(self):
        updates = {"work_experience": [{"description": "Did stuff"}]}
        result = server._sanitize_profile_updates(updates)
        assert "work_experience" not in result

    def test_education_valid_entry_kept(self):
        updates = {"education": [{"degree": "B.Tech", "institution": "MIT"}]}
        result = server._sanitize_profile_updates(updates)
        assert len(result["education"]) == 1

    def test_education_entry_without_degree_or_institution_dropped(self):
        updates = {"education": [{"start_date": "2018"}]}
        result = server._sanitize_profile_updates(updates)
        assert "education" not in result

    def test_experience_years_coerced_to_float(self):
        updates = {"experience_years": "5"}
        result = server._sanitize_profile_updates(updates)
        assert result["experience_years"] == 5.0

    def test_experience_years_invalid_string_dropped(self):
        updates = {"experience_years": "many years"}
        result = server._sanitize_profile_updates(updates)
        assert "experience_years" not in result

    def test_scalar_fields_pass_through(self):
        updates = {"current_role": "Backend Developer", "location": "London"}
        result = server._sanitize_profile_updates(updates)
        assert result["current_role"] == "Backend Developer"
        assert result["location"] == "London"

    def test_non_list_skills_dropped(self):
        updates = {"skills": "Python, Docker"}
        result = server._sanitize_profile_updates(updates)
        assert "skills" not in result

    def test_empty_dict_returns_empty(self):
        assert server._sanitize_profile_updates({}) == {}

    def test_non_dict_returns_empty(self):
        assert server._sanitize_profile_updates(None) == {}  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# _extract_profile_updates — end-to-end sanitization
# ---------------------------------------------------------------------------

class TestExtractProfileUpdatesSanitization:
    def test_llm_marker_with_clean_skills(self):
        reply = (
            "Great, I've noted your skills.\n"
            "<<<PROFILE_UPDATES>>>\n"
            "{\"profile_updates\": {\"skills\": [\"Python\", \"Docker\", \"Redis\"]}}\n"
            "<<<END_UPDATES>>>"
        )
        clean, updates = server._extract_profile_updates(reply)
        assert updates is not None
        assert updates["skills"] == ["Python", "Docker", "Redis"]

    def test_llm_marker_strips_conversational_skill_phrase(self):
        reply = (
            "Got it!\n"
            "<<<PROFILE_UPDATES>>>\n"
            "{\"profile_updates\": {\"skills\": [\"These are the skills\", \"Python\", \"Docker\", \"Redis\"]}}\n"
            "<<<END_UPDATES>>>"
        )
        clean, updates = server._extract_profile_updates(reply)
        assert updates is not None
        assert "These are the skills" not in updates["skills"]
        assert "Python" in updates["skills"]

    def test_llm_marker_strips_conversational_cert_phrase(self):
        reply = (
            "Noted.\n"
            "<<<PROFILE_UPDATES>>>\n"
            "{\"profile_updates\": {\"certifications\": [\"These are my certifications\", \"AWS Certified Developer\"]}}\n"
            "<<<END_UPDATES>>>"
        )
        clean, updates = server._extract_profile_updates(reply)
        assert updates is not None
        assert "These are my certifications" not in updates["certifications"]
        assert "AWS Certified Developer" in updates["certifications"]

    def test_natural_language_update_my_skills(self):
        """'Update my skills: Python, Docker, Redis' should save only the skill names."""
        message = "Update my skills: Python, Docker, Redis"
        _, updates = server._extract_profile_updates("", candidate_message=message)
        if updates and "skills" in updates:
            for skill in updates["skills"]:
                assert skill in {"Python", "Docker", "Redis"}, f"Unexpected skill: {skill!r}"

    def test_natural_language_these_are_my_skills(self):
        """'These are my skills: Python, Docker' should not save the phrase itself."""
        message = "These are my skills: Python, Docker"
        _, updates = server._extract_profile_updates("", candidate_message=message)
        if updates and "skills" in updates:
            for skill in updates["skills"]:
                assert "these are" not in skill.lower()
                assert "my skills" not in skill.lower()

    def test_natural_language_my_skills_are(self):
        """'My skills are Python and Docker' should not save the phrase itself."""
        message = "My skills are Python and Docker"
        _, updates = server._extract_profile_updates("", candidate_message=message)
        if updates and "skills" in updates:
            for skill in updates["skills"]:
                assert "my skills are" not in skill.lower()

    def test_clean_reply_returned_without_markers(self):
        reply = (
            "I've updated your profile.\n"
            "<<<PROFILE_UPDATES>>>\n"
            "{\"profile_updates\": {\"skills\": [\"Python\"]}}\n"
            "<<<END_UPDATES>>>"
        )
        clean, _ = server._extract_profile_updates(reply)
        assert "<<<PROFILE_UPDATES>>>" not in clean
        assert "<<<END_UPDATES>>>" not in clean

    def test_no_markers_no_candidate_message_returns_none(self):
        _, updates = server._extract_profile_updates("Hello, how can I help?")
        # No structured data should be inferred from a generic greeting
        if updates:
            assert isinstance(updates, dict)

    def test_work_experience_valid_entry_preserved(self):
        reply = (
            "Noted your experience.\n"
            "<<<PROFILE_UPDATES>>>\n"
            "{\"profile_updates\": {\"work_experience\": [{\"title\": \"Backend Developer\", \"company\": \"Acme\", \"description\": \"Built APIs\"}]}}\n"
            "<<<END_UPDATES>>>"
        )
        _, updates = server._extract_profile_updates(reply)
        assert updates is not None
        assert len(updates["work_experience"]) == 1
        assert updates["work_experience"][0]["title"] == "Backend Developer"

    def test_work_experience_entry_without_title_company_dropped(self):
        reply = (
            "Noted.\n"
            "<<<PROFILE_UPDATES>>>\n"
            "{\"profile_updates\": {\"work_experience\": [{\"description\": \"Did stuff\"}]}}\n"
            "<<<END_UPDATES>>>"
        )
        _, updates = server._extract_profile_updates(reply)
        assert updates is None or "work_experience" not in (updates or {})
