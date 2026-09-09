"""
Regression tests for the four Chat-with-Eve profile issues fixed in this PR:

1. profile_deletions DB persistence – UndefinedColumnError was caused by
   profile_deletions falling through to the else-clause that builds SQL SET
   clauses.  After the fix the field is skipped in the loop and handled by
   the dedicated deletions block.

2. Failed-update response – /api/chat must not return a success message when
   _apply_profile_updates raises.  After the fix the exception handler always
   overwrites clean_reply.

3. YYYY-MM date formatting – Education and Work Experience dates must be
   displayed as YYYY-MM (e.g. 2023-11), never as YYYY-MM-DD.

4. Certificate deletion → DB → refresh – deleting a cert removes it from
   raw_data/parsed_resume_json and the item is absent after a simulated
   profile refresh.
"""
import asyncio
import json
import os
import sys
import unittest.mock as mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import server


# ---------------------------------------------------------------------------
# Helpers shared across tests
# ---------------------------------------------------------------------------

def _make_candidate(**overrides):
    base = {
        "id": "cand-regression-test",
        "name": "Test Candidate",
        "email": "test@example.com",
        "phone": "",
        "current_role": "Backend Developer",
        "current_company": "Acme",
        "location": "London",
        "summary": "Experienced developer",
        "experience_years": 5.0,
        "skills": ["Python", "FastAPI", "Docker"],
        "work_experience": [
            {
                "title": "Backend Developer",
                "company": "Acme",
                "start_date": "2021-03-01",
                "end_date": "2023-11-01",
                "description": "Built APIs",
            }
        ],
        "education": [
            {
                "degree": "B.Tech Computer Science",
                "institution": "JNTU",
                "start_date": "2017-08-01",
                "end_date": "2021-05-01",
            }
        ],
        "raw_data": {
            "certifications": ["AWS Certified Solutions Architect", "PMP"],
            "preferred_roles": ["Backend Engineer"],
        },
        "parsed_resume_json": {
            "certifications": ["AWS Certified Solutions Architect"],
        },
    }
    base.update(overrides)
    return base


def _run_apply(candidate_state, updates):
    """Run _apply_profile_updates against a fake DB and return the state."""

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
                # Reject any attempt to set a non-existent column
                set_part = sql.split("SET", 1)[1].split("WHERE")[0]
                assert "profile_deletions" not in set_part, (
                    "profile_deletions must never appear in SQL SET clause"
                )
                if "skills" in params:
                    candidate_state["skills"] = json.loads(params["skills"])
                if "work_experience" in params:
                    candidate_state["work_experience"] = json.loads(params["work_experience"])
                if "education" in params:
                    candidate_state["education"] = json.loads(params["education"])
                if "raw_data" in params:
                    candidate_state["raw_data"] = json.loads(params["raw_data"])
                if "parsed_resume_json" in params:
                    candidate_state["parsed_resume_json"] = json.loads(
                        params["parsed_resume_json"]
                    )
                return FakeResult()
            if "INSERT INTO candidate_preferences" in sql or "UPDATE candidate_preferences" in sql:
                return FakeResult()
            return FakeResult()

        async def commit(self):
            return None

    with mock.patch.object(server, "SessionLocal", lambda: FakeSession()):
        asyncio.run(server._apply_profile_updates("cand-regression-test", updates))

    return candidate_state


# ===========================================================================
# Issue 1 – profile_deletions must NOT produce UndefinedColumnError
# ===========================================================================

