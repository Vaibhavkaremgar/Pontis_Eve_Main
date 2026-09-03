"""
Regression tests for Voice Intake certification extraction fix.

Covers:
1. Candidate mentions one certification -> appears in summary and profile
2. Candidate mentions multiple certifications -> all are saved
3. Existing certifications are preserved when new ones are added
4. A Voice Intake without certifications behaves exactly as before
"""
import asyncio
import json
import sys
import os

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import server


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_candidate(raw_data=None, parsed_resume_json=None):
    return {
        "id": "cand-cert-test",
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
        "raw_data": raw_data or {},
        "parsed_resume_json": json.dumps(parsed_resume_json) if parsed_resume_json else None,
    }


def _voice_data(certifications=None, **kwargs):
    base = {
        "summary": "",
        "skills": [],
        "preferred_roles": [],
        "availability": "",
        "certifications": certifications or [],
    }
    base.update(kwargs)
    return base


def _get_certs(merged):
    raw = merged.get("raw_data") or {}
    if isinstance(raw, str):
        raw = json.loads(raw)
    return raw.get("certifications") or []


def _make_fake_session(state):
    class FakeResult:
        def __init__(self, rows=None):
            self._rows = rows or []
        def mappings(self): return self
        def fetchone(self): return self._rows[0] if self._rows else None
        def scalar(self): return None

    class FakeSession:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def execute(self_inner, statement, params=None):
            sql = str(statement)
            params = params or {}
            if "FROM candidate_preferences" in sql and "SELECT" in sql:
                return FakeResult([])
            if "UPDATE candidates SET" in sql:
                if "raw_data" in params:
                    state["raw_data"] = json.loads(params["raw_data"])
                return FakeResult()
            if "INSERT INTO candidate_preferences" in sql:
                return FakeResult()
            if "UPDATE candidate_preferences SET" in sql:
                return FakeResult()
            return FakeResult()
        async def commit(self): return None

    return FakeSession


# ---------------------------------------------------------------------------
# 1. _merge_voice_into_profile
# ---------------------------------------------------------------------------

class TestMergeVoiceIntoCertifications:

    def test_single_cert_from_voice_appears_in_profile(self):
        """Candidate mentions one certification -> it appears in merged profile."""
        candidate = _make_candidate()
        voice = _voice_data(certifications=["AWS Certified Solutions Architect - Associate"])

        merged = server._merge_voice_into_profile(candidate, voice)

        assert "AWS Certified Solutions Architect - Associate" in _get_certs(merged)

    def test_multiple_certs_from_voice_all_saved(self):
        """Candidate mentions multiple certifications -> all are saved."""
        candidate = _make_candidate()
        voice = _voice_data(certifications=[
            "AWS Certified Solutions Architect - Associate",
            "Google Cloud Professional Data Engineer",
            "PMP",
        ])

        merged = server._merge_voice_into_profile(candidate, voice)

        certs = _get_certs(merged)
        assert "AWS Certified Solutions Architect - Associate" in certs
        assert "Google Cloud Professional Data Engineer" in certs
        assert "PMP" in certs

    def test_existing_resume_certs_preserved_when_voice_adds_new(self):
        """Existing resume certifications are not lost when voice adds new ones."""
        candidate = _make_candidate(
            parsed_resume_json={"certifications": ["Certified Scrum Master"]}
        )
        voice = _voice_data(certifications=["AWS Certified Solutions Architect - Associate"])

        merged = server._merge_voice_into_profile(candidate, voice)

        certs = _get_certs(merged)
        assert "Certified Scrum Master" in certs
        assert "AWS Certified Solutions Architect - Associate" in certs

    def test_existing_raw_data_certs_preserved_when_voice_adds_new(self):
        """Certifications already in raw_data are preserved when voice adds more."""
        candidate = _make_candidate(raw_data={"certifications": ["Google Cloud Associate"]})
        voice = _voice_data(certifications=["PMP"])

        merged = server._merge_voice_into_profile(candidate, voice)

        certs = _get_certs(merged)
        assert "Google Cloud Associate" in certs
        assert "PMP" in certs

    def test_no_duplicate_certs_when_voice_repeats_existing(self):
        """Certifications mentioned in voice that already exist are not duplicated."""
        candidate = _make_candidate(
            raw_data={"certifications": ["AWS Certified Solutions Architect - Associate"]}
        )
        voice = _voice_data(certifications=["aws certified solutions architect associate"])

        merged = server._merge_voice_into_profile(candidate, voice)

        assert len(_get_certs(merged)) == 1

    def test_voice_intake_without_certs_preserves_existing(self):
        """A Voice Intake with no certifications does not wipe existing ones."""
        candidate = _make_candidate(
            raw_data={"certifications": ["Certified Scrum Master"]},
            parsed_resume_json={"certifications": ["PMP"]},
        )
        voice = _voice_data(certifications=[])

        merged = server._merge_voice_into_profile(candidate, voice)

        certs = _get_certs(merged)
        assert "Certified Scrum Master" in certs
        assert "PMP" in certs

    def test_voice_intake_without_certs_does_not_affect_other_fields(self):
        """A Voice Intake with no certifications leaves skills and experience intact."""
        candidate = _make_candidate(raw_data={"certifications": ["PMP"]})
        candidate["skills"] = ["Python", "FastAPI"]
        voice = _voice_data(certifications=[], skills=["Java"])

        merged = server._merge_voice_into_profile(candidate, voice)

        assert "Python" in merged["skills"]
        assert "Java" in merged["skills"]
        assert "PMP" in _get_certs(merged)

    def test_cert_merge_runs_even_without_parsed_resume(self):
        """
        Regression: the old conditional guard was replaced with an unconditional
        merge. Verify it runs even when parsed_resume_json is absent.
        """
        candidate = _make_candidate()
        voice = _voice_data(certifications=["Azure Fundamentals"])

        merged = server._merge_voice_into_profile(candidate, voice)

        assert "Azure Fundamentals" in _get_certs(merged)


