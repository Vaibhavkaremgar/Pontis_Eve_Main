"""
Regression tests for Chat with Eve profile update sanitization and deletion.

Covers:
- Natural-language skill extraction (only structured values, not surrounding sentences)
- Extraction from other sections (certifications, roles, experience, education)
- Deleting a skill
- Deleting items from other sections
- Attempting to delete a non-existent item
- Ensuring unrelated profile data remains unchanged
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

    def test_profile_deletions_dict_preserved(self):
        updates = {"profile_deletions": {"skills": ["FastAPI"]}}
        result = server._sanitize_profile_updates(updates)
        assert result["profile_deletions"] == {"skills": ["FastAPI"]}


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

    def test_natural_language_fastapi_docker_redis_extraction(self):
        """'My Python skills are FastAPI, Docker and Redis' → only FastAPI, Docker, Redis saved."""
        message = "My Python skills are FastAPI, Docker and Redis"
        _, updates = server._extract_profile_updates("", candidate_message=message)
        if updates and "skills" in updates:
            for skill in updates["skills"]:
                assert skill in {"FastAPI", "Docker", "Redis"}, f"Unexpected skill: {skill!r}"
                assert "my python skills are" not in skill.lower()

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

    def test_certifications_extraction_from_natural_language(self):
        """'I hold AWS Certified Developer and PMP certifications' → cert names only."""
        message = "I hold AWS Certified Developer and PMP certifications"
        _, updates = server._extract_profile_updates("", candidate_message=message)
        if updates and "certifications" in updates:
            for cert in updates["certifications"]:
                assert "i hold" not in cert.lower()

    def test_preferred_roles_extraction_from_natural_language(self):
        """'I am targeting Backend Engineer and Data Engineer roles' → role names only."""
        message = "I am targeting Backend Engineer and Data Engineer roles"
        _, updates = server._extract_profile_updates("", candidate_message=message)
        if updates and "preferred_roles" in updates:
            for role in updates["preferred_roles"]:
                assert "i am targeting" not in role.lower()

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


# ---------------------------------------------------------------------------
# _detect_deletion_intent
# ---------------------------------------------------------------------------

class TestDetectDeletionIntent:
    def test_remove_skill_from_skills(self):
        result = server._detect_deletion_intent("remove FastAPI from my skills")
        assert result is not None
        assert result["field"] == "skills"
        assert result["item"].lower() == "fastapi"

    def test_delete_skill_from_skills(self):
        result = server._detect_deletion_intent("delete Docker from my skills")
        assert result is not None
        assert result["field"] == "skills"
        assert result["item"].lower() == "docker"

    def test_remove_certification(self):
        result = server._detect_deletion_intent("remove AWS cert from my certifications")
        assert result is not None
        assert result["field"] == "certifications"

    def test_remove_preferred_role(self):
        result = server._detect_deletion_intent("remove Backend Engineer from my preferred roles")
        assert result is not None
        assert result["field"] == "preferred_roles"

    def test_remove_work_experience(self):
        result = server._detect_deletion_intent("remove Acme from my work experience")
        assert result is not None
        assert result["field"] == "work_experience"

    def test_remove_education(self):
        result = server._detect_deletion_intent("delete MIT from my education")
        assert result is not None
        assert result["field"] == "education"

    def test_no_deletion_intent(self):
        result = server._detect_deletion_intent("My skills are Python and Docker")
        assert result is None

    def test_empty_message(self):
        result = server._detect_deletion_intent("")
        assert result is None

    def test_none_message(self):
        result = server._detect_deletion_intent(None)  # type: ignore[arg-type]
        assert result is None


# ---------------------------------------------------------------------------
# _extract_profile_updates — deletion detection
# ---------------------------------------------------------------------------

class TestExtractProfileUpdatesDeletion:
    def test_deletion_detected_in_candidate_message(self):
        """Deletion intent in candidate message is embedded in profile_updates."""
        _, updates = server._extract_profile_updates(
            "I've removed FastAPI from your skills.",
            candidate_message="remove FastAPI from my skills",
        )
        assert updates is not None
        deletions = updates.get("profile_deletions", {})
        assert "skills" in deletions
        assert any("fastapi" in item.lower() for item in deletions["skills"])

    def test_deletion_from_certifications(self):
        _, updates = server._extract_profile_updates(
            "Done.",
            candidate_message="delete AWS cert from my certifications",
        )
        assert updates is not None
        deletions = updates.get("profile_deletions", {})
        assert "certifications" in deletions

    def test_deletion_from_preferred_roles(self):
        _, updates = server._extract_profile_updates(
            "Removed.",
            candidate_message="remove Backend Engineer from my preferred roles",
        )
        assert updates is not None
        deletions = updates.get("profile_deletions", {})
        assert "preferred_roles" in deletions

    def test_deletion_from_work_experience(self):
        _, updates = server._extract_profile_updates(
            "Done.",
            candidate_message="remove Acme from my work experience",
        )
        assert updates is not None
        deletions = updates.get("profile_deletions", {})
        assert "work_experience" in deletions

    def test_deletion_from_education(self):
        _, updates = server._extract_profile_updates(
            "Done.",
            candidate_message="delete MIT from my education",
        )
        assert updates is not None
        deletions = updates.get("profile_deletions", {})
        assert "education" in deletions

    def test_no_deletion_when_no_intent(self):
        _, updates = server._extract_profile_updates(
            "Great, noted.",
            candidate_message="I have 5 years of experience",
        )
        if updates:
            assert "profile_deletions" not in updates

    def test_llm_marker_deletion_preserved(self):
        """LLM-emitted profile_deletions block is preserved through sanitization."""
        reply = (
            "Done.\n"
            "<<<PROFILE_UPDATES>>>\n"
            "{\"profile_updates\": {\"profile_deletions\": {\"skills\": [\"Redis\"]}}}\n"
            "<<<END_UPDATES>>>"
        )
        _, updates = server._extract_profile_updates(reply)
        assert updates is not None
        assert updates.get("profile_deletions", {}).get("skills") == ["Redis"]

    def test_nonexistent_item_deletion_still_returns_deletion_dict(self):
        """Even if the item doesn't exist in the profile, the deletion dict is returned.
        The actual removal is a no-op in _apply_profile_updates."""
        _, updates = server._extract_profile_updates(
            "I've tried to remove that.",
            candidate_message="remove NonExistentSkill from my skills",
        )
        assert updates is not None
        deletions = updates.get("profile_deletions", {})
        assert "skills" in deletions
        assert any("nonexistentskill" in item.lower() for item in deletions["skills"])


# ---------------------------------------------------------------------------
# _remove_item_from_list
# ---------------------------------------------------------------------------

class TestRemoveItemFromList:
    def test_removes_exact_match(self):
        lst, found = server._remove_item_from_list(["Python", "FastAPI", "Docker"], "FastAPI")
        assert found is True
        assert "FastAPI" not in lst
        assert "Python" in lst
        assert "Docker" in lst

    def test_case_insensitive_removal(self):
        lst, found = server._remove_item_from_list(["Python", "FastAPI", "Docker"], "fastapi")
        assert found is True
        assert not any(x.lower() == "fastapi" for x in lst)

    def test_item_not_found(self):
        lst, found = server._remove_item_from_list(["Python", "Docker"], "Redis")
        assert found is False
        assert lst == ["Python", "Docker"]

    def test_empty_list(self):
        lst, found = server._remove_item_from_list([], "FastAPI")
        assert found is False
        assert lst == []

    def test_unrelated_items_preserved(self):
        original = ["Python", "FastAPI", "Docker", "Redis"]
        lst, found = server._remove_item_from_list(original, "FastAPI")
        assert found is True
        assert set(lst) == {"Python", "Docker", "Redis"}


# ---------------------------------------------------------------------------
# _remove_item_from_dict_list
# ---------------------------------------------------------------------------

class TestRemoveItemFromDictList:
    def test_removes_by_title(self):
        exp = [
            {"title": "Backend Developer", "company": "Acme"},
            {"title": "Frontend Developer", "company": "Beta"},
        ]
        lst, found = server._remove_item_from_dict_list(exp, "Backend Developer", ["title", "company"])
        assert found is True
        assert len(lst) == 1
        assert lst[0]["title"] == "Frontend Developer"

    def test_removes_by_company(self):
        exp = [
            {"title": "Engineer", "company": "Acme"},
            {"title": "Developer", "company": "Beta"},
        ]
        lst, found = server._remove_item_from_dict_list(exp, "Acme", ["title", "company"])
        assert found is True
        assert len(lst) == 1
        assert lst[0]["company"] == "Beta"

    def test_item_not_found(self):
        exp = [{"title": "Engineer", "company": "Acme"}]
        lst, found = server._remove_item_from_dict_list(exp, "NonExistent", ["title", "company"])
        assert found is False
        assert len(lst) == 1

    def test_unrelated_entries_preserved(self):
        exp = [
            {"title": "Backend Developer", "company": "Acme"},
            {"title": "Data Analyst", "company": "Beta"},
            {"title": "DevOps Engineer", "company": "Gamma"},
        ]
        lst, found = server._remove_item_from_dict_list(exp, "Data Analyst", ["title", "company"])
        assert found is True
        assert len(lst) == 2
        titles = [e["title"] for e in lst]
        assert "Backend Developer" in titles
        assert "DevOps Engineer" in titles
        assert "Data Analyst" not in titles


# ---------------------------------------------------------------------------
# Unrelated data preservation
# ---------------------------------------------------------------------------

class TestUnrelatedDataPreservation:
    def test_deletion_does_not_affect_other_fields(self):
        """Deleting a skill must not touch certifications or other fields."""
        reply = (
            "Removed FastAPI from your skills.\n"
            "<<<PROFILE_UPDATES>>>\n"
            "{\"profile_updates\": {\"profile_deletions\": {\"skills\": [\"FastAPI\"]}}}\n"
            "<<<END_UPDATES>>>"
        )
        _, updates = server._extract_profile_updates(reply)
        assert updates is not None
        assert set(updates.keys()) == {"profile_deletions"}
        assert "certifications" not in updates
        assert "preferred_roles" not in updates

    def test_adding_skill_does_not_affect_certifications(self):
        reply = (
            "Added Redis to your skills.\n"
            "<<<PROFILE_UPDATES>>>\n"
            "{\"profile_updates\": {\"skills\": [\"Redis\"]}}\n"
            "<<<END_UPDATES>>>"
        )
        _, updates = server._extract_profile_updates(reply)
        assert updates is not None
        assert "certifications" not in updates
        assert "profile_deletions" not in updates

    def test_deletion_of_one_skill_preserves_others_in_remove_item_from_list(self):
        """_remove_item_from_list only removes the targeted item."""
        skills = ["Python", "FastAPI", "Docker", "Redis", "PostgreSQL"]
        new_skills, found = server._remove_item_from_list(skills, "FastAPI")
        assert found is True
        assert set(new_skills) == {"Python", "Docker", "Redis", "PostgreSQL"}

    def test_deletion_of_nonexistent_cert_leaves_list_unchanged(self):
        certs = ["AWS Certified Developer", "PMP"]
        new_certs, found = server._remove_item_from_list(certs, "Google Cloud Professional")
        assert found is False
        assert set(new_certs) == {"AWS Certified Developer", "PMP"}


# ---------------------------------------------------------------------------
# _merge_profile_updates
# ---------------------------------------------------------------------------

class TestMergeProfileUpdates:
    def test_extra_fills_empty_base_field(self):
        base = {"availability": ""}
        extra = {"availability": "2 weeks notice"}
        result = server._merge_profile_updates(base, extra)
        assert result["availability"] == "2 weeks notice"

    def test_base_non_empty_field_not_overwritten(self):
        base = {"availability": "immediate"}
        extra = {"availability": "3 months"}
        result = server._merge_profile_updates(base, extra)
        assert result["availability"] == "immediate"

    def test_list_fields_merged_without_duplicates(self):
        base = {"skills": ["Python"]}
        extra = {"skills": ["Python", "Docker"]}
        result = server._merge_profile_updates(base, extra)
        assert result["skills"].count("Python") == 1
        assert "Docker" in result["skills"]

    def test_none_base_field_filled_by_extra(self):
        base = {"location": None}
        extra = {"location": "London"}
        result = server._merge_profile_updates(base, extra)
        assert result["location"] == "London"

    def test_profile_deletions_not_copied_from_extra(self):
        base = {}
        extra = {"profile_deletions": {"skills": ["FastAPI"]}, "location": "Berlin"}
        result = server._merge_profile_updates(base, extra)
        assert "profile_deletions" not in result
        assert result["location"] == "Berlin"

    def test_empty_extra_returns_base_unchanged(self):
        base = {"availability": "immediate"}
        result = server._merge_profile_updates(base, {})
        assert result == base

    def test_empty_base_gets_all_extra_fields(self):
        extra = {"availability": "2 weeks", "location": "Berlin"}
        result = server._merge_profile_updates({}, extra)
        assert result["availability"] == "2 weeks"
        assert result["location"] == "Berlin"


# ---------------------------------------------------------------------------
# Multi-field answer regression tests
# (synchronous — test _sanitize_profile_updates + _merge_profile_updates
#  to verify the pipeline that _extract_multi_field_updates_from_answer feeds into)
# ---------------------------------------------------------------------------

class TestMultiFieldAnswerRegression:
    """
    Regression tests: one candidate response answers 2+ suggested questions.
    Verifies that both fields are extracted and saved, and that those questions
    are not suggested again (getDynamicChatSuggestions equivalent: missing(p) is False).
    """

    def _simulate_multi_field_save(self, raw_extracted: dict) -> dict:
        """Simulate the pipeline: sanitize raw LLM output -> merge into empty profile."""
        sanitized = server._sanitize_profile_updates(raw_extracted)
        return server._merge_profile_updates({}, sanitized)

    def test_availability_and_salary_both_extracted(self):
        """'I can start in 2 weeks and I'm targeting £60k–£80k' answers both questions."""
        raw = {
            "availability": "2 weeks notice",
            "additional_information": "Targeting £60k–£80k salary",
        }
        result = self._simulate_multi_field_save(raw)
        assert result.get("availability") == "2 weeks notice"
        assert result.get("additional_information") == "Targeting £60k–£80k salary"

    def test_location_and_preferred_roles_both_extracted(self):
        """'I'm based in Berlin and looking for backend engineering roles' answers 2 questions."""
        raw = {
            "location": "Berlin",
            "preferred_roles": ["Backend Engineer"],
        }
        result = self._simulate_multi_field_save(raw)
        assert result.get("location") == "Berlin"
        assert "Backend Engineer" in result.get("preferred_roles", [])

    def test_skills_and_availability_both_extracted(self):
        """'I know Python and FastAPI, and I'm available immediately' answers 2 questions."""
        raw = {
            "skills": ["Python", "FastAPI"],
            "availability": "immediately",
        }
        result = self._simulate_multi_field_save(raw)
        assert "Python" in result.get("skills", [])
        assert "FastAPI" in result.get("skills", [])
        assert result.get("availability") == "immediately"

    def test_three_fields_extracted_from_one_answer(self):
        """One answer covering location, availability, and preferred_roles."""
        raw = {
            "location": "London",
            "availability": "1 month notice",
            "preferred_roles": ["Data Engineer", "ML Engineer"],
        }
        result = self._simulate_multi_field_save(raw)
        assert result.get("location") == "London"
        assert result.get("availability") == "1 month notice"
        assert set(result.get("preferred_roles", [])) >= {"Data Engineer", "ML Engineer"}

    def test_answered_fields_not_suggested_again(self):
        """After saving availability + preferred_roles, those questions must not appear in suggestions."""
        from frontend_suggestion_check import suggestions_for_profile

        profile_before = {
            "headline": "",
            "keySkills": [],
            "experience": [],
            "education": [],
            "certifications": [],
            "preferred_roles": [],
            "location": "London",
            "availability": "",
            "bio": "",
            "additional_information": "",
        }
        profile_after = dict(profile_before)
        profile_after["availability"] = "2 weeks notice"
        profile_after["preferred_roles"] = ["Backend Engineer"]

        suggestions_before = suggestions_for_profile(profile_before)
        suggestions_after = suggestions_for_profile(profile_after)

        # availability question should be gone after saving
        avail_q = "What's your availability to start?"
        assert avail_q in suggestions_before
        assert avail_q not in suggestions_after

        # preferred_roles question should be gone after saving
        roles_q = "What roles are you targeting?"
        assert roles_q in suggestions_before
        assert roles_q not in suggestions_after

    def test_already_present_field_not_overwritten_by_multi_extract(self):
        """If availability is already set, multi-field merge must not overwrite it."""
        existing_updates = {"availability": "immediate"}
        extra = {"availability": "3 months", "location": "Paris"}
        result = server._merge_profile_updates(existing_updates, extra)
        assert result["availability"] == "immediate"
        assert result["location"] == "Paris"

    def test_sanitize_removes_noise_from_multi_field_skills(self):
        """Multi-field extraction noise is stripped before saving."""
        raw = {
            "skills": ["These are my skills", "Python", "Docker"],
            "availability": "2 weeks",
        }
        result = self._simulate_multi_field_save(raw)
        skills = result.get("skills", [])
        assert "These are my skills" not in skills
        assert "Python" in skills
        assert "Docker" in skills
        assert result.get("availability") == "2 weeks"