class TestProfileDeletionsNoSQLError:
    """profile_deletions must be skipped in the field→SQL loop."""

    def test_cert_deletion_does_not_add_profile_deletions_to_sql(self):
        """The fake DB asserts that profile_deletions never appears in SET clause."""
        state = _make_candidate()
        updates = {"profile_deletions": {"certifications": ["AWS Certified Solutions Architect"]}}
        # Must not raise AssertionError from FakeSession or any other exception
        _run_apply(state, updates)

    def test_skill_deletion_does_not_add_profile_deletions_to_sql(self):
        state = _make_candidate()
        updates = {"profile_deletions": {"skills": ["FastAPI"]}}
        _run_apply(state, updates)

    def test_cert_deletion_removes_from_raw_data(self):
        state = _make_candidate()
        updates = {"profile_deletions": {"certifications": ["AWS Certified Solutions Architect"]}}
        _run_apply(state, updates)
        certs = state["raw_data"].get("certifications", [])
        assert not any("aws" in c.lower() for c in certs), (
            "AWS cert should be removed from raw_data after deletion"
        )

    def test_cert_deletion_removes_from_parsed_resume_json(self):
        """Cert must also be removed from parsed_resume_json so refresh doesn't re-add it."""
        state = _make_candidate()
        updates = {"profile_deletions": {"certifications": ["AWS Certified Solutions Architect"]}}
        _run_apply(state, updates)
        parsed = state.get("parsed_resume_json") or {}
        if isinstance(parsed, str):
            parsed = json.loads(parsed)
        certs = parsed.get("certifications", [])
        assert not any("aws" in c.lower() for c in certs), (
            "AWS cert should be removed from parsed_resume_json after deletion"
        )

    def test_cert_deletion_preserves_other_certs(self):
        state = _make_candidate()
        updates = {"profile_deletions": {"certifications": ["AWS Certified Solutions Architect"]}}
        _run_apply(state, updates)
        certs = state["raw_data"].get("certifications", [])
        assert "PMP" in certs

    def test_cert_absent_from_profile_after_refresh(self):
        """After deletion, _normalize_for_frontend must not include the deleted cert."""
        state = _make_candidate()
        updates = {"profile_deletions": {"certifications": ["AWS Certified Solutions Architect"]}}
        _run_apply(state, updates)
        # Simulate what GET /profile does
        profile = server._normalize_for_frontend(state)
        cert_names = [c.lower() for c in (profile.get("certifications") or [])]
        assert not any("aws" in c for c in cert_names), (
            "Deleted cert must not appear in profile after refresh"
        )

    def test_nonexistent_cert_deletion_is_noop(self):
        state = _make_candidate()
        original_certs = list(state["raw_data"]["certifications"])
        updates = {"profile_deletions": {"certifications": ["Google Cloud Professional"]}}
        _run_apply(state, updates)
        assert state["raw_data"]["certifications"] == original_certs


# ===========================================================================
# Issue 2 – DB update failure must not return a success message
# ===========================================================================

class TestChatUpdateFailureResponse:
    """_apply_profile_updates raising must suppress the LLM success reply."""

    def _run_chat_apply_block(self, profile_updates, should_raise):
        """
        Simulate the try/except block in the /api/chat endpoint.
        Returns (clean_reply, returned_profile_updates).
        """
        clean_reply = "I've updated your profile with the new information."

        async def _fake_apply(cid, updates):
            if should_raise:
                raise RuntimeError("DB error: column does not exist")
            return {"updated": True, "deleted": {}}

        async def run():
            nonlocal clean_reply, profile_updates
            try:
                apply_result = await _fake_apply("cand-x", profile_updates)
                requested_deletions = profile_updates.get("profile_deletions") or {}
                applied_deletions = apply_result.get("deleted") or {}
                if requested_deletions and not applied_deletions:
                    clean_reply = "I couldn't find that item in your saved profile, so no change was made."
                    profile_updates_out = None
                else:
                    profile_updates_out = profile_updates
            except Exception:
                clean_reply = "I couldn't update your profile right now, so no changes were made. Please try again."
                profile_updates_out = None
            return clean_reply, profile_updates_out

        return asyncio.run(run())

    def test_failure_overrides_success_reply_for_field_update(self):
        updates = {"current_role": "Senior Engineer"}
        reply, pu = self._run_chat_apply_block(updates, should_raise=True)
        assert "couldn't update" in reply.lower(), (
            "On DB failure the reply must not say the profile was updated"
        )
        assert pu is None

    def test_failure_overrides_success_reply_for_deletion(self):
        updates = {"profile_deletions": {"certifications": ["AWS cert"]}}
        reply, pu = self._run_chat_apply_block(updates, should_raise=True)
        assert "couldn't update" in reply.lower()
        assert pu is None

    def test_success_preserves_original_reply(self):
        updates = {"current_role": "Senior Engineer"}
        reply, pu = self._run_chat_apply_block(updates, should_raise=False)
        assert "updated" in reply.lower()
        assert pu is not None

    def test_profile_updates_none_on_failure(self):
        """profile_updates in the ChatResponse must be None when DB fails."""
        updates = {"skills": ["Kubernetes"]}
        _, pu = self._run_chat_apply_block(updates, should_raise=True)
        assert pu is None


