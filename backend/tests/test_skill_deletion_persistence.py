"""
Regression tests: skill deletion persistence.

Verifies that:
1. _remove_item_from_list correctly removes the skill (DB-level operation).
2. _extract_profile_updates detects natural-language deletion and produces the
   correct profile_deletions dict.
3. _sanitize_profile_updates preserves profile_deletions unchanged.
4. After deletion, the skill is absent from the resulting list (Profile UI state).
5. Unrelated skills and fields are not affected.
6. Deleting a non-existent skill is a no-op (no error, list unchanged).
7. Existing add/update behaviour is not broken.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import server


class TestSkillDeletionPersistence:
    """
    Simulate the full deletion pipeline without a real DB.
    Mirrors what _apply_profile_updates does for the 'skills' deletion branch.
    """

    def _run_deletion(self, existing_skills: list, item: str) -> tuple:
        return server._remove_item_from_list(existing_skills, item)

    # --- DB-level removal ---

    def test_fastapi_removed_from_skills(self):
        skills = ["Python", "FastAPI", "Docker", "PostgreSQL"]
        new_skills, found = self._run_deletion(skills, "FastAPI")
        assert found is True
        assert "FastAPI" not in new_skills
        assert "fastapi" not in [s.lower() for s in new_skills]

    def test_deleted_skill_absent_from_db_state(self):
        skills = ["Python", "FastAPI", "Docker"]
        new_skills, found = self._run_deletion(skills, "FastAPI")
        assert found is True
        assert "FastAPI" not in new_skills

    def test_unrelated_skills_preserved_after_deletion(self):
        skills = ["Python", "FastAPI", "Docker", "PostgreSQL"]
        new_skills, found = self._run_deletion(skills, "FastAPI")
        assert found is True
        assert set(new_skills) == {"Python", "Docker", "PostgreSQL"}

    def test_deletion_of_nonexistent_skill_is_noop(self):
        skills = ["Python", "Docker"]
        new_skills, found = self._run_deletion(skills, "Redis")
        assert found is False
        assert set(new_skills) == {"Python", "Docker"}

    def test_case_insensitive_deletion(self):
        skills = ["Python", "FastAPI", "Docker"]
        new_skills, found = self._run_deletion(skills, "fastapi")
        assert found is True
        assert not any(s.lower() == "fastapi" for s in new_skills)

    def test_multiple_skills_deleted_independently(self):
        skills = ["Python", "FastAPI", "Docker", "PostgreSQL"]
        after_first, found1 = self._run_deletion(skills, "FastAPI")
        assert found1 is True
        after_second, found2 = self._run_deletion(after_first, "Docker")
        assert found2 is True
        assert set(after_second) == {"Python", "PostgreSQL"}

    # --- Natural-language detection → deletion dict ---

    def test_deletion_detected_from_natural_language(self):
        _, updates = server._extract_profile_updates(
            "Done, I've removed FastAPI from your skills.",
            candidate_message="remove FastAPI from my skills",
        )
        assert updates is not None
        deletions = updates.get("profile_deletions", {})
        assert "skills" in deletions
        assert any("fastapi" in item.lower() for item in deletions["skills"])

    def test_profile_updates_dict_contains_only_deletion(self):
        """When candidate only deletes, no spurious add entries must appear."""
        _, updates = server._extract_profile_updates(
            "Sure, FastAPI has been removed.",
            candidate_message="remove FastAPI from my skills",
        )
        assert updates is not None
        assert "profile_deletions" in updates
        skills_added = updates.get("skills", [])
        assert not any("fastapi" in str(s).lower() for s in skills_added)

    def test_sanitize_preserves_profile_deletions(self):
        updates = {"profile_deletions": {"skills": ["FastAPI"]}}
        result = server._sanitize_profile_updates(updates)
        assert result.get("profile_deletions") == {"skills": ["FastAPI"]}

    # --- Profile UI state: deleted skill absent ---

    def test_deleted_skill_absent_from_profile_ui_state(self):
        """
        After _remove_item_from_list, the resulting list (what would be stored
        in the DB and returned by GET /profile) must not contain the deleted skill.
        The Profile panel reads keySkills directly from this list.
        """
        skills = ["Python", "FastAPI", "Docker"]
        db_state, _ = self._run_deletion(skills, "FastAPI")
        # Simulate what normalizeProfileForDisplay returns for keySkills
        assert "FastAPI" not in db_state
        assert "fastapi" not in [s.lower() for s in db_state]

    # --- No cross-field contamination ---

    def test_deletion_does_not_affect_certifications(self):
        _, updates = server._extract_profile_updates(
            "Removed FastAPI from your skills.",
            candidate_message="remove FastAPI from my skills",
        )
        assert updates is not None
        assert "certifications" not in updates

    def test_deletion_does_not_affect_preferred_roles(self):
        _, updates = server._extract_profile_updates(
            "Removed FastAPI from your skills.",
            candidate_message="remove FastAPI from my skills",
        )
        assert updates is not None
        assert "preferred_roles" not in updates

    def test_deletion_does_not_affect_work_experience(self):
        _, updates = server._extract_profile_updates(
            "Removed FastAPI from your skills.",
            candidate_message="remove FastAPI from my skills",
        )
        assert updates is not None
        assert "work_experience" not in updates

    # --- Existing add/update behaviour not broken ---

    def test_adding_skill_still_works(self):
        reply = (
            "Added Redis to your skills.\n"
            "<<<PROFILE_UPDATES>>>\n"
            '{"profile_updates": {"skills": ["Redis"]}}\n'
            "<<<END_UPDATES>>>"
        )
        _, updates = server._extract_profile_updates(reply)
        assert updates is not None
        assert "Redis" in updates.get("skills", [])
        assert "profile_deletions" not in updates

    def test_adding_skill_does_not_remove_existing_skills(self):
        """_merge_skills (called inside _apply_profile_updates) unions lists."""
        existing = ["Python", "Docker"]
        new = ["Redis"]
        merged = server._merge_skills(existing, new)
        assert "Python" in merged
        assert "Docker" in merged
        assert "Redis" in merged

    def test_update_and_delete_in_same_message(self):
        """LLM can emit both a new skill and a deletion in one block."""
        reply = (
            "Updated.\n"
            "<<<PROFILE_UPDATES>>>\n"
            '{"profile_updates": {"skills": ["Redis"], "profile_deletions": {"skills": ["FastAPI"]}}}\n'
            "<<<END_UPDATES>>>"
        )
        _, updates = server._extract_profile_updates(reply)
        assert updates is not None
        assert "Redis" in updates.get("skills", [])
        assert updates.get("profile_deletions", {}).get("skills") == ["FastAPI"]