# ---------------------------------------------------------------------------
# 2. _persist_voice_intake_profile_state (mocked DB)
# ---------------------------------------------------------------------------

class TestPersistVoiceIntakeCertifications:

    def test_single_cert_persisted_to_raw_data(self, monkeypatch):
        """One certification from voice is written to raw_data.certifications."""
        state = {"raw_data": {}}
        monkeypatch.setattr(server, "SessionLocal", _make_fake_session(state))

        candidate = _make_candidate()
        voice = _voice_data(certifications=["AWS Certified Solutions Architect - Associate"])
        vi_state = {"status": "in_progress", "progress": 1, "completed_turns": []}

        asyncio.run(server._persist_voice_intake_profile_state(
            "cand-cert-test", candidate, voice, vi_state
        ))

        assert "AWS Certified Solutions Architect - Associate" in (
            state["raw_data"].get("certifications") or []
        )

    def test_multiple_certs_all_persisted(self, monkeypatch):
        """Multiple certifications from voice are all written to raw_data."""
        state = {"raw_data": {}}
        monkeypatch.setattr(server, "SessionLocal", _make_fake_session(state))

        candidate = _make_candidate()
        voice = _voice_data(certifications=["PMP", "AWS Certified Developer", "Google Cloud Associate"])
        vi_state = {"status": "in_progress", "progress": 1, "completed_turns": []}

        asyncio.run(server._persist_voice_intake_profile_state(
            "cand-cert-test", candidate, voice, vi_state
        ))

        saved = state["raw_data"].get("certifications") or []
        assert "PMP" in saved
        assert "AWS Certified Developer" in saved
        assert "Google Cloud Associate" in saved

    def test_existing_certs_preserved_when_voice_adds_new(self, monkeypatch):
        """Existing raw_data certifications survive when voice adds new ones."""
        state = {"raw_data": {}}
        monkeypatch.setattr(server, "SessionLocal", _make_fake_session(state))

        candidate = _make_candidate(
            raw_data={"certifications": ["Certified Scrum Master"]},
            parsed_resume_json={"certifications": ["PMP"]},
        )
        voice = _voice_data(certifications=["AWS Certified Solutions Architect - Associate"])
        vi_state = {"status": "in_progress", "progress": 1, "completed_turns": []}

        asyncio.run(server._persist_voice_intake_profile_state(
            "cand-cert-test", candidate, voice, vi_state
        ))

        saved = state["raw_data"].get("certifications") or []
        assert "Certified Scrum Master" in saved
        assert "PMP" in saved
        assert "AWS Certified Solutions Architect - Associate" in saved

    def test_no_certs_in_voice_preserves_existing(self, monkeypatch):
        """Voice Intake with no certifications does not wipe existing ones."""
        state = {"raw_data": {}}
        monkeypatch.setattr(server, "SessionLocal", _make_fake_session(state))

        candidate = _make_candidate(raw_data={"certifications": ["Google Cloud Professional"]})
        voice = _voice_data(certifications=[])
        vi_state = {"status": "in_progress", "progress": 1, "completed_turns": []}

        asyncio.run(server._persist_voice_intake_profile_state(
            "cand-cert-test", candidate, voice, vi_state
        ))

        assert "Google Cloud Professional" in (state["raw_data"].get("certifications") or [])

    def test_voice_intake_state_written_alongside_certifications(self, monkeypatch):
        """The voice_intake state is written to raw_data alongside certifications."""
        state = {"raw_data": {}}
        monkeypatch.setattr(server, "SessionLocal", _make_fake_session(state))

        candidate = _make_candidate()
        voice = _voice_data(certifications=["PMP"])
        vi_state = {"status": "completed", "progress": 7, "completed_turns": []}

        asyncio.run(server._persist_voice_intake_profile_state(
            "cand-cert-test", candidate, voice, vi_state
        ))

        assert state["raw_data"].get("voice_intake") == vi_state
        assert "PMP" in (state["raw_data"].get("certifications") or [])


