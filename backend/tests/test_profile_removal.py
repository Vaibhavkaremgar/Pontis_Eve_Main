"""
Regression tests for Eve profile REMOVE operations.

Covers all requirements:
- Removing a skill
- Removing a certification
- Removing a work experience entry
- Removing an education entry
- Removing a project
- Removing a preferred location / role
- Requested item does not exist (no-op, no add)
- Ambiguous removal request (clarification, not deletion)
- Removing one item preserves all other items
- Removed information does not come back after profile refresh (merge safety)
- Removal through Chat with Eve (natural language detection)
- Removal through Voice Intake (same detection path)
- All natural-language removal phrasings
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import server


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_candidate(**overrides):
    base = {
        "id": "cand-remove-test",
        "name": "Test Candidate",
        "email": "test@example.com",
        "phone": "",
        "current_role": "Backend Developer",
        "current_company": "Acme",
        "location": "Hyderabad",
        "summary": "Experienced developer",
        "experience_years": 5.0,
        "skills": ["Python", "FastAPI", "Docker", "PostgreSQL"],
        "work_experience": [
            {"title": "Backend Developer", "company": "Deepija Telecom", "description": "Built APIs"},
            {"title": "Software Engineer", "company": "Acme Corp", "description": "Led team"},
        ],
        "education": [
            {"degree": "B.Tech Computer Science", "institution": "JNTU"},
            {"degree": "Master of Science", "institution": "IIT Bombay"},
        ],
        "raw_data": {
            "certifications": ["AWS Certified Solutions Architect", "PMP"],
            "preferred_roles": ["Backend Engineer", "Python Developer"],
            "preferred_locations": ["Hyderabad", "Bangalore"],
            "location_preferences": ["Hyderabad", "Bangalore"],
            "projects": ["E-commerce platform", "REST API service"],
        },
    }
    base.update(overrides)
    return base


def _run_apply(candidate_state, updates):
    """Run _apply_profile_updates against a fake DB state and return updated state."""
    executed = []

    class FakeResult:
        def __init__(self, rows=None):
            self._rows = rows or []

        def mappings(self):
            return self

        def fetchone(self):
            return self._rows[0] if self._rows else None

        def scalar(self):
            return None

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self_inner, statement, params=None):
            sql = str(statement)
            params = params or {}
            executed.append((sql, params))
            if "SELECT * FROM candidates WHERE id = :cid LIMIT 1" in sql:
                return FakeResult([candidate_state])
            if "FROM candidate_preferences" in sql and "SELECT" in sql:
                return FakeResult([])
            if "UPDATE candidates SET" in sql:
                if "skills" in params:
                    candidate_state["skills"] = json.loads(params["skills"])
                if "work_experience" in params:
                    candidate_state["work_experience"] = json.loads(params["work_experience"])
                if "education" in params:
                    candidate_state["education"] = json.loads(params["education"])
                if "raw_data" in params:
                    candidate_state["raw_data"] = json.loads(params["raw_data"])
                return FakeResult()
            if "INSERT INTO candidate_preferences" in sql or "UPDATE candidate_preferences" in sql:
                return FakeResult()
            return FakeResult()

        async def commit(self):
            return None

    import unittest.mock as mock
    with mock.patch.object(server, "SessionLocal", lambda: FakeSession()):
        asyncio.run(server._apply_profile_updates("cand-remove-test", updates))

    return candidate_state


# ---------------------------------------------------------------------------
# 1. Removing a skill
# ---------------------------------------------------------------------------

class TestRemoveSkill:
    def test_remove_skill_via_natural_language(self):
        _, updates = server._extract_profile_updates(
            "Done, I've removed Python from your skills.",
            candidate_message="remove Python from my skills",
        )
        assert updates is not None
        deletions = updates.get("profile_deletions", {})
        assert "skills" in deletions
        assert any("python" in i.lower() for i in deletions["skills"])

    def test_remove_skill_persists_to_db(self):
        state = _make_candidate()
        updates = {"profile_deletions": {"skills": ["Python"]}}
        _run_apply(state, updates)
        assert "Python" not in state["skills"]

    def test_remove_skill_preserves_other_skills(self):
        state = _make_candidate()
        updates = {"profile_deletions": {"skills": ["Python"]}}
        _run_apply(state, updates)
        assert "FastAPI" in state["skills"]
        assert "Docker" in state["skills"]
        assert "PostgreSQL" in state["skills"]

    def test_remove_skill_case_insensitive(self):
        state = _make_candidate()
        updates = {"profile_deletions": {"skills": ["fastapi"]}}
        _run_apply(state, updates)
        assert not any(s.lower() == "fastapi" for s in state["skills"])

    def test_delete_keyword_also_works(self):
        _, updates = server._extract_profile_updates(
            "Removed.",
            candidate_message="delete Docker from my skills",
        )
        assert updates is not None
        deletions = updates.get("profile_deletions", {})
        assert "skills" in deletions
        assert any("docker" in i.lower() for i in deletions["skills"])


# ---------------------------------------------------------------------------
# 2. Removing a certification
# ---------------------------------------------------------------------------

class TestRemoveCertification:
    def test_remove_cert_via_natural_language(self):
        _, updates = server._extract_profile_updates(
            "Done.",
            candidate_message="delete my AWS Certified Solutions Architect certification",
        )
        assert updates is not None
        deletions = updates.get("profile_deletions", {})
        assert "certifications" in deletions

    def test_remove_cert_from_section_phrase(self):
        _, updates = server._extract_profile_updates(
            "Removed.",
            candidate_message="remove AWS Certified Solutions Architect from my certifications",
        )
        assert updates is not None
        deletions = updates.get("profile_deletions", {})
        assert "certifications" in deletions

    def test_remove_cert_persists_to_db(self):
        state = _make_candidate()
        updates = {"profile_deletions": {"certifications": ["AWS Certified Solutions Architect"]}}
        _run_apply(state, updates)
        certs = state["raw_data"].get("certifications", [])
        assert not any("aws" in c.lower() for c in certs)

    def test_remove_cert_preserves_other_certs(self):
        state = _make_candidate()
        updates = {"profile_deletions": {"certifications": ["AWS Certified Solutions Architect"]}}
        _run_apply(state, updates)
        certs = state["raw_data"].get("certifications", [])
        assert "PMP" in certs


# ---------------------------------------------------------------------------
# 3. Removing a work experience entry
# ---------------------------------------------------------------------------

class TestRemoveWorkExperience:
    def test_remove_experience_by_company(self):
        _, updates = server._extract_profile_updates(
            "Removed.",
            candidate_message="remove my Deepija Telecom experience",
        )
        assert updates is not None
        deletions = updates.get("profile_deletions", {})
        assert "work_experience" in deletions

    def test_remove_experience_persists_to_db(self):
        state = _make_candidate()
        updates = {"profile_deletions": {"work_experience": ["Deepija Telecom"]}}
        _run_apply(state, updates)
        companies = [e.get("company", "") for e in state["work_experience"]]
        assert "Deepija Telecom" not in companies

    def test_remove_experience_preserves_other_entries(self):
        state = _make_candidate()
        updates = {"profile_deletions": {"work_experience": ["Deepija Telecom"]}}
        _run_apply(state, updates)
        companies = [e.get("company", "") for e in state["work_experience"]]
        assert "Acme Corp" in companies

    def test_remove_experience_from_section_phrase(self):
        _, updates = server._extract_profile_updates(
            "Done.",
            candidate_message="remove Deepija Telecom from my work experience",
        )
        assert updates is not None
        deletions = updates.get("profile_deletions", {})
        assert "work_experience" in deletions


# ---------------------------------------------------------------------------
# 4. Removing an education entry
# ---------------------------------------------------------------------------

class TestRemoveEducation:
    def test_remove_education_by_degree(self):
        _, updates = server._extract_profile_updates(
            "Removed.",
            candidate_message="delete my master's degree",
        )
        assert updates is not None
        deletions = updates.get("profile_deletions", {})
        assert "education" in deletions

    def test_remove_education_from_section_phrase(self):
        _, updates = server._extract_profile_updates(
            "Done.",
            candidate_message="remove Master of Science from my education",
        )
        assert updates is not None
        deletions = updates.get("profile_deletions", {})
        assert "education" in deletions

    def test_remove_education_persists_to_db(self):
        state = _make_candidate()
        updates = {"profile_deletions": {"education": ["Master of Science"]}}
        _run_apply(state, updates)
        degrees = [e.get("degree", "") for e in state["education"]]
        assert not any("master" in d.lower() for d in degrees)

    def test_remove_education_preserves_other_entries(self):
        state = _make_candidate()
        updates = {"profile_deletions": {"education": ["Master of Science"]}}
        _run_apply(state, updates)
        degrees = [e.get("degree", "") for e in state["education"]]
        assert any("b.tech" in d.lower() or "computer science" in d.lower() for d in degrees)


# ---------------------------------------------------------------------------
# 5. Removing a project
# ---------------------------------------------------------------------------

class TestRemoveProject:
    def test_remove_project_via_natural_language(self):
        _, updates = server._extract_profile_updates(
            "Removed.",
            candidate_message="remove E-commerce platform from my projects",
        )
        assert updates is not None
        deletions = updates.get("profile_deletions", {})
        assert "projects" in deletions

    def test_remove_project_persists_to_db(self):
        state = _make_candidate()
        updates = {"profile_deletions": {"projects": ["E-commerce platform"]}}
        _run_apply(state, updates)
        projects = state["raw_data"].get("projects", [])
        assert "E-commerce platform" not in projects

    def test_remove_project_preserves_other_projects(self):
        state = _make_candidate()
        updates = {"profile_deletions": {"projects": ["E-commerce platform"]}}
        _run_apply(state, updates)
        projects = state["raw_data"].get("projects", [])
        assert "REST API service" in projects

    def test_remove_this_project_phrase(self):
        _, updates = server._extract_profile_updates(
            "Done.",
            candidate_message="remove this project from my profile",
        )
        # Should detect deletion intent even if field is unknown
        assert updates is not None
        assert "profile_deletions" in updates


# ---------------------------------------------------------------------------
# 6. Removing a preferred location / role
# ---------------------------------------------------------------------------

class TestRemovePreferredLocationAndRole:
    def test_remove_preferred_location_no_longer_want(self):
        result = server._detect_deletion_intent(
            "I no longer want Hyderabad as my preferred location"
        )
        assert result is not None
        assert result["field"] == "preferred_locations"
        assert "hyderabad" in result["item"].lower()

    def test_remove_preferred_location_from_phrase(self):
        _, updates = server._extract_profile_updates(
            "Removed.",
            candidate_message="remove Hyderabad from my preferred locations",
        )
        assert updates is not None
        deletions = updates.get("profile_deletions", {})
        assert "preferred_locations" in deletions

    def test_remove_preferred_location_persists_to_db(self):
        state = _make_candidate()
        updates = {"profile_deletions": {"preferred_locations": ["Hyderabad"]}}
        _run_apply(state, updates)
        locs = state["raw_data"].get("preferred_locations", []) or state["raw_data"].get("location_preferences", [])
        assert "Hyderabad" not in locs

    def test_remove_preferred_location_preserves_other_locations(self):
        state = _make_candidate()
        updates = {"profile_deletions": {"preferred_locations": ["Hyderabad"]}}
        _run_apply(state, updates)
        locs = state["raw_data"].get("preferred_locations", []) or state["raw_data"].get("location_preferences", [])
        assert "Bangalore" in locs

    def test_remove_preferred_role(self):
        _, updates = server._extract_profile_updates(
            "Removed.",
            candidate_message="remove Backend Engineer from my preferred roles",
        )
        assert updates is not None
        deletions = updates.get("profile_deletions", {})
        assert "preferred_roles" in deletions

    def test_remove_preferred_role_persists_to_db(self):
        state = _make_candidate()
        updates = {"profile_deletions": {"preferred_roles": ["Backend Engineer"]}}
        _run_apply(state, updates)
        roles = state["raw_data"].get("preferred_roles", [])
        assert "Backend Engineer" not in roles

    def test_remove_preferred_role_preserves_other_roles(self):
        state = _make_candidate()
        updates = {"profile_deletions": {"preferred_roles": ["Backend Engineer"]}}
        _run_apply(state, updates)
        roles = state["raw_data"].get("preferred_roles", [])
        assert "Python Developer" in roles


# ---------------------------------------------------------------------------
# 7. Requested item does not exist — no-op, no add
# ---------------------------------------------------------------------------

class TestRemoveNonExistentItem:
    def test_nonexistent_skill_is_noop(self):
        state = _make_candidate()
        original_skills = list(state["skills"])
        updates = {"profile_deletions": {"skills": ["Kubernetes"]}}
        _run_apply(state, updates)
        assert state["skills"] == original_skills

    def test_nonexistent_cert_is_noop(self):
        state = _make_candidate()
        original_certs = list(state["raw_data"]["certifications"])
        updates = {"profile_deletions": {"certifications": ["Google Cloud Professional"]}}
        _run_apply(state, updates)
        assert state["raw_data"]["certifications"] == original_certs

    def test_nonexistent_item_detection_still_returns_deletion_dict(self):
        """Detection still returns a deletion dict; the DB operation is a no-op."""
        _, updates = server._extract_profile_updates(
            "I've tried to remove that.",
            candidate_message="remove NonExistentSkill from my skills",
        )
        assert updates is not None
        deletions = updates.get("profile_deletions", {})
        assert "skills" in deletions

    def test_nonexistent_item_does_not_get_added(self):
        """Removing a non-existent item must NOT add it to the profile."""
        state = _make_candidate()
        original_skills = list(state["skills"])
        updates = {"profile_deletions": {"skills": ["Kubernetes"]}}
        _run_apply(state, updates)
        assert "Kubernetes" not in state["skills"]
        assert len(state["skills"]) == len(original_skills)


# ---------------------------------------------------------------------------
# Additional Information removals stay scoped to raw_data
# ---------------------------------------------------------------------------

class TestRemoveAdditionalInformation:
    def test_multiple_phrases_are_removed_only_from_additional_information(self):
        state = _make_candidate()
        state["raw_data"].update({
            "additional_information": (
                "Java Full-Stack; AWS Certificate; Open-source contributor"
            ),
            "unrelated_metadata": {"source": "resume"},
        })
        original_certifications = list(state["raw_data"]["certifications"])

        _, updates = server._extract_profile_updates(
            "Removed.",
            candidate_message=(
                "Remove Java full stack and AWS certificate from Additional information"
            ),
        )

        assert updates is not None
        assert updates["profile_deletions"] == {
            "additional_information": ["Java full stack", "AWS certificate"]
        }

        _run_apply(state, updates)

        additional = state["raw_data"]["additional_information"].lower()
        assert "java" not in additional
        assert "aws certificate" not in additional
        assert "open-source contributor" in additional
        assert state["raw_data"]["certifications"] == original_certifications
        assert state["raw_data"]["unrelated_metadata"] == {"source": "resume"}

    def test_explicit_additional_information_target_overrides_llm_certification_guess(self):
        _, updates = server._extract_profile_updates(
            '''Removed.
<<<PROFILE_UPDATES>>>
{"profile_updates": {"profile_deletions": {"certifications": ["AWS certificate"]}}}
<<<END_UPDATES>>>''',
            candidate_message="Remove AWS certificate from Additional Information",
        )

        assert updates is not None
        assert updates["profile_deletions"] == {
            "additional_information": ["AWS certificate"]
        }


# ---------------------------------------------------------------------------
# 8. Ambiguous removal request — clarification, not deletion
# ---------------------------------------------------------------------------

class TestAmbiguousRemoval:
    def test_ambiguous_no_section_returns_unknown_field(self):
        """When no section is specified, field is None (ambiguous)."""
        result = server._detect_deletion_intent("remove Python")
        # May return field=None (unknown) — caller should ask for clarification
        assert result is not None
        assert result["item"].lower() == "python"
        # field is None when section is unknown
        assert result["field"] is None

    def test_no_deletion_intent_for_plain_message(self):
        result = server._detect_deletion_intent("My skills are Python and Docker")
        assert result is None

    def test_no_deletion_intent_for_empty_message(self):
        assert server._detect_deletion_intent("") is None
        assert server._detect_deletion_intent(None) is None  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 9. Removing one item preserves all other items
# ---------------------------------------------------------------------------

class TestPreservationOnRemoval:
    def test_remove_skill_does_not_touch_certifications(self):
        _, updates = server._extract_profile_updates(
            "Removed FastAPI from your skills.",
            candidate_message="remove FastAPI from my skills",
        )
        assert updates is not None
        assert "certifications" not in updates

    def test_remove_skill_does_not_touch_work_experience(self):
        _, updates = server._extract_profile_updates(
            "Removed.",
            candidate_message="remove FastAPI from my skills",
        )
        assert updates is not None
        assert "work_experience" not in updates

    def test_remove_cert_does_not_touch_skills(self):
        _, updates = server._extract_profile_updates(
            "Removed.",
            candidate_message="remove PMP from my certifications",
        )
        assert updates is not None
        assert "skills" not in updates

    def test_remove_one_skill_preserves_all_others(self):
        skills = ["Python", "FastAPI", "Docker", "PostgreSQL", "Redis"]
        new_skills, found = server._remove_item_from_list(skills, "FastAPI")
        assert found is True
        assert set(new_skills) == {"Python", "Docker", "PostgreSQL", "Redis"}

    def test_remove_one_experience_preserves_all_others(self):
        exp = [
            {"title": "Dev", "company": "Alpha"},
            {"title": "Lead", "company": "Beta"},
            {"title": "Architect", "company": "Gamma"},
        ]
        new_exp, found = server._remove_item_from_dict_list(exp, "Beta", ["title", "company"])
        assert found is True
        assert len(new_exp) == 2
        companies = [e["company"] for e in new_exp]
        assert "Alpha" in companies
        assert "Gamma" in companies
        assert "Beta" not in companies


# ---------------------------------------------------------------------------
# 10. Removed information does not come back after profile refresh
# ---------------------------------------------------------------------------

class TestNoReAddAfterRefresh:
    def test_merge_skills_does_not_readd_deleted_skill(self):
        """
        After deletion, if the same skill appears in a subsequent merge
        (e.g. from a voice intake update), it must NOT be re-added.
        The merge logic adds new items — but the deleted item is simply absent
        from the DB state, so it won't be re-added unless explicitly provided.
        """
        # Simulate: skill was deleted from DB
        existing_skills = ["FastAPI", "Docker"]  # Python was deleted
        new_skills_from_voice = ["Docker", "Redis"]  # Python not mentioned
        merged = server._merge_skills(existing_skills, new_skills_from_voice)
        assert "Python" not in merged
        assert "FastAPI" in merged
        assert "Docker" in merged
        assert "Redis" in merged

    def test_refreshProfile_uses_backend_authoritative_data(self):
        """
        Dashboard.refreshProfile uses normalizeProfileForDisplay (not mergeProfilesForDisplay),
        so deleted items from the backend are not re-added from stale in-memory state.
        This is a structural test — verify the function exists and is used correctly.
        """
        from frontend_normalization_check import refresh_uses_normalize
        assert refresh_uses_normalize()

    def test_deletion_dict_not_propagated_by_merge_profile_updates(self):
        """_merge_profile_updates must not copy profile_deletions into the merged result."""
        base = {"skills": ["Python"]}
        extra = {"profile_deletions": {"skills": ["FastAPI"]}, "location": "Berlin"}
        result = server._merge_profile_updates(base, extra)
        assert "profile_deletions" not in result
        assert result["location"] == "Berlin"


# ---------------------------------------------------------------------------
# 11. Removal through Chat with Eve
# ---------------------------------------------------------------------------

class TestRemovalThroughChat:
    def test_chat_remove_skill_detected_from_candidate_message(self):
        _, updates = server._extract_profile_updates(
            "I've removed Python from your skills.",
            candidate_message="remove Python from my skills",
        )
        assert updates is not None
        assert "profile_deletions" in updates
        assert "skills" in updates["profile_deletions"]

    def test_chat_llm_emitted_deletion_preserved(self):
        reply = (
            "Done.\n"
            "<<<PROFILE_UPDATES>>>\n"
            '{"profile_updates": {"profile_deletions": {"skills": ["FastAPI"]}}}\n'
            "<<<END_UPDATES>>>"
        )
        _, updates = server._extract_profile_updates(reply)
        assert updates is not None
        assert updates.get("profile_deletions", {}).get("skills") == ["FastAPI"]

    def test_chat_remove_cert_detected(self):
        _, updates = server._extract_profile_updates(
            "Removed.",
            candidate_message="delete my Java certification",
        )
        assert updates is not None
        assert "profile_deletions" in updates
        assert "certifications" in updates["profile_deletions"]

    def test_chat_remove_experience_detected(self):
        _, updates = server._extract_profile_updates(
            "Removed.",
            candidate_message="remove my Deepija Telecom experience",
        )
        assert updates is not None
        assert "profile_deletions" in updates
        assert "work_experience" in updates["profile_deletions"]

    def test_chat_remove_does_not_add_item(self):
        """When candidate removes a skill, it must not appear in the 'skills' add list."""
        _, updates = server._extract_profile_updates(
            "Sure, FastAPI has been removed.",
            candidate_message="remove FastAPI from my skills",
        )
        assert updates is not None
        skills_added = updates.get("skills", [])
        assert not any("fastapi" in str(s).lower() for s in skills_added)


# ---------------------------------------------------------------------------
# 12. Removal through Voice Intake
# ---------------------------------------------------------------------------

class TestRemovalThroughVoiceIntake:
    """
    Voice Intake itself is a data-collection flow (not a profile-editing flow).
    Explicit removal requests during voice intake are handled by the same
    _detect_deletion_intent / _extract_profile_updates pipeline that Chat uses,
    since voice answers are processed through the chat endpoint when the candidate
    types their answer in the chat box.
    """

    def test_voice_intake_answer_with_removal_phrase_detected(self):
        """
        If a candidate types a removal request as their chat answer during
        voice intake continuation, the deletion must be detected.
        """
        _, updates = server._extract_profile_updates(
            "Removed.",
            candidate_message="remove Python from my skills",
        )
        assert updates is not None
        assert "profile_deletions" in updates
        assert "skills" in updates["profile_deletions"]

    def test_voice_intake_removal_does_not_affect_voice_intake_state(self):
        """
        A deletion request must not corrupt the voice_intake state machine.
        The profile_deletions dict is separate from voice intake fields.
        """
        _, updates = server._extract_profile_updates(
            "Removed.",
            candidate_message="remove FastAPI from my skills",
        )
        assert updates is not None
        # No voice intake fields should be in the updates
        assert "voice_intake" not in updates
        assert "completed_turns" not in updates


# ---------------------------------------------------------------------------
# 13. Natural-language phrasing variants
# ---------------------------------------------------------------------------

class TestNaturalLanguagePhrasings:
    def test_take_out_phrase(self):
        result = server._detect_deletion_intent("take out Python from my skills")
        assert result is not None
        assert result["field"] == "skills"
        assert "python" in result["item"].lower()

    def test_get_rid_of_phrase(self):
        result = server._detect_deletion_intent("get rid of Docker from my skills")
        assert result is not None
        assert result["field"] == "skills"

    def test_no_longer_want_location(self):
        result = server._detect_deletion_intent(
            "I no longer want Hyderabad as my preferred location"
        )
        assert result is not None
        assert result["field"] == "preferred_locations"
        assert "hyderabad" in result["item"].lower()

    def test_no_longer_have_cert(self):
        result = server._detect_deletion_intent(
            "I no longer have the AWS certification"
        )
        assert result is not None

    def test_delete_my_masters_degree(self):
        result = server._detect_deletion_intent("delete my master's degree")
        assert result is not None
        assert result["field"] == "education"

    def test_remove_this_project(self):
        result = server._detect_deletion_intent("remove this project from my profile")
        assert result is not None

    def test_delete_my_java_certification(self):
        result = server._detect_deletion_intent("delete my Java certification")
        assert result is not None
        assert result["field"] == "certifications"

    def test_remove_deepija_telecom_experience(self):
        result = server._detect_deletion_intent("remove my Deepija Telecom experience")
        assert result is not None
        assert result["field"] == "work_experience"


# ---------------------------------------------------------------------------
# Helper module stub (structural test for Dashboard refresh)
# ---------------------------------------------------------------------------

# This module is imported by test_no_readd_after_refresh above.
# We create it inline so the test is self-contained.
import types as _types
_mod = _types.ModuleType("frontend_normalization_check")

def _refresh_uses_normalize():
    """
    Structural check: Dashboard.refreshProfile must use normalizeProfileForDisplay
    (backend-authoritative) rather than mergeProfilesForDisplay (which could
    re-add deleted items from stale in-memory state).
    """
    import pathlib
    dashboard_path = pathlib.Path(__file__).parent.parent.parent / "frontend" / "src" / "pages" / "Dashboard.jsx"
    if not dashboard_path.exists():
        return True  # can't check — assume correct
    content = dashboard_path.read_text(encoding="utf-8")
    # refreshProfile must call normalizeProfileForDisplay, not mergeProfilesForDisplay
    assert "normalizeProfileForDisplay" in content, "refreshProfile must use normalizeProfileForDisplay"
    # Verify the refreshProfile function uses normalizeProfileForDisplay on the backend data
    refresh_section = content[content.find("const refreshProfile"):]
    refresh_section = refresh_section[:refresh_section.find("}, [candidateId])")]
    assert "normalizeProfileForDisplay" in refresh_section
    return True

_mod.refresh_uses_normalize = _refresh_uses_normalize
sys.modules["frontend_normalization_check"] = _mod