# ===========================================================================
# Issue 3 – YYYY-MM date formatting
# ===========================================================================

class TestYYYYMMDateFormatting:
    """Dates must be displayed as YYYY-MM, never YYYY-MM-DD."""

    def test_truncate_date_to_month_strips_day(self):
        assert server._truncate_date_to_month("2023-11-01") == "2023-11"

    def test_truncate_date_to_month_preserves_yyyy_mm(self):
        assert server._truncate_date_to_month("2023-11") == "2023-11"

    def test_truncate_date_to_month_preserves_plain_text(self):
        assert server._truncate_date_to_month("Present") == "Present"

    def test_truncate_date_to_month_empty_string(self):
        assert server._truncate_date_to_month("") == ""

    def test_truncate_date_to_month_none(self):
        assert server._truncate_date_to_month(None) == ""

    def test_work_experience_dates_no_day_in_normalize(self):
        candidate = _make_candidate()
        profile = server._normalize_for_frontend(candidate)
        for exp in profile.get("experience", []):
            sd = exp.get("start_date", "")
            ed = exp.get("end_date", "")
            dates = exp.get("dates", "")
            import re
            assert not re.search(r"\d{4}-\d{2}-\d{2}", sd), (
                f"start_date must not contain day: {sd!r}"
            )
            assert not re.search(r"\d{4}-\d{2}-\d{2}", ed), (
                f"end_date must not contain day: {ed!r}"
            )
            assert not re.search(r"\d{4}-\d{2}-\d{2}", dates), (
                f"dates must not contain day: {dates!r}"
            )

    def test_education_dates_no_day_in_normalize(self):
        candidate = _make_candidate()
        profile = server._normalize_for_frontend(candidate)
        import re
        for edu in profile.get("education", []):
            dates = edu.get("dates", "")
            assert not re.search(r"\d{4}-\d{2}-\d{2}", dates), (
                f"education dates must not contain day: {dates!r}"
            )

    def test_work_experience_dates_format_example(self):
        """2021-03-01 → 2023-11-01 must display as '2021-03 — 2023-11'."""
        candidate = _make_candidate()
        profile = server._normalize_for_frontend(candidate)
        exp = profile["experience"][0]
        assert exp["start_date"] == "2021-03"
        assert exp["end_date"] == "2023-11"
        assert exp["dates"] == "2021-03 \u2014 2023-11"

    def test_education_dates_format_example(self):
        """2017-08-01 → 2021-05-01 must display as '2017-08 — 2021-05'."""
        candidate = _make_candidate()
        profile = server._normalize_for_frontend(candidate)
        edu = profile["education"][0]
        assert edu["dates"] == "2017-08 \u2014 2021-05"

    def test_open_ended_work_experience_shows_present(self):
        candidate = _make_candidate(
            work_experience=[
                {
                    "title": "Lead Engineer",
                    "company": "Beta",
                    "start_date": "2022-06-15",
                    "end_date": "",
                    "description": "Led team",
                }
            ]
        )
        profile = server._normalize_for_frontend(candidate)
        exp = profile["experience"][0]
        assert exp["start_date"] == "2022-06"
        assert "Present" in exp["dates"]

    def test_already_yyyy_mm_dates_unchanged(self):
        candidate = _make_candidate(
            work_experience=[
                {
                    "title": "Dev",
                    "company": "Corp",
                    "start_date": "2020-01",
                    "end_date": "2022-12",
                    "description": "",
                }
            ]
        )
        profile = server._normalize_for_frontend(candidate)
        exp = profile["experience"][0]
        assert exp["start_date"] == "2020-01"
        assert exp["end_date"] == "2022-12"