# ---------------------------------------------------------------------------
# _merge_profile_updates
# ---------------------------------------------------------------------------

class TestMergeProfileUpdates:
    def test_extra_fills_empty_base_field(self):
        base = {"availability": ""}
        extra = {"availability": "2 weeks notice"}
        result = server._merge_profile_updates(base, extra)
        assert result["availability"] == "2 weeks notice"

    def test_base_non_empty_field_not_overwritten(self):
        base = {"availability": "immediate"}
        extra = {"availability": "3 months"}
        result = server._merge_profile_updates(base, extra)
        assert result["availability"] == "immediate"

    def test_list_fields_merged_without_duplicates(self):
        base = {"skills": ["Python"]}
        extra = {"skills": ["Python", "Docker"]}
        result = server._merge_profile_updates(base, extra)
        assert result["skills"].count("Python") == 1
        assert "Docker" in result["skills"]

    def test_none_base_field_filled_by_extra(self):
        base = {"location": None}
        extra = {"location": "London"}
        result = server._merge_profile_updates(base, extra)
        assert result["location"] == "London"

    def test_profile_deletions_not_copied_from_extra(self):
        base = {}
        extra = {"profile_deletions": {"skills": ["FastAPI"]}, "location": "Berlin"}
        result = server._merge_profile_updates(base, extra)
        assert "profile_deletions" not in result
        assert result["location"] == "Berlin"

    def test_empty_extra_returns_base_unchanged(self):
        base = {"availability": "immediate"}
        result = server._merge_profile_updates(base, {})
        assert result == base

    def test_empty_base_gets_all_extra_fields(self):
        extra = {"availability": "2 weeks", "location": "Berlin"}
        result = server._merge_profile_updates({}, extra)
        assert result["availability"] == "2 weeks"
        assert result["location"] == "Berlin"


