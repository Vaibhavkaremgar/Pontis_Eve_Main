"""
Regression tests for certification profile-update bugs.

BUG 1: "Yes I have java full stack and AWS certificate"
        -> must NOT add "any" as a certification.
        -> must add Java Full Stack and AWS Certificate.

BUG 2: "Remove any from Certifications"
        -> "any" must be removed from the persisted profile.
        -> right-side profile must reflect the deletion.
        -> deletion must survive refresh.
        -> other certifications must be preserved.
        -> deleting a non-existent cert must not add it.
"""
import asyncio
import json
import os
import sys
import unittest.mock as mock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import server


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_candidate(certifications=None, **overrides):
    base = {
        "id": "cand-cert-bug-test",
        "name": "Test Candidate",
        "email": "test@example.com",
        "phone": "",
        "current_role": "",
        "current_company": "",
        "location": "",
        "summary": "",
        "experience_years": None,
        "skills": [],
        "work_experience": [],
        "education": [],
        "raw_data": {"certifications": list(certifications or [])},
        "parsed_resume_json": None,
    }
    base.update(overrides)
    return base


def _run_apply(candidate_state, updates):
    """Run _apply_profile_updates against a fake DB state and return updated state."""

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
                if "parsed_resume_json" in params:
                    candidate_state["parsed_resume_json"] = json.loads(params["parsed_resume_json"])
                return FakeResult()
            if "INSERT INTO candidate_preferences" in sql or "UPDATE candidate_preferences" in sql:
                return FakeResult()
            return FakeResult()

        async def commit(self):
            return None

    with mock.patch.object(server, "SessionLocal", lambda: FakeSession()):
        asyncio.run(server._apply_profile_updates("cand-cert-bug-test", updates))
    return candidate_state


def _get_certs(state):
    raw = state.get("raw_data") or {}
    if isinstance(raw, str):
        raw = json.loads(raw)
    return raw.get("certifications") or []


# ---------------------------------------------------------------------------
# BUG 1 — "any" must NOT be added as a certification
# ---------------------------------------------------------------------------

class TestBug1AnyNotAddedAsCertification:

    def test_sanitize_strips_any_from_list(self):
        items = ["any", "Java Full Stack", "AWS Certificate"]
        result = server._sanitize_structured_list_items(items)
        assert "any" not in result
        assert "Java Full Stack" in result
        assert "AWS Certificate" in result

    def test_sanitize_strips_yes_from_list(self):
        items = ["yes", "Java Full Stack"]
        result = server._sanitize_structured_list_items(items)
        assert "yes" not in result
        assert "Java Full Stack" in result

    def test_sanitize_strips_some_from_list(self):
        items = ["some", "AWS Certificate"]
        result = server._sanitize_structured_list_items(items)
        assert "some" not in result
        assert "AWS Certificate" in result

    def test_sanitize_profile_updates_strips_any_from_certifications(self):
        updates = {"certifications": ["any", "Java Full Stack", "AWS Certificate"]}
        result = server._sanitize_profile_updates(updates)
        certs = result.get("certifications", [])
        assert "any" not in [c.lower() for c in certs]
        assert any("java full stack" in c.lower() for c in certs)
        assert any("aws certificate" in c.lower() for c in certs)

    def test_extract_profile_updates_llm_marker_strips_any(self):
        reply = (
            "Great, I've noted your certifications.\n"
            "<<<PROFILE_UPDATES>>>\n"
            '{"profile_updates": {"certifications": ["any", "Java Full Stack", "AWS Certificate"]}}\n'
            "<<<END_UPDATES>>>"
        )
        _, updates = server._extract_profile_updates(reply)
        assert updates is not None
        certs = updates.get("certifications", [])
        assert "any" not in [c.lower() for c in certs]
        assert any("java full stack" in c.lower() for c in certs)
        assert any("aws certificate" in c.lower() for c in certs)

    def test_natural_language_yes_i_have_java_full_stack_and_aws(self):
        """Regression: 'Yes I have java full stack and AWS certificate' must not add 'any'."""
        message = "Yes I have java full stack and AWS certificate"
        _, updates = server._extract_profile_updates("", candidate_message=message)
        if updates and "certifications" in updates:
            cert_lower = [c.lower() for c in updates["certifications"]]
            assert "any" not in cert_lower, (
                f"'any' must not be a certification, got: {updates['certifications']}"
            )

    def test_apply_profile_updates_does_not_persist_any(self):
        state = _make_candidate()
        updates = {"certifications": ["any", "Java Full Stack", "AWS Certificate"]}
        _run_apply(state, updates)
        certs = _get_certs(state)
        assert "any" not in [c.lower() for c in certs]
        assert any("java full stack" in c.lower() for c in certs)
        assert any("aws certificate" in c.lower() for c in certs)

    def test_normalize_certifications_strips_any(self):
        result = server._normalize_certifications(["any", "Java Full Stack", "AWS Certificate"])
        assert "any" not in [c.lower() for c in result]
        assert any("java full stack" in c.lower() for c in result)
        assert any("aws certificate" in c.lower() for c in result)