# ---------------------------------------------------------------------------
# 3. _normalize_for_frontend exposes certifications to Dashboard
# ---------------------------------------------------------------------------

class TestNormalizeForFrontendCertifications:

    def _candidate(self, certs):
        return {
            "id": "cand-fe",
            "name": "Fe Test",
            "email": "",
            "phone": "",
            "current_role": "",
            "current_company": "",
            "location": "",
            "summary": "",
            "experience_years": None,
            "skills": [],
            "work_experience": [],
            "education": [],
            "raw_data": {"certifications": certs},
            "parsed_resume_json": None,
        }

    def test_single_cert_appears_in_frontend_profile(self):
        """_normalize_for_frontend exposes a single certification."""
        profile = server._normalize_for_frontend(
            self._candidate(["AWS Certified Solutions Architect - Associate"])
        )
        assert "AWS Certified Solutions Architect - Associate" in (profile.get("certifications") or [])

    def test_multiple_certs_all_appear_in_frontend_profile(self):
        """_normalize_for_frontend exposes all certifications."""
        profile = server._normalize_for_frontend(
            self._candidate(["PMP", "AWS Certified Developer", "Google Cloud Associate"])
        )
        certs = profile.get("certifications") or []
        assert "PMP" in certs
        assert "AWS Certified Developer" in certs
        assert "Google Cloud Associate" in certs

    def test_no_certs_stored_returns_empty_list(self):
        """When no certifications are stored, the frontend profile has an empty list."""
        profile = server._normalize_for_frontend(self._candidate([]))
        assert (profile.get("certifications") or []) == []


# ---------------------------------------------------------------------------
# 4. VOICE_EXTRACT_SYSTEM prompt contains certification instruction
# ---------------------------------------------------------------------------

class TestVoiceExtractSystemPrompt:

    def test_prompt_contains_certifications_field(self):
        """VOICE_EXTRACT_SYSTEM schema must include the certifications field."""
        assert '"certifications"' in server.VOICE_EXTRACT_SYSTEM

    def test_prompt_instructs_to_extract_all_certifications(self):
        """The prompt must tell the LLM to extract ALL certifications."""
        prompt_lower = server.VOICE_EXTRACT_SYSTEM.lower()
        assert "extract all" in prompt_lower

    def test_prompt_instructs_not_to_omit_incidental_mentions(self):
        """The prompt must tell the LLM not to omit certifications mentioned in passing."""
        assert "mentioned in passing" in server.VOICE_EXTRACT_SYSTEM or \
               "incidentally" in server.VOICE_EXTRACT_SYSTEM