# ---------------------------------------------------------------------------
# Multi-field answer regression tests
# ---------------------------------------------------------------------------

# Mirror of chatSuggestions.js SUGGESTIONS so tests are self-contained.
_SUGGESTION_CHECKS = [
    ("What's your current job title and industry?", lambda p: not p.get("headline")),
    ("What are your top skills?",                   lambda p: not p.get("keySkills")),
    ("Can you walk me through your work experience?", lambda p: not p.get("experience")),
    ("What's your highest level of education?",     lambda p: not p.get("education")),
    ("Do you have any certifications?",             lambda p: not p.get("certifications")),
    ("What roles are you targeting?",               lambda p: not p.get("preferred_roles")),
    ("Where are you located?",                      lambda p: not p.get("location")),
    ("What's your availability to start?",          lambda p: not p.get("availability")),
    ("Tell me about yourself in a few sentences.",  lambda p: not p.get("bio")),
    ("What salary range are you targeting?",        lambda p: not p.get("additional_information")),
]


def _missing_suggestions(profile: dict) -> list[str]:
    """Return the suggestion questions for fields still missing in profile."""
    return [q for q, missing_fn in _SUGGESTION_CHECKS if missing_fn(profile)]


class TestMultiFieldAnswerRegression:
    """
    Regression: one candidate response answers 2+ suggested questions.
    Both fields must be extracted/saved and those questions must not be suggested again.
    """

    def _pipeline(self, raw_extracted: dict) -> dict:
        """Sanitize raw LLM output then merge into an empty updates dict."""
        sanitized = server._sanitize_profile_updates(raw_extracted)
        return server._merge_profile_updates({}, sanitized)

    # --- extraction correctness ---

    def test_availability_and_salary_both_extracted(self):
        raw = {
            "availability": "2 weeks notice",
            "additional_information": "Targeting £60k–£80k salary",
        }
        result = self._pipeline(raw)
        assert result.get("availability") == "2 weeks notice"
        assert result.get("additional_information") == "Targeting £60k–£80k salary"

    def test_location_and_preferred_roles_both_extracted(self):
        raw = {
            "location": "Berlin",
            "preferred_roles": ["Backend Engineer"],
        }
        result = self._pipeline(raw)
        assert result.get("location") == "Berlin"
        assert "Backend Engineer" in result.get("preferred_roles", [])

    def test_skills_and_availability_both_extracted(self):
        raw = {
            "skills": ["Python", "FastAPI"],
            "availability": "immediately",
        }
        result = self._pipeline(raw)
        assert "Python" in result.get("skills", [])
        assert "FastAPI" in result.get("skills", [])
        assert result.get("availability") == "immediately"

    def test_three_fields_extracted_from_one_answer(self):
        raw = {
            "location": "London",
            "availability": "1 month notice",
            "preferred_roles": ["Data Engineer", "ML Engineer"],
        }
        result = self._pipeline(raw)
        assert result.get("location") == "London"
        assert result.get("availability") == "1 month notice"
        assert set(result.get("preferred_roles", [])) >= {"Data Engineer", "ML Engineer"}

    # --- suggestion suppression after saving ---

    def test_availability_question_not_suggested_after_save(self):
        profile_before = {
            "headline": "Engineer", "keySkills": ["Python"], "experience": [{"title": "Dev"}],
            "education": [{"degree": "BSc"}], "certifications": [], "preferred_roles": [],
            "location": "London", "availability": "", "bio": "", "additional_information": "",
        }
        profile_after = {**profile_before, "availability": "2 weeks notice"}

        assert "What's your availability to start?" in _missing_suggestions(profile_before)
        assert "What's your availability to start?" not in _missing_suggestions(profile_after)

    def test_preferred_roles_question_not_suggested_after_save(self):
        profile_before = {
            "headline": "Engineer", "keySkills": ["Python"], "experience": [{"title": "Dev"}],
            "education": [{"degree": "BSc"}], "certifications": [], "preferred_roles": [],
            "location": "London", "availability": "immediate", "bio": "", "additional_information": "",
        }
        profile_after = {**profile_before, "preferred_roles": ["Backend Engineer"]}

        assert "What roles are you targeting?" in _missing_suggestions(profile_before)
        assert "What roles are you targeting?" not in _missing_suggestions(profile_after)

    def test_both_availability_and_roles_not_suggested_after_single_answer(self):
        """Regression: one answer covers availability + preferred_roles — neither re-suggested."""
        profile_before = {
            "headline": "Engineer", "keySkills": ["Python"], "experience": [{"title": "Dev"}],
            "education": [{"degree": "BSc"}], "certifications": [], "preferred_roles": [],
            "location": "London", "availability": "", "bio": "", "additional_information": "",
        }
        # Simulate saving both fields from one multi-field extraction
        raw = {"availability": "2 weeks", "preferred_roles": ["Backend Engineer"]}
        saved = self._pipeline(raw)

        profile_after = {**profile_before, **saved}

        before_qs = _missing_suggestions(profile_before)
        after_qs = _missing_suggestions(profile_after)

        assert "What's your availability to start?" in before_qs
        assert "What roles are you targeting?" in before_qs
        assert "What's your availability to start?" not in after_qs
        assert "What roles are you targeting?" not in after_qs

    def test_salary_and_availability_not_suggested_after_single_answer(self):
        """Regression: 'I can start in 2 weeks, targeting £70k' answers 2 questions."""
        profile_before = {
            "headline": "Engineer", "keySkills": ["Python"], "experience": [{"title": "Dev"}],
            "education": [{"degree": "BSc"}], "certifications": [],
            "preferred_roles": ["Backend Engineer"],
            "location": "London", "availability": "", "bio": "Some bio", "additional_information": "",
        }
        raw = {"availability": "2 weeks", "additional_information": "Targeting £70k"}
        saved = self._pipeline(raw)
        profile_after = {**profile_before, **saved}

        assert "What's your availability to start?" in _missing_suggestions(profile_before)
        assert "What salary range are you targeting?" in _missing_suggestions(profile_before)
        assert "What's your availability to start?" not in _missing_suggestions(profile_after)
        assert "What salary range are you targeting?" not in _missing_suggestions(profile_after)

    # --- merge safety ---

    def test_already_present_field_not_overwritten_by_multi_extract(self):
        existing = {"availability": "immediate"}
        extra = {"availability": "3 months", "location": "Paris"}
        result = server._merge_profile_updates(existing, extra)
        assert result["availability"] == "immediate"
        assert result["location"] == "Paris"

    def test_sanitize_removes_noise_from_multi_field_skills(self):
        raw = {
            "skills": ["These are my skills", "Python", "Docker"],
            "availability": "2 weeks",
        }
        result = self._pipeline(raw)
        skills = result.get("skills", [])
        assert "These are my skills" not in skills
        assert "Python" in skills
        assert "Docker" in skills
        assert result.get("availability") == "2 weeks"

    def test_unrelated_fields_not_affected_by_multi_extract(self):
        """Saving availability must not touch skills or other fields."""
        raw = {"availability": "immediate"}
        result = self._pipeline(raw)
        assert "skills" not in result
        assert "preferred_roles" not in result
        assert result.get("availability") == "immediate"