# ---------------------------------------------------------------------------
# BUG 2 — Certification deletion persisted and reflected
# ---------------------------------------------------------------------------

class TestBug2CertificationDeletion:
    """
    Starting state: certifications = ["any", "Java Full Stack", "AWS Certificate"]
    After "Remove any from Certifications":
      -> ["Java Full Stack", "AWS Certificate"]
    """

    def _initial_state(self):
        return _make_candidate(certifications=["any", "Java Full Stack", "AWS Certificate"])

    def test_deletion_intent_detected_for_any(self):
        result = server._detect_deletion_intent("Remove any from Certifications")
        assert result is not None
        assert result["field"] == "certifications"
        assert result["item"].lower() == "any"

    def test_extract_profile_updates_detects_deletion(self):
        _, updates = server._extract_profile_updates(
            "Done, I've removed it.",
            candidate_message="Remove any from Certifications",
        )
        assert updates is not None
        deletions = updates.get("profile_deletions", {})
        assert "certifications" in deletions
        assert any("any" == i.lower() for i in deletions["certifications"])

    def test_deletion_persisted_to_db(self):
        state = self._initial_state()
        updates = {"profile_deletions": {"certifications": ["any"]}}
        _run_apply(state, updates)
        certs = _get_certs(state)
        assert "any" not in [c.lower() for c in certs]

    def test_other_certifications_preserved_after_deletion(self):
        state = self._initial_state()
        updates = {"profile_deletions": {"certifications": ["any"]}}
        _run_apply(state, updates)
        certs = _get_certs(state)
        assert any("java full stack" in c.lower() for c in certs)
        assert any("aws certificate" in c.lower() for c in certs)

    def test_deletion_does_not_return_after_normalize(self):
        """After deletion, _normalize_for_frontend must not re-add the deleted cert."""
        state = _make_candidate(certifications=["Java Full Stack", "AWS Certificate"])
        profile = server._normalize_for_frontend(state)
        certs = profile.get("certifications") or []
        assert "any" not in [c.lower() for c in certs]
        assert any("java full stack" in c.lower() for c in certs)
        assert any("aws certificate" in c.lower() for c in certs)

    def test_nonexistent_cert_deletion_is_noop(self):
        state = _make_candidate(certifications=["Java Full Stack", "AWS Certificate"])
        original_certs = list(_get_certs(state))
        updates = {"profile_deletions": {"certifications": ["NonExistentCert"]}}
        _run_apply(state, updates)
        certs = _get_certs(state)
        assert "NonExistentCert" not in certs
        assert set(c.lower() for c in certs) == set(c.lower() for c in original_certs)

    def test_remove_one_cert_preserves_others(self):
        lst, found = server._remove_item_from_list(
            ["any", "Java Full Stack", "AWS Certificate"], "any"
        )
        assert found is True
        assert "any" not in [c.lower() for c in lst]
        assert any("java full stack" in c.lower() for c in lst)
        assert any("aws certificate" in c.lower() for c in lst)

    def test_profile_deletions_key_present_in_updates(self):
        """Backend must return profile_deletions in updates so frontend can detect it."""
        _, updates = server._extract_profile_updates(
            "Removed.",
            candidate_message="Remove any from Certifications",
        )
        assert updates is not None
        assert "profile_deletions" in updates
        assert isinstance(updates["profile_deletions"], dict)
        assert updates["profile_deletions"]

    def test_end_to_end_any_in_db_then_deleted(self):
        """If 'any' is in the DB (pre-fix data), deletion must remove it cleanly."""
        state = _make_candidate(certifications=["any", "Java Full Stack", "AWS Certificate"])
        updates = {"profile_deletions": {"certifications": ["any"]}}
        _run_apply(state, updates)
        certs = _get_certs(state)
        assert "any" not in [c.lower() for c in certs]
        assert any("java full stack" in c.lower() for c in certs)
        assert any("aws certificate" in c.lower() for c in certs)

    def test_delete_aws_certificate_persists_across_raw_and_resume_sources_then_refresh(self):
        """A dashboard refresh must not revive a cert from parsed_resume_json."""
        state = _make_candidate(
            certifications=["AWS Certificate", "PMP"],
            parsed_resume_json={"certifications": ["AWS Certificate", "PMP"]},
        )
        _run_apply(state, {"profile_deletions": {"certifications": ["AWS Certificate"]}})

        assert "aws certificate" not in [c.lower() for c in _get_certs(state)]
        assert "aws certificate" not in [
            c.lower() for c in state["parsed_resume_json"]["certifications"]
        ]
        refreshed = server._normalize_for_frontend(state)
        assert refreshed["certifications"] == ["PMP"]

    def test_sanitize_prevents_any_being_added_in_first_place(self):
        """End-to-end: LLM emits 'any' + real certs; sanitize strips 'any' before apply."""
        state = _make_candidate()
        add_updates = {"certifications": ["any", "Java Full Stack"]}
        sanitized = server._sanitize_profile_updates(add_updates)
        _run_apply(state, sanitized)
        certs = _get_certs(state)
        assert "any" not in [c.lower() for c in certs]
        assert any("java full stack" in c.lower() for c in certs)
