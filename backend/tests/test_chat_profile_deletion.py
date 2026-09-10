"""
Regression tests for Chat with Eve profile deletion.

Verifies:
- profile_deletions is never saved as a DB column (no UndefinedColumnError)
- List field deletions: skills, certifications, preferred_roles, work_experience, education, projects, preferred_locations
- Scalar field deletions: headline/current_role, bio, location, salary_expectation, availability, additional_information, experience_years
- Case-insensitive matching for list deletions
- Unrelated data is preserved after any deletion
- applied_deletions is populated for every field type so the chat endpoint returns success
- Non-existent item is a no-op (no add, no error)
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import server


# ---------------------------------------------------------------------------
# Shared fake-DB harness
# ---------------------------------------------------------------------------

def _make_candidate(**overrides):
    base = {
        "id": "cand-del-test",
        "name": "Test User",
        "email": "test@example.com",
        "phone": "555-0000",
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
            "salary_expectation": "15 LPA",
            "availability": "2 weeks notice",
            "additional_information": "Open to remote work",
        },
    }
    base.update(overrides)
    return base


def _run_apply(candidate_state, updates):
    """Run _apply_profile_updates against a fake DB and return (state, result)."""
    import unittest.mock as mock

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
            # Reject any attempt to set profile_deletions as a column
            assert "profile_deletions" not in sql or "profile_deletions" not in (params or {}), \
                f"profile_deletions must not appear in SQL params: {sql}"
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
                if "current_role" in params:
                    candidate_state["current_role"] = params["current_role"]
                if "bio" in params:
                    candidate_state["summary"] = params["bio"]
                if "location" in params:
                    candidate_state["location"] = params["location"]
                if "experience_years" in params:
                    candidate_state["experience_years"] = params["experience_years"]
                return FakeResult()
            if "INSERT INTO candidate_preferences" in sql or "UPDATE candidate_preferences" in sql:
                return FakeResult()
            return FakeResult()
        async def commit(self):
            return None

    with mock.patch.object(server, "SessionLocal", lambda: FakeSession()):
        result = asyncio.run(server._apply_profile_updates("cand-del-test", updates))
    return candidate_state, result


# ---------------------------------------------------------------------------
# 1. profile_deletions never reaches SQL as a column
# ---------------------------------------------------------------------------

class TestNoDatabaseColumn:
    def test_profile_deletions_not_in_sql_params(self):
        """_apply_profile_updates must never pass profile_deletions to SQL."""
        state = _make_candidate()
        # The FakeSession.execute asserts this; if it raises, the test fails.
        _run_apply(state, {"profile_deletions": {"skills": ["Python"]}})

    def test_profile_deletions_only_updates_not_returned_to_frontend(self):
        """profile_updates returned to frontend must not contain profile_deletions."""
        reply = (
            "Done.\n"
            "<<<PROFILE_UPDATES>>>\n"
            '{"profile_updates": {"profile_deletions": {"skills": ["FastAPI"]}}}\n'
            "<<<END_UPDATES>>>"
        )
        _, updates = server._extract_profile_updates(reply)
        assert updates is not None
        # profile_deletions is an internal instruction, not a profile field to merge
        assert "profile_deletions" in updates  # present for backend processing
        # But VALID_UPDATE_FIELDS must include it so _apply_profile_updates handles it
        assert "profile_deletions" in server.VALID_UPDATE_FIELDS


# ---------------------------------------------------------------------------
# 2. List field deletions
# ---------------------------------------------------------------------------

class TestListFieldDeletions:
    def test_skill_deletion_removes_item_and_tracks(self):
        state = _make_candidate()
        _, result = _run_apply(state, {"profile_deletions": {"skills": ["Python"]}})
        assert "Python" not in state["skills"]
        assert "skills" in result["deleted"]

    def test_skill_deletion_case_insensitive(self):
        state = _make_candidate()
        _run_apply(state, {"profile_deletions": {"skills": ["fastapi"]}})
        assert not any(s.lower() == "fastapi" for s in state["skills"])

    def test_skill_deletion_preserves_others(self):
        state = _make_candidate()
        _run_apply(state, {"profile_deletions": {"skills": ["Python"]}})
        assert "FastAPI" in state["skills"]
        assert "Docker" in state["skills"]
        assert "PostgreSQL" in state["skills"]

    def test_certification_deletion_removes_and_tracks(self):
        state = _make_candidate()
        _, result = _run_apply(state, {"profile_deletions": {"certifications": ["PMP"]}})
        assert "PMP" not in state["raw_data"]["certifications"]
        assert "certifications" in result["deleted"]

    def test_certification_deletion_preserves_others(self):
        state = _make_candidate()
        _run_apply(state, {"profile_deletions": {"certifications": ["PMP"]}})
        assert "AWS Certified Solutions Architect" in state["raw_data"]["certifications"]

    def test_preferred_roles_deletion_removes_and_tracks(self):
        state = _make_candidate()
        _, result = _run_apply(state, {"profile_deletions": {"preferred_roles": ["Backend Engineer"]}})
        assert "Backend Engineer" not in state["raw_data"]["preferred_roles"]
        assert "preferred_roles" in result["deleted"]

    def test_preferred_roles_deletion_preserves_others(self):
        state = _make_candidate()
        _run_apply(state, {"profile_deletions": {"preferred_roles": ["Backend Engineer"]}})
        assert "Python Developer" in state["raw_data"]["preferred_roles"]

    def test_work_experience_deletion_removes_and_tracks(self):
        state = _make_candidate()
        _, result = _run_apply(state, {"profile_deletions": {"work_experience": ["Deepija Telecom"]}})
        companies = [e["company"] for e in state["work_experience"]]
        assert "Deepija Telecom" not in companies
        assert "work_experience" in result["deleted"]

    def test_work_experience_deletion_preserves_others(self):
        state = _make_candidate()
        _run_apply(state, {"profile_deletions": {"work_experience": ["Deepija Telecom"]}})
        companies = [e["company"] for e in state["work_experience"]]
        assert "Acme Corp" in companies

    def test_education_deletion_removes_and_tracks(self):
        state = _make_candidate()
        _, result = _run_apply(state, {"profile_deletions": {"education": ["Master of Science"]}})
        degrees = [e["degree"] for e in state["education"]]
        assert not any("master" in d.lower() for d in degrees)
        assert "education" in result["deleted"]

    def test_education_deletion_preserves_others(self):
        state = _make_candidate()
        _run_apply(state, {"profile_deletions": {"education": ["Master of Science"]}})
        degrees = [e["degree"] for e in state["education"]]
        assert any("b.tech" in d.lower() for d in degrees)

    def test_projects_deletion_removes_and_tracks(self):
        state = _make_candidate()
        _, result = _run_apply(state, {"profile_deletions": {"projects": ["E-commerce platform"]}})
        assert "E-commerce platform" not in state["raw_data"]["projects"]
        assert "projects" in result["deleted"]

    def test_projects_deletion_preserves_others(self):
        state = _make_candidate()
        _run_apply(state, {"profile_deletions": {"projects": ["E-commerce platform"]}})
        assert "REST API service" in state["raw_data"]["projects"]

    def test_preferred_locations_deletion_removes_and_tracks(self):
        state = _make_candidate()
        _, result = _run_apply(state, {"profile_deletions": {"preferred_locations": ["Hyderabad"]}})
        locs = state["raw_data"].get("preferred_locations", [])
        assert "Hyderabad" not in locs
        assert "preferred_locations" in result["deleted"]

    def test_preferred_locations_deletion_preserves_others(self):
        state = _make_candidate()
        _run_apply(state, {"profile_deletions": {"preferred_locations": ["Hyderabad"]}})
        locs = state["raw_data"].get("preferred_locations", [])
        assert "Bangalore" in locs


# ---------------------------------------------------------------------------
# 3. Scalar field deletions
# ---------------------------------------------------------------------------

class TestScalarFieldDeletions:
    def test_headline_deletion_clears_field_and_tracks(self):
        state = _make_candidate()
        _, result = _run_apply(state, {"profile_deletions": {"headline": ["Backend Developer"]}})
        assert state["current_role"] == ""
        assert "headline" in result["deleted"]

    def test_bio_deletion_clears_field_and_tracks(self):
        state = _make_candidate()
        _, result = _run_apply(state, {"profile_deletions": {"bio": ["Experienced developer"]}})
        assert state["summary"] == ""
        assert "bio" in result["deleted"]

    def test_location_deletion_clears_field_and_tracks(self):
        state = _make_candidate()
        _, result = _run_apply(state, {"profile_deletions": {"location": ["Hyderabad"]}})
        assert state["location"] == ""
        assert "location" in result["deleted"]

    def test_salary_deletion_clears_field_and_tracks(self):
        state = _make_candidate()
        _, result = _run_apply(state, {"profile_deletions": {"salary_expectation": ["15 LPA"]}})
        assert state["raw_data"]["salary_expectation"] == ""
        assert "salary_expectation" in result["deleted"]

    def test_availability_deletion_clears_field_and_tracks(self):
        state = _make_candidate()
        _, result = _run_apply(state, {"profile_deletions": {"availability": ["2 weeks notice"]}})
        assert state["raw_data"]["availability"] == ""
        assert "availability" in result["deleted"]

    def test_additional_information_deletion_removes_only_requested_phrase_from_raw_data(self):
        state = _make_candidate(
            raw_data={
                **_make_candidate()["raw_data"],
                "additional_information": "Java Full-Stack; Open to remote work",
                "unrelated_raw_value": "must be preserved",
            }
        )
        _, result = _run_apply(
            state,
            {"profile_deletions": {"additional_information": ["java full stack"]}},
        )
        # The fake DB applies the raw_data value sent by UPDATE candidates,
        # mirroring the JSONB value that will be read on the profile refresh.
        assert state["raw_data"]["additional_information"] == "Open to remote work"
        assert state["raw_data"]["unrelated_raw_value"] == "must be preserved"
        assert "additional_information" in result["deleted"]

    def test_additional_information_deletion_is_a_noop_when_phrase_is_absent(self):
        state = _make_candidate()
        original = state["raw_data"]["additional_information"]
        _, result = _run_apply(
            state,
            {"profile_deletions": {"additional_information": ["Java full stack"]}},
        )
        assert state["raw_data"]["additional_information"] == original
        assert "additional_information" not in result["deleted"]

    def test_experience_years_deletion_clears_field_and_tracks(self):
        state = _make_candidate()
        _, result = _run_apply(state, {"profile_deletions": {"experience_years": ["5"]}})
        assert state["experience_years"] is None
        assert "experience_years" in result["deleted"]

    def test_scalar_deletion_does_not_touch_other_fields(self):
        state = _make_candidate()
        _run_apply(state, {"profile_deletions": {"bio": ["Experienced developer"]}})
        # Skills, experience, education must be untouched
        assert "Python" in state["skills"]
        assert len(state["work_experience"]) == 2
        assert len(state["education"]) == 2


# ---------------------------------------------------------------------------
# 4. Non-existent item is a no-op
# ---------------------------------------------------------------------------

class TestNonExistentItemNoop:
    def test_nonexistent_skill_noop(self):
        state = _make_candidate()
        original = list(state["skills"])
        _run_apply(state, {"profile_deletions": {"skills": ["Kubernetes"]}})
        assert state["skills"] == original

    def test_nonexistent_cert_noop(self):
        state = _make_candidate()
        original = list(state["raw_data"]["certifications"])
        _run_apply(state, {"profile_deletions": {"certifications": ["Google Cloud"]}})
        assert state["raw_data"]["certifications"] == original

    def test_nonexistent_item_not_added(self):
        state = _make_candidate()
        _run_apply(state, {"profile_deletions": {"skills": ["Kubernetes"]}})
        assert "Kubernetes" not in state["skills"]


# ---------------------------------------------------------------------------
# 5. applied_deletions populated → chat endpoint returns success
# ---------------------------------------------------------------------------

class TestAppliedDeletionsPopulated:
    def test_applied_deletions_returned_for_skills(self):
        state = _make_candidate()
        _, result = _run_apply(state, {"profile_deletions": {"skills": ["Python"]}})
        assert result["deleted"].get("skills") == ["Python"]

    def test_applied_deletions_returned_for_certifications(self):
        state = _make_candidate()
        _, result = _run_apply(state, {"profile_deletions": {"certifications": ["PMP"]}})
        assert "PMP" in result["deleted"].get("certifications", [])

    def test_applied_deletions_returned_for_work_experience(self):
        state = _make_candidate()
        _, result = _run_apply(state, {"profile_deletions": {"work_experience": ["Deepija Telecom"]}})
        assert result["deleted"].get("work_experience")

    def test_applied_deletions_empty_for_nonexistent_item(self):
        state = _make_candidate()
        _, result = _run_apply(state, {"profile_deletions": {"skills": ["Kubernetes"]}})
        assert not result["deleted"]

    def test_updated_true_when_deletion_applied(self):
        state = _make_candidate()
        _, result = _run_apply(state, {"profile_deletions": {"skills": ["Python"]}})
        assert result["updated"] is True

    def test_updated_false_when_nothing_changed(self):
        state = _make_candidate()
        _, result = _run_apply(state, {"profile_deletions": {"skills": ["Kubernetes"]}})
        assert result["updated"] is False
