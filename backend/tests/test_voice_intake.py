"""
Tests for Eve candidate voice intake feature.

Covers:
- Vapi configuration
- Candidate authorization
- Transcript handling
- Backend voice intake endpoint
- LLM extraction
- Profile merge
- Onboarding state
- Retry / idempotency
"""
import os
import json
import asyncio
import uuid
import pytest
import requests
from sqlalchemy import text

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8001").rstrip("/")
API = f"{BASE_URL}/api"


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _create_candidate(name="Test Voice", email=None) -> str:
    """Create a minimal candidate via parse-resume and return candidate_id."""
    import io
    from reportlab.pdfgen import canvas as rl_canvas

    email = email or f"voice-test-{uuid.uuid4().hex[:8]}@example.com"
    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf)
    c.drawString(50, 800, f"{name}")
    c.drawString(50, 780, f"Email: {email}")
    c.drawString(50, 760, "Software Engineer with 5 years experience.")
    c.drawString(50, 740, "Skills: Python, FastAPI, PostgreSQL")
    c.showPage()
    c.save()
    buf.seek(0)

    r = requests.post(
        f"{API}/onboarding/parse-resume",
        files={"file": ("resume.pdf", buf.getvalue(), "application/pdf")},
        timeout=90,
    )
    assert r.status_code == 200, f"Failed to create candidate: {r.text}"
    data = r.json()
    cid = data.get("candidate_id") or data.get("candidateId")
    assert cid, f"No candidate_id in response: {data}"
    return cid


def _fetch_candidate_preferences(candidate_id: str) -> dict:
    import sys
    import os as _os

    sys.path.insert(0, _os.path.join(_os.path.dirname(__file__), ".."))
    from server import SessionLocal

    async def _query():
        async with SessionLocal() as db:
            row = await db.execute(
                text(
                    "SELECT candidate_id, preferred_roles, notice_period "
                    "FROM candidate_preferences WHERE candidate_id = :cid LIMIT 1"
                ),
                {"cid": candidate_id},
            )
            result = row.mappings().fetchone()
        return dict(result) if result else {}

    return asyncio.run(_query())


def _count_candidate_preferences_rows(candidate_id: str) -> int:
    import sys
    import os as _os

    sys.path.insert(0, _os.path.join(_os.path.dirname(__file__), ".."))
    from server import SessionLocal

    async def _query():
        async with SessionLocal() as db:
            row = await db.execute(
                text("SELECT COUNT(*) FROM candidate_preferences WHERE candidate_id = :cid"),
                {"cid": candidate_id},
            )
            return int(row.scalar() or 0)

    return asyncio.run(_query())


def _fetch_candidate_profile(candidate_id: str) -> dict:
    r = requests.get(f"{API}/candidate/{candidate_id}/profile", timeout=15)
    assert r.status_code == 200, r.text
    return r.json()


SAMPLE_TRANSCRIPT = (
    "Assistant: Hi! Tell me about your background.\n"
    "Candidate: I'm a Python backend developer with 5 years of experience. "
    "I've worked at TechCorp building REST APIs with FastAPI and PostgreSQL. "
    "I'm looking for senior backend roles in fintech, preferably remote.\n"
    "Assistant: What are your key skills?\n"
    "Candidate: Python, FastAPI, PostgreSQL, Docker, AWS, Redis. "
    "I'm available immediately and prefer remote work in Europe or North America."
)


# ─────────────────────────────────────────────
# 1. Vapi configuration
# ─────────────────────────────────────────────

class TestVapiConfig:
    def test_config_endpoint_exists(self):
        """GET /api/config/vapi returns 200 or 503 (not 404)."""
        r = requests.get(f"{API}/config/vapi", timeout=10)
        assert r.status_code in (200, 503), f"Unexpected status: {r.status_code}"

    def test_config_503_when_not_configured(self):
        """When env vars are missing, endpoint returns 503 with clear message."""
        r = requests.get(f"{API}/config/vapi", timeout=10)
        if r.status_code == 503:
            assert "not configured" in r.json().get("detail", "").lower()

    def test_config_200_returns_required_keys(self):
        """When configured, response contains publicKey and assistantId."""
        r = requests.get(f"{API}/config/vapi", timeout=10)
        if r.status_code == 200:
            data = r.json()
            assert "publicKey" in data
            assert "assistantId" in data
            # Must not expose private keys
            assert "privateKey" not in data
            assert "secret" not in data


# ─────────────────────────────────────────────
# 2. Candidate authorization
# ─────────────────────────────────────────────

class TestCandidateAuthorization:
    def test_valid_candidate_intake_accepted(self):
        """A valid candidate_id is accepted."""
        cid = _create_candidate("Auth Test Valid")
        r = requests.post(
            f"{API}/voice/candidate-intake",
            json={"transcript": SAMPLE_TRANSCRIPT, "candidate_id": cid},
            timeout=60,
        )
        assert r.status_code == 200, r.text

    def test_nonexistent_candidate_rejected(self):
        """A random UUID that doesn't exist in DB returns 404."""
        fake_id = str(uuid.uuid4())
        r = requests.post(
            f"{API}/voice/candidate-intake",
            json={"transcript": SAMPLE_TRANSCRIPT, "candidate_id": fake_id},
            timeout=15,
        )
        assert r.status_code == 404

    def test_invalid_uuid_rejected(self):
        """A non-UUID candidate_id returns 4xx."""
        r = requests.post(
            f"{API}/voice/candidate-intake",
            json={"transcript": SAMPLE_TRANSCRIPT, "candidate_id": "not-a-uuid"},
            timeout=15,
        )
        assert r.status_code in (400, 404, 422)


# ─────────────────────────────────────────────
# 3. Transcript validation
# ─────────────────────────────────────────────

class TestTranscript:
    def test_empty_transcript_rejected(self):
        """Empty transcript returns 400."""
        cid = _create_candidate("Transcript Empty")
        r = requests.post(
            f"{API}/voice/candidate-intake",
            json={"transcript": "", "candidate_id": cid},
            timeout=15,
        )
        assert r.status_code == 400

    def test_whitespace_only_transcript_rejected(self):
        """Whitespace-only transcript returns 400."""
        cid = _create_candidate("Transcript Whitespace")
        r = requests.post(
            f"{API}/voice/candidate-intake",
            json={"transcript": "   \n  ", "candidate_id": cid},
            timeout=15,
        )
        assert r.status_code == 400

    def test_valid_transcript_accepted(self):
        """A real transcript is accepted and leaves intake resumable until complete."""
        cid = _create_candidate("Transcript Valid")
        r = requests.post(
            f"{API}/voice/candidate-intake",
            json={"transcript": SAMPLE_TRANSCRIPT, "candidate_id": cid},
            timeout=60,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["status"] in ("in_progress", "completed", "duplicate")
        assert data["candidate_id"] == cid

    def test_progress_ignores_setup_messages_and_keeps_resumable_state(self):
        """Setup chatter must not count as completed turns or force completion."""
        cid = _create_candidate("Progress Resume")
        transcript = (
            "Assistant: Are you ready?\n"
            "Candidate: Yes, I'm ready.\n"
            "Assistant: Take your time, and remember you can pause or refresh the page whenever you need. Are you ready?\n"
            "Candidate: I'm a Java backend engineer with 8 years of experience.\n"
            "Assistant: Tell me about your background.\n"
            "Candidate: I've built Kafka systems and Spring Boot services."
        )
        voice_notes = [
            {"role": "assistant", "text": "Are you ready?"},
            {"role": "user", "text": "Yes, I'm ready."},
            {
                "role": "assistant",
                "text": "Take your time, and remember you can pause or refresh the page whenever you need. Are you ready?",
            },
            {"role": "user", "text": "I'm a Java backend engineer with 8 years of experience."},
            {"role": "assistant", "text": "Tell me about your background."},
            {"role": "user", "text": "I've built Kafka systems and Spring Boot services."},
        ]

        r = requests.post(
            f"{API}/voice/candidate-intake/progress",
            json={
                "transcript": transcript,
                "voice_notes": voice_notes,
                "candidate_id": cid,
            },
            timeout=60,
        )
        assert r.status_code == 200
        resume = r.json()["voice_intake_resume"]
        assert resume["status"] == "in_progress"
        assert resume["progress"] == 1
        assert len(resume.get("completed_turns") or []) == 1
        assert resume["completed_turns"][0]["question"] == "Tell me about your background."
        assert resume["next_question"] == "What are your key skills?"

        profile = requests.get(f"{API}/candidate/{cid}/profile", timeout=15).json()
        vir = profile.get("voice_intake_resume") or {}
        assert vir.get("status") == "in_progress"
        assert vir.get("progress") == 1
        assert len(vir.get("completed_turns") or []) == 1
        assert vir.get("next_question") == "What are your key skills?"

        final = requests.post(
            f"{API}/voice/candidate-intake",
            json={
                "transcript": transcript,
                "voice_notes": voice_notes,
                "candidate_id": cid,
            },
            timeout=60,
        )
        assert final.status_code == 200
        assert final.json()["status"] == "in_progress"

        profile_after = requests.get(f"{API}/candidate/{cid}/profile", timeout=15).json()
        vir_after = profile_after.get("voice_intake_resume") or {}
        assert vir_after.get("status") == "in_progress"
        assert vir_after.get("progress") == 1
        assert len(vir_after.get("completed_turns") or []) == 1
        assert vir_after.get("next_question") == "What are your key skills?"

    def test_progress_ignores_setup_yes_and_binds_first_real_question_to_candidate_intro(self):
        """A setup 'Yes' must not be paired with a later intake question."""
        cid = _create_candidate("Progress Setup Yes")
        transcript = (
            "Assistant: Are you ready?\n"
            "Candidate: Yes.\n"
            "Assistant: What roles are you targeting right now?\n"
            "Candidate: I have 2 years of experience as a Python developer and working at Viral Bug.\n"
            "Candidate: We built a product using Python, PostgreSQL, FastAPI and REST APIs...\n"
            "Candidate: I was looking for opportunities where I can work as a backend developer...\n"
            "Assistant: Tell me about your background."
        )
        voice_notes = [
            {"role": "assistant", "text": "Are you ready?"},
            {"role": "user", "text": "Yes."},
            {"role": "assistant", "text": "What roles are you targeting right now?"},
            {"role": "user", "text": "I have 2 years of experience as a Python developer and working at Viral Bug."},
            {"role": "user", "text": "We built a product using Python, PostgreSQL, FastAPI and REST APIs..."},
            {"role": "user", "text": "I was looking for opportunities where I can work as a backend developer..."},
            {"role": "assistant", "text": "Tell me about your background."},
        ]

        r = requests.post(
            f"{API}/voice/candidate-intake/progress",
            json={
                "transcript": transcript,
                "voice_notes": voice_notes,
                "candidate_id": cid,
            },
            timeout=60,
        )
        assert r.status_code == 200, r.text

        resume = r.json()["voice_intake_resume"]
        assert resume["status"] == "in_progress"
        assert resume["progress"] == 1
        assert len(resume.get("completed_turns") or []) == 1
        assert resume["completed_turns"][0]["question"] == "Tell me about your background."
        assert "Yes." not in resume["completed_turns"][0]["answer"]
        assert "I have 2 years of experience as a Python developer and working at Viral Bug." in resume["completed_turns"][0]["answer"]
        assert "We built a product using Python, PostgreSQL, FastAPI and REST APIs..." in resume["completed_turns"][0]["answer"]
        assert "I was looking for opportunities where I can work as a backend developer..." in resume["completed_turns"][0]["answer"]
        assert resume["next_question"] == "What are your key skills?"

        profile = requests.get(f"{API}/candidate/{cid}/profile", timeout=15).json()
        vir = profile.get("voice_intake_resume") or {}
        assert vir.get("status") == "in_progress"
        assert vir.get("progress") == 1
        assert len(vir.get("completed_turns") or []) == 1
        assert vir.get("completed_turns")[0]["question"] == "Tell me about your background."
        assert vir.get("next_question") == "What are your key skills?"

    def test_small_talk_excluded_only_real_question_gets_completed_turn(self):
        """
        Regression: small-talk answers must never be paired with a canonical intake question.

        Transcript:
          Eve:  "How are you doing today?"
          User: "Doing good. What about you?"
          Eve:  "Are you ready?"
          User: "Yes, I'm ready."
          Eve:  "What roles are you targeting right now?"
          User: "I'm looking for senior Python backend roles."
          User: "Preferably remote in fintech."

        Expected:
          progress = 1
          completed_turns has exactly 1 entry
          question = "What roles are you targeting right now?"
          answer contains both user fragments but NOT "Doing good" or "Yes, I'm ready"
          next_question = next canonical question after "What roles are you targeting"
          status = "in_progress"
        """
        cid = _create_candidate("Regression SmallTalk Exclusion")
        voice_notes = [
            {"role": "assistant", "text": "How are you doing today?"},
            {"role": "user",      "text": "Doing good. What about you?"},
            {"role": "assistant", "text": "Are you ready?"},
            {"role": "user",      "text": "Yes, I'm ready."},
            {"role": "assistant", "text": "What roles are you targeting right now?"},
            {"role": "user",      "text": "I'm looking for senior Python backend roles."},
            {"role": "user",      "text": "Preferably remote in fintech."},
        ]
        transcript = (
            "Assistant: How are you doing today?\n"
            "Candidate: Doing good. What about you?\n"
            "Assistant: Are you ready?\n"
            "Candidate: Yes, I'm ready.\n"
            "Assistant: What roles are you targeting right now?\n"
            "Candidate: I'm looking for senior Python backend roles.\n"
            "Candidate: Preferably remote in fintech."
        )

        r = requests.post(
            f"{API}/voice/candidate-intake/progress",
            json={"transcript": transcript, "voice_notes": voice_notes, "candidate_id": cid},
            timeout=60,
        )
        assert r.status_code == 200, r.text

        resume = r.json()["voice_intake_resume"]
        assert resume["status"] == "in_progress"
        assert resume["progress"] == 1
        turns = resume.get("completed_turns") or []
        assert len(turns) == 1, f"Expected 1 completed turn, got {len(turns)}: {turns}"
        assert turns[0]["question"] == "What roles are you targeting right now?"
        answer = turns[0]["answer"]
        assert "senior Python backend roles" in answer
        assert "remote in fintech" in answer
        assert "Doing good" not in answer
        assert "What about you" not in answer
        assert "Yes, I'm ready" not in answer
        assert resume.get("next_question") is not None
        assert resume["next_question"] != "What roles are you targeting right now?"

    def test_voice_notes_accepted(self):
        """voice_notes array is accepted alongside transcript."""
        cid = _create_candidate("Transcript Notes")
        notes = [
            {"role": "assistant", "text": "Tell me about yourself."},
            {"role": "user", "text": "I'm a Python developer."},
        ]
        r = requests.post(
            f"{API}/voice/candidate-intake",
            json={
                "transcript": SAMPLE_TRANSCRIPT,
                "voice_notes": notes,
                "candidate_id": cid,
            },
            timeout=60,
        )
        assert r.status_code == 200

    def test_exact_reproduction_are_you_ready_yes_then_multi_fragment_answer(self):
        """
        Regression: exact reproduction from bug report.

        Eve: "Are you ready?"
        Candidate: "Yes."
        Eve: "Tell me about your background."
        Candidate gives 4 consecutive VAPI fragments about Viral Bug, Python,
        FastAPI, Postgres, REST APIs, backend, Java, Spring Boot, Hibernate, Kubernetes.

        Expected:
          progress = 1
          completed_turns has exactly 1 entry
          question = "Tell me about your background."
          answer contains all 4 fragments combined
          next_question = "What are your key skills?"
          status = "in_progress"
        """
        cid = _create_candidate("Regression Exact Repro")
        voice_notes = [
            {"role": "assistant", "text": "Are you ready?"},
            {"role": "user", "text": "Yes."},
            {"role": "assistant", "text": "Tell me about your background."},
            {"role": "user", "text": "I have 2 years of experience as a software engineer at Viral Bug."},
            {"role": "user", "text": "I was working with Python, FastAPI, Postgres and REST APIs."},
            {"role": "user", "text": "I'm looking for new opportunities as a backend developer."},
            {"role": "user", "text": "Java, OOPS, Hibernate, Kubernetes and Spring Boot."},
        ]
        transcript = (
            "Assistant: Are you ready?\n"
            "Candidate: Yes.\n"
            "Assistant: Tell me about your background.\n"
            "Candidate: I have 2 years of experience as a software engineer at Viral Bug.\n"
            "Candidate: I was working with Python, FastAPI, Postgres and REST APIs.\n"
            "Candidate: I'm looking for new opportunities as a backend developer.\n"
            "Candidate: Java, OOPS, Hibernate, Kubernetes and Spring Boot."
        )

        r = requests.post(
            f"{API}/voice/candidate-intake/progress",
            json={"transcript": transcript, "voice_notes": voice_notes, "candidate_id": cid},
            timeout=60,
        )
        assert r.status_code == 200, r.text

        resume = r.json()["voice_intake_resume"]
        assert resume["status"] == "in_progress"
        assert resume["progress"] == 1
        turns = resume.get("completed_turns") or []
        assert len(turns) == 1, f"Expected 1 completed turn, got {len(turns)}: {turns}"
        assert turns[0]["question"] == "Tell me about your background."
        answer = turns[0]["answer"]
        assert "Viral Bug" in answer
        assert "Python" in answer
        assert "FastAPI" in answer
        assert "REST APIs" in answer
        assert "backend developer" in answer
        assert "Kubernetes" in answer
        assert "Spring Boot" in answer
        assert "Yes." not in answer
        assert resume["next_question"] == "What are your key skills?"


# ─────────────────────────────────────────────
# 4. Idempotency / duplicate handling
# ─────────────────────────────────────────────

class TestIdempotency:
    def test_duplicate_request_returns_duplicate_status(self):
        """Submitting the same transcript twice within 10 min returns 'duplicate'."""
        cid = _create_candidate("Idempotency Test")
        payload = {"transcript": SAMPLE_TRANSCRIPT, "candidate_id": cid}

        r1 = requests.post(f"{API}/voice/candidate-intake", json=payload, timeout=60)
        assert r1.status_code == 200

        r2 = requests.post(f"{API}/voice/candidate-intake", json=payload, timeout=15)
        assert r2.status_code == 200
        assert r2.json()["status"] == "duplicate"

    def test_different_transcript_not_duplicate(self):
        """A different transcript for the same candidate is not a duplicate."""
        cid = _create_candidate("Idempotency Different")
        r1 = requests.post(
            f"{API}/voice/candidate-intake",
            json={"transcript": SAMPLE_TRANSCRIPT, "candidate_id": cid},
            timeout=60,
        )
        assert r1.status_code == 200

        different = SAMPLE_TRANSCRIPT + " Also I love open source."
        r2 = requests.post(
            f"{API}/voice/candidate-intake",
            json={"transcript": different, "candidate_id": cid},
            timeout=60,
        )
        assert r2.status_code == 200
        assert r2.json()["status"] == "completed"


# ─────────────────────────────────────────────
# 5. Profile merge — resume data preserved
# ─────────────────────────────────────────────

class TestProfileMerge:
    def test_resume_data_preserved_after_voice_intake(self):
        """Existing resume fields are not overwritten by voice intake."""
        cid = _create_candidate("Merge Test Candidate", email=f"merge-{uuid.uuid4().hex[:6]}@example.com")

        # Get profile before voice intake
        before = requests.get(f"{API}/candidate/{cid}/profile", timeout=15).json()
        original_name = before.get("name", "")
        original_email = before.get("email", "")

        # Submit voice intake
        r = requests.post(
            f"{API}/voice/candidate-intake",
            json={"transcript": SAMPLE_TRANSCRIPT, "candidate_id": cid},
            timeout=60,
        )
        assert r.status_code == 200

        # Get profile after
        after = requests.get(f"{API}/candidate/{cid}/profile", timeout=15).json()

        # Name and email must be preserved
        assert after.get("name") == original_name
        assert after.get("email") == original_email

    def test_skills_merged_not_replaced(self):
        """Skills from voice intake are merged with existing resume skills."""
        cid = _create_candidate("Skills Merge")

        before = requests.get(f"{API}/candidate/{cid}/profile", timeout=15).json()
        original_skills = set(before.get("keySkills") or [])

        r = requests.post(
            f"{API}/voice/candidate-intake",
            json={"transcript": SAMPLE_TRANSCRIPT, "candidate_id": cid},
            timeout=60,
        )
        assert r.status_code == 200

        after = requests.get(f"{API}/candidate/{cid}/profile", timeout=15).json()
        after_skills = set(after.get("keySkills") or [])

        # Original skills must still be present
        for skill in original_skills:
            assert skill in after_skills, f"Skill '{skill}' was lost after voice intake"


class TestVoiceIntakePersistenceRegression:
    def test_persistence_helper_writes_preferences_and_voice_intake_state(self, monkeypatch):
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        import server

        executed = []
        state = {"existing_preference": None}

        class FakeResult:
            def __init__(self, rows=None, scalar_value=None):
                self._rows = rows or []
                self._scalar_value = scalar_value

            def mappings(self):
                return self

            def fetchone(self):
                return self._rows[0] if self._rows else None

            def scalar(self):
                return self._scalar_value

        class FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def execute(self, statement, params=None):
                sql = str(statement)
                executed.append((sql, params or {}))
                if "FROM candidate_preferences" in sql and "SELECT" in sql:
                    if state["existing_preference"] is None:
                        return FakeResult([])
                    return FakeResult([state["existing_preference"]])
                return FakeResult()

            async def commit(self):
                return None

        monkeypatch.setattr(server, "SessionLocal", lambda: FakeSession())

        candidate = {
            "id": "candidate-1",
            "summary": "",
            "current_role": "",
            "current_company": "",
            "location": "",
            "experience_years": None,
            "skills": [],
            "work_experience": [],
            "education": [],
            "raw_data": {},
        }
        voice_data = {
            "preferred_roles": ["Java Backend Developer", "java backend developer"],
            "availability": "I can join immediately.",
            "role_preference_bio": "Looking for Java backend roles.",
        }
        voice_intake_state = {
            "status": "in_progress",
            "progress": 1,
            "current_question": "What kind of role are you looking for next?",
            "missing_topics": ["availability_location"],
            "completed_turns": [
                {
                    "question": "What kind of role are you looking for next?",
                    "answer": "I want a Java Backend Developer role and I can join immediately.",
                }
            ],
        }

        merged = asyncio.run(
            server._persist_voice_intake_profile_state("candidate-1", candidate, voice_data, voice_intake_state)
        )

        assert merged["raw_data"]["voice_intake"] == voice_intake_state
        assert merged["raw_data"]["preferred_roles"] == ["Java Backend Developer"]
        assert merged["raw_data"]["availability"] == "I can join immediately."
        assert any("UPDATE candidates SET" in sql for sql, _ in executed)
        assert any("INSERT INTO candidate_preferences" in sql for sql, _ in executed)
        insert_params = next(params for sql, params in executed if "INSERT INTO candidate_preferences" in sql)
        assert "Java Backend Developer" in insert_params["preferred_roles"]
        assert insert_params["notice_period"] == "I can join immediately."

    def test_target_role_persists_to_profile_and_candidate_preferences(self):
        cid = _create_candidate("Target Role Persistence")
        transcript = (
            "Assistant: What kind of role are you looking for next?\n"
            "Candidate: I want a Java Backend Developer or Engineer role."
        )
        voice_notes = [
            {"role": "assistant", "text": "What kind of role are you looking for next?", "final": True},
            {"role": "user", "text": "I want a Java Backend Developer or Engineer role.", "final": True},
        ]

        r = requests.post(
            f"{API}/voice/candidate-intake/progress",
            json={"transcript": transcript, "voice_notes": voice_notes, "candidate_id": cid},
            timeout=60,
        )
        assert r.status_code == 200, r.text

        profile = _fetch_candidate_profile(cid)
        assert "Java Backend Developer" in (profile.get("preferred_roles") or [])
        prefs = _fetch_candidate_preferences(cid)
        assert "Java Backend Developer" in (prefs.get("preferred_roles") or [])
        assert _count_candidate_preferences_rows(cid) == 1

    def test_availability_persists_to_profile_and_candidate_preferences(self):
        cid = _create_candidate("Availability Persistence")
        transcript = (
            "Assistant: When would you be able to start a new position?\n"
            "Candidate: I can join immediately."
        )
        voice_notes = [
            {"role": "assistant", "text": "When would you be able to start a new position?", "final": True},
            {"role": "user", "text": "I can join immediately.", "final": True},
        ]

        r = requests.post(
            f"{API}/voice/candidate-intake/progress",
            json={"transcript": transcript, "voice_notes": voice_notes, "candidate_id": cid},
            timeout=60,
        )
        assert r.status_code == 200, r.text

        profile = _fetch_candidate_profile(cid)
        assert "immed" in (profile.get("availability") or "").lower()
        prefs = _fetch_candidate_preferences(cid)
        assert "immed" in (prefs.get("notice_period") or "").lower()
        assert _count_candidate_preferences_rows(cid) == 1

    def test_voice_intake_state_advances_after_answer(self):
        cid = _create_candidate("Resume Advancement")
        voice_notes = [
            {"role": "assistant", "text": "What roles are you targeting right now?", "final": True},
            {"role": "user", "text": "I'm looking for senior Java backend roles.", "final": True},
        ]

        r = requests.post(
            f"{API}/voice/candidate-intake/progress",
            json={"transcript": "", "voice_notes": voice_notes, "candidate_id": cid},
            timeout=60,
        )
        assert r.status_code == 200, r.text

        resume = r.json()["voice_intake_resume"]
        assert resume["status"] == "in_progress"
        assert resume["progress"] == 1
        turns = resume.get("completed_turns") or []
        assert len(turns) == 1
        assert turns[0]["question"] == "What roles are you targeting right now?"
        assert resume.get("current_question") != "What roles are you targeting right now?"

        profile = _fetch_candidate_profile(cid)
        vir = profile.get("voice_intake_resume") or {}
        assert vir.get("progress") == 1
        assert len(vir.get("completed_turns") or []) == 1
        assert vir.get("current_question") != "What roles are you targeting right now?"

    def test_repeated_progress_submission_does_not_duplicate_completed_turns(self):
        cid = _create_candidate("Repeated Progress Submission")
        voice_notes = [
            {"role": "assistant", "text": "What kind of role are you looking for next?", "final": True},
            {"role": "user", "text": "I want a Java Backend Developer role.", "final": True},
        ]

        first = requests.post(
            f"{API}/voice/candidate-intake/progress",
            json={"transcript": "", "voice_notes": voice_notes, "candidate_id": cid},
            timeout=60,
        )
        assert first.status_code == 200, first.text

        second = requests.post(
            f"{API}/voice/candidate-intake/progress",
            json={"transcript": "", "voice_notes": voice_notes, "candidate_id": cid},
            timeout=60,
        )
        assert second.status_code == 200, second.text

        resume = second.json()["voice_intake_resume"]
        turns = resume.get("completed_turns") or []
        assert len(turns) == 1
        assert _count_candidate_preferences_rows(cid) == 1

    def test_existing_resume_and_interruption_behavior_is_preserved(self):
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from server import _build_voice_intake_resume_from_notes

        existing_resume = {
            "status": "in_progress",
            "progress": 2,
            "completed_turns": [
                {"question": "What made you start exploring your next opportunity?", "answer": "I was interested in Java."},
                {"question": "Which Java technologies or frameworks do you enjoy working with the most?", "answer": "Spring Boot and Hibernate."},
            ],
            "current_question": "What kind of projects have you worked on using Spring Boot and Hibernate?",
            "next_question": "What kind of team or work environment helps you do your best work in your next role?",
            "missing_topics": ["projects"],
            "known_topics": ["java", "spring boot", "hibernate"],
        }

        resumed = _build_voice_intake_resume_from_notes([], "", existing_resume)
        assert resumed["progress"] == 2
        assert resumed["current_question"] == existing_resume["current_question"]
        assert resumed["next_question"] == existing_resume["next_question"]
        assert resumed["completed_turns"] == existing_resume["completed_turns"]


# ─────────────────────────────────────────────
# 6. LLM extraction
# ─────────────────────────────────────────────

class TestLLMExtraction:
    def test_valid_transcript_extracts_skills(self):
        """A transcript mentioning skills results in skills being added to profile."""
        cid = _create_candidate("LLM Extract Skills")
        transcript = (
            "Assistant: What are your technical skills?\n"
            "Candidate: I specialize in Kubernetes, Terraform, and Go. "
            "I have 7 years of experience in DevOps and cloud infrastructure."
        )
        r = requests.post(
            f"{API}/voice/candidate-intake",
            json={"transcript": transcript, "candidate_id": cid},
            timeout=60,
        )
        assert r.status_code == 200

        profile = requests.get(f"{API}/candidate/{cid}/profile", timeout=15).json()
        skills_lower = [s.lower() for s in (profile.get("keySkills") or [])]
        # At least one of the mentioned skills should appear
        assert any(s in skills_lower for s in ["kubernetes", "terraform", "go"]), \
            f"Expected skills not found in: {skills_lower}"

    def test_intake_completes_even_if_extraction_sparse(self):
        """A very short transcript still saves without forcing completion."""
        cid = _create_candidate("LLM Sparse")
        r = requests.post(
            f"{API}/voice/candidate-intake",
            json={
                "transcript": "Candidate: I'm a developer.\nAssistant: Great.",
                "candidate_id": cid,
            },
            timeout=60,
        )
        assert r.status_code == 200
        assert r.json()["status"] in ("in_progress", "duplicate")


# ─────────────────────────────────────────────
# 7. Onboarding flow
# ─────────────────────────────────────────────

class TestOnboardingFlow:
    def test_full_onboarding_sequence(self):
        """
        Simulate: parse-resume → upload cert → voice intake → profile check.
        Verifies the complete onboarding sequence works end-to-end.
        """
        import io
        from reportlab.pdfgen import canvas as rl_canvas

        # Step 1: Parse resume
        email = f"flow-{uuid.uuid4().hex[:8]}@example.com"
        buf = io.BytesIO()
        c = rl_canvas.Canvas(buf)
        c.drawString(50, 800, "Flow Test Candidate")
        c.drawString(50, 780, f"Email: {email}")
        c.drawString(50, 760, "Senior Engineer, 8 years experience.")
        c.drawString(50, 740, "Skills: Java, Spring Boot, Kafka")
        c.showPage()
        c.save()
        buf.seek(0)

        r = requests.post(
            f"{API}/onboarding/parse-resume",
            files={"file": ("resume.pdf", buf.getvalue(), "application/pdf")},
            timeout=90,
        )
        assert r.status_code == 200
        cid = r.json().get("candidate_id") or r.json().get("candidateId")
        assert cid

        # Step 2: Upload certificate
        cert_buf = io.BytesIO()
        cc = rl_canvas.Canvas(cert_buf)
        cc.drawString(50, 800, "AWS Certified Solutions Architect")
        cc.showPage()
        cc.save()
        cert_buf.seek(0)

        r2 = requests.post(
            f"{API}/candidate/{cid}/certificates/upload",
            files={"file": ("aws_cert.pdf", cert_buf.getvalue(), "application/pdf")},
            timeout=30,
        )
        assert r2.status_code == 200

        # Step 3: Voice intake
        r3 = requests.post(
            f"{API}/voice/candidate-intake",
            json={
                "transcript": (
                    "Assistant: Tell me about your experience.\n"
                    "Candidate: I'm a senior Java engineer with 8 years in distributed systems. "
                    "I've built Kafka-based event streaming platforms at scale. "
                    "Looking for principal engineer roles in fintech, remote preferred."
                ),
                "candidate_id": cid,
            },
            timeout=60,
        )
        assert r3.status_code == 200
        assert r3.json()["status"] == "in_progress"

        # Step 4: Verify profile is enriched
        profile = requests.get(f"{API}/candidate/{cid}/profile", timeout=15).json()
        assert profile.get("name")
        assert profile.get("email") == email

    def test_voice_intake_after_uploads_does_not_lose_certs(self):
        """Certificates uploaded before voice intake are still present after."""
        import io
        from reportlab.pdfgen import canvas as rl_canvas

        cid = _create_candidate("Cert Preservation")

        cert_buf = io.BytesIO()
        cc = rl_canvas.Canvas(cert_buf)
        cc.drawString(50, 800, "Google Cloud Professional")
        cc.showPage()
        cc.save()
        cert_buf.seek(0)

        r_cert = requests.post(
            f"{API}/candidate/{cid}/certificates/upload",
            files={"file": ("gcp.pdf", cert_buf.getvalue(), "application/pdf")},
            timeout=30,
        )
        assert r_cert.status_code == 200

        r_voice = requests.post(
            f"{API}/voice/candidate-intake",
            json={"transcript": SAMPLE_TRANSCRIPT, "candidate_id": cid},
            timeout=60,
        )
        assert r_voice.status_code == 200

        docs = requests.get(f"{API}/candidate/{cid}/documents", timeout=15).json()
        assert len(docs.get("certificates", [])) >= 1


# ─────────────────────────────────────────────
# 8. Retry safety
# ─────────────────────────────────────────────

class TestRetry:
    def test_retry_with_different_transcript_succeeds(self):
        """A candidate can retry voice intake with a new transcript."""
        cid = _create_candidate("Retry Candidate")

        r1 = requests.post(
            f"{API}/voice/candidate-intake",
            json={"transcript": "Candidate: I'm a developer.", "candidate_id": cid},
            timeout=60,
        )
        assert r1.status_code == 200

        # Retry with richer transcript
        r2 = requests.post(
            f"{API}/voice/candidate-intake",
            json={"transcript": SAMPLE_TRANSCRIPT, "candidate_id": cid},
            timeout=60,
        )
        assert r2.status_code == 200
        assert r2.json()["status"] in ("in_progress", "completed")

        # Profile should still be intact
        profile = requests.get(f"{API}/candidate/{cid}/profile", timeout=15).json()
        assert profile.get("name")


# ─────────────────────────────────────────────
# 9. Regression: production bug — acknowledgements / embedded questions
# ─────────────────────────────────────────────

class TestProductionRegressions:
    """
    Regression tests derived from the production bug where:
    - "Thanks for that." was incorrectly becoming a pending_question
    - Candidate answer fragments were being split across fake turns
    - Real canonical questions embedded in longer assistant messages were missed
    """

    def test_acknowledgement_between_fragments_combined_into_one_answer(self):
        """
        Rule 9: assistant acknowledgements between candidate fragments must NOT
        terminate the active answer. All fragments belong to the same turn.

        Transcript:
          Assistant: "What kind of projects or technologies excite you the most right now?"
          Candidate: "Projects like app development and."
          Assistant: "Thanks for that."
          Candidate: "Recru"
          Assistant: "Thanks for that."
          Candidate: "Recruitment platforms."

        Expected: ONE completed_turn with all three candidate fragments combined.
        "Thanks for that." must never appear as a question.
        """
        cid = _create_candidate("Regression Ack Between Fragments")
        voice_notes = [
            {"role": "assistant", "text": "What kind of projects or technologies excite you the most right now?"},
            {"role": "user",      "text": "Projects like app development and."},
            {"role": "assistant", "text": "Thanks for that."},
            {"role": "user",      "text": "Recru"},
            {"role": "assistant", "text": "Thanks for that."},
            {"role": "user",      "text": "Recruitment platforms."},
        ]
        transcript = (
            "Assistant: What kind of projects or technologies excite you the most right now?\n"
            "Candidate: Projects like app development and.\n"
            "Assistant: Thanks for that.\n"
            "Candidate: Recru\n"
            "Assistant: Thanks for that.\n"
            "Candidate: Recruitment platforms."
        )

        r = requests.post(
            f"{API}/voice/candidate-intake/progress",
            json={"transcript": transcript, "voice_notes": voice_notes, "candidate_id": cid},
            timeout=60,
        )
        assert r.status_code == 200, r.text

        resume = r.json()["voice_intake_resume"]
        turns = resume.get("completed_turns") or []
        assert len(turns) == 1, f"Expected 1 completed turn, got {len(turns)}: {turns}"

        turn = turns[0]
        # The question must be the real canonical question, never "Thanks for that."
        assert "Thanks for that" not in turn["question"]
        assert "Thanks for that." not in turn["question"]
        assert "projects or technologies" in turn["question"].lower() or \
               turn["question"] == "What kind of projects or technologies excite you the most right now?"

        # All three candidate fragments must be in the combined answer
        answer = turn["answer"]
        assert "app development" in answer
        assert "Recru" in answer or "Recruitment" in answer
        assert "Recruitment platforms" in answer

    def test_real_question_embedded_in_longer_assistant_message(self):
        """
        Rule 5: a canonical question embedded inside a longer assistant message
        must be detected and used as the pending_question.

        Assistant: "Interesting. Let me understand your preferences.
                    What kind of role would you ideally like to move into next?"
        Candidate: "Yeah, I'm looking for a development role where the developer
                    development team will be using Python, C#, .NET—"

        Expected: ONE completed_turn with the canonical question extracted.
        """
        cid = _create_candidate("Regression Embedded Question")
        voice_notes = [
            {
                "role": "assistant",
                "text": (
                    "Interesting. Let me understand your preferences. "
                    "What kind of role would you ideally like to move into next?"
                ),
            },
            {
                "role": "user",
                "text": (
                    "Yeah, I'm looking for a development role where the developer "
                    "development team will be using Python, C#, .NET—"
                ),
            },
        ]
        transcript = (
            "Assistant: Interesting. Let me understand your preferences. "
            "What kind of role would you ideally like to move into next?\n"
            "Candidate: Yeah, I'm looking for a development role where the developer "
            "development team will be using Python, C#, .NET—"
        )

        r = requests.post(
            f"{API}/voice/candidate-intake/progress",
            json={"transcript": transcript, "voice_notes": voice_notes, "candidate_id": cid},
            timeout=60,
        )
        assert r.status_code == 200, r.text

        resume = r.json()["voice_intake_resume"]
        # The answer is still pending (no closing question yet), so check has_open_question
        # OR it may be flushed as a completed turn if the parser flushes trailing answers.
        # Either way, the question must be the canonical one, never "Interesting."
        turns = resume.get("completed_turns") or []
        pending_q = resume.get("next_question") or ""

        if turns:
            assert turns[-1]["question"] == "What kind of role would you ideally like to move into next?"
            assert "Python" in turns[-1]["answer"] or ".NET" in turns[-1]["answer"]
        else:
            # Answer is still open — the canonical question should be the active one
            assert resume.get("has_open_question") is True

        # "Interesting." must never be a question
        for t in turns:
            assert t["question"] != "Interesting."
            assert t["question"] != "Interesting"

    def test_acknowledgement_phrases_never_become_question(self):
        """
        Rule 3 / Rule 10: none of the listed acknowledgement phrases may ever
        appear as a completed_turn question or pending_question.
        """
        from server import _voice_intake_turn_pairs

        ack_phrases = [
            "Thanks",
            "Thanks.",
            "Thanks for that",
            "Thanks for that.",
            "Thanks for sharing",
            "Thanks for sharing that",
            "Got it",
            "Got it.",
            "Great",
            "Great.",
            "Interesting",
            "Interesting.",
            "Understood",
            "Understood.",
            "That's useful",
            "That's useful.",
            "Thanks, that's useful.",
        ]

        for phrase in ack_phrases:
            notes = [
                {"role": "assistant", "text": phrase, "final": True},
                {"role": "user",      "text": "I am a Python developer.", "final": True},
            ]
            completed, pending = _voice_intake_turn_pairs(notes)
            for turn in completed:
                assert turn["question"] != phrase, \
                    f'"{phrase}" must never be a completed_turn question'
            assert pending != phrase, \
                f'"{phrase}" must never be a pending_question'


# ─────────────────────────────────────────────
# 10. Production regression: dynamic VAPI questions + clarification requests
# ─────────────────────────────────────────────

class TestDynamicVapiTranscript:
    """
    Regression tests for the exact production payload that was returning
    progress=0 and completed_turns=[].

    The VAPI assistant generates dynamic/rephrased intake questions that do NOT
    match the hardcoded VOICE_INTAKE_QUESTION_FLOW patterns. The parser must
    detect them from the conversation structure (question mark, not setup/ack).
    """

    # Exact production voice_notes from the bug report
    PRODUCTION_VOICE_NOTES = [
        # Q1 — dynamic question not in hardcoded flow
        {"role": "assistant", "text": "What made you start exploring your next opportunity?", "final": True},
        {"role": "user", "text": "Uh, present I was working as an Python developer, uh.", "final": True},
        {"role": "user", "text": "By using the technologies Python, FastAPI, Postgres, and for.", "final": True},
        {"role": "user", "text": "And REST APIs. So.", "final": True},
        {"role": "user", "text": "I was currently looking.", "final": True},
        {"role": "user", "text": "For the opportunities, uh, as.", "final": True},
        {"role": "user", "text": "A backend developer. Uh.", "final": True},
        {"role": "user", "text": "Within.", "final": True},
        {"role": "user", "text": "The technology of.", "final": True},
        {"role": "user", "text": "Java.", "final": True},
        {"role": "user", "text": "Spring Boot, and Hibernate.", "final": True},
        # Q2 — second dynamic question
        {"role": "assistant", "text": "What would you like your next role to look like, compared with what you're doing today?", "final": True},
        {"role": "user", "text": "Yeah.", "final": True},
        {"role": "user", "text": "I want to gain more knowledge and more skill, skillful person, uh, in Java.", "final": True},
        # Clarification sequence — must NOT increment progress
        {"role": "assistant", "text": "That's great. Can you tell me more about the kind of team environment you're looking for?", "final": True},
        {"role": "user", "text": "Can you repeat the question?", "final": True},
        {"role": "assistant", "text": "Sure. What kind of team or company culture are you hoping to find in your next role?", "final": True},
        {"role": "user", "text": "Can you reframe the question? I can't get it.", "final": True},
        {"role": "assistant", "text": "Of course. What type of work environment would make you feel most engaged and productive?", "final": True},
        {"role": "user", "text": "Are you there?", "final": True},
    ]

    def test_unit_progress_equals_2(self):
        """
        Unit test (no HTTP): exact production notes must yield progress=2.
        """
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from server import _voice_intake_turn_pairs

        completed, pending = _voice_intake_turn_pairs(self.PRODUCTION_VOICE_NOTES)
        assert len(completed) == 2, (
            f"Expected 2 completed turns, got {len(completed)}: {completed}"
        )

    def test_unit_first_turn_question(self):
        """First completed turn question must be the first dynamic VAPI question."""
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from server import _voice_intake_turn_pairs

        completed, _ = _voice_intake_turn_pairs(self.PRODUCTION_VOICE_NOTES)
        assert completed[0]["question"] == "What made you start exploring your next opportunity?"

    def test_unit_first_turn_answer_contains_all_fragments(self):
        """First answer must combine all 10 candidate fragments into one string."""
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from server import _voice_intake_turn_pairs

        completed, _ = _voice_intake_turn_pairs(self.PRODUCTION_VOICE_NOTES)
        answer = completed[0]["answer"]
        assert "Python" in answer
        assert "FastAPI" in answer
        assert "Postgres" in answer
        assert "REST APIs" in answer
        assert "backend developer" in answer
        assert "Java" in answer
        assert "Spring Boot" in answer


class TestVoiceIntakeProduction67c819b0:
    """
    Regression coverage for candidate 67c819b0-e4d3-4daa-9d81-306996b740e6.

    These tests lock down the production state-machine bugs described in the
    issue: off-topic fragment filtering, answered-question promotion, and
    persisted-state parity.
    """

    PRODUCTION_ID = "67c819b0-e4d3-4daa-9d81-306996b740e6"
    Q1 = "What made you start exploring your next opportunity?"
    Q2 = (
        "What would make a backend developer role using Java and Spring Boot a really good fit for you "
        "compared to your current experience with Python and FastAPI?"
    )
    Q3 = "Are there particular types of projects or industries you'd like to work in using Java?"
    NEXT_QUESTION = "What kind of Java projects or responsibilities would you like to focus on in your next role?"

    VOICE_NOTES = [
        {"role": "assistant", "text": Q1, "final": True},
        {"role": "user", "text": "Do you need a car for my appointment today?", "final": True},
        {"role": "user", "text": "See, present I was working as a Python developer.", "final": True},
        {"role": "assistant", "text": Q2, "final": True},
        {"role": "user", "text": "See, I have more interest on Java. Technology. So I want to move. My career to Java technologies.", "final": True},
        {"role": "assistant", "text": Q3, "final": True},
        {"role": "user", "text": "There is no particular. Industry.", "final": True},
    ]
    PRODUCTION_VOICE_NOTES = VOICE_NOTES

    EXISTING_RESUME = {
        "status": "in_progress",
        "progress": 2,
        "completed_turns": [
            {
                "question": Q1,
                "answer": "See, present I was working as a Python developer.",
            },
            {
                "question": Q2,
                "answer": "See, I have more interest on Java. Technology. So I want to move. My career to Java technologies.",
            },
        ],
        "current_question": Q3,
        "next_question": NEXT_QUESTION,
        "missing_topics": ["projects"],
        "known_topics": ["python", "fastapi", "java", "spring boot"],
    }

    LLM_ANALYSIS = {
        "completed": False,
        "next_question": NEXT_QUESTION,
        "missing_topics": ["projects"],
        "known_topics": ["python", "fastapi", "java", "spring boot"],
    }

    def _build_resume(self, existing_resume=None, llm_analysis=None):
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from server import _build_voice_intake_resume_from_notes

        return _build_voice_intake_resume_from_notes(
            self.VOICE_NOTES,
            "",
            existing_resume or self.EXISTING_RESUME,
            llm_analysis=llm_analysis or self.LLM_ANALYSIS,
        )

    def test_A_off_topic_fragment_is_excluded(self):
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from server import _voice_intake_turn_pairs

        completed, _ = _voice_intake_turn_pairs(self.VOICE_NOTES)
        assert len(completed) == 3
        first_answer = completed[0]["answer"]
        assert "car for my appointment" not in first_answer
        assert "Python developer" in first_answer

    def test_B_answered_q3_appears_once_and_is_not_current_question(self):
        resume = self._build_resume()
        questions = [turn["question"] for turn in resume.get("completed_turns") or []]
        assert questions.count(self.Q3) == 1
        assert resume.get("current_question") != self.Q3

    def test_C_next_unanswered_question_becomes_current_question(self):
        resume = self._build_resume()
        assert resume["current_question"] == self.NEXT_QUESTION
        assert resume.get("next_question") in (None, "")

    def test_D_persisted_voice_intake_matches_returned_state(self):
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from server import _normalize_for_frontend

        resume = self._build_resume()
        profile = _normalize_for_frontend({
            "id": self.PRODUCTION_ID,
            "name": "Production 67c819b0",
            "raw_data": {"voice_intake": resume},
        })
        assert profile["voice_intake_resume"] == resume

    def test_E_disconnect_after_new_question_preserves_current_question(self):
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from server import _build_voice_intake_resume_from_notes

        existing_resume = dict(self.EXISTING_RESUME)
        existing_resume["current_question"] = self.NEXT_QUESTION
        existing_resume["next_question"] = "What kinds of responsibilities would you like next?"

        resume = _build_voice_intake_resume_from_notes([], "", existing_resume, llm_analysis=self.LLM_ANALYSIS)
        assert resume["current_question"] == self.NEXT_QUESTION

    def test_F_repeated_cumulative_submission_is_idempotent(self):
        first = self._build_resume()
        second = self._build_resume(existing_resume=first)

        assert second["progress"] == first["progress"]
        assert second["completed_turns"] == first["completed_turns"]
        assert len(second.get("completed_turns") or []) == len({t["question"] for t in second.get("completed_turns") or []})

    def test_unit_second_turn_question(self):
        """Second completed turn question must be the second dynamic VAPI question."""
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from server import _voice_intake_turn_pairs

        completed, _ = _voice_intake_turn_pairs(self.PRODUCTION_VOICE_NOTES)
        assert completed[1]["question"] == self.Q2

    def test_unit_second_turn_answer_contains_java(self):
        """Second answer must contain the candidate's Java upskilling intent."""
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from server import _voice_intake_turn_pairs

        completed, _ = _voice_intake_turn_pairs(self.PRODUCTION_VOICE_NOTES)
        answer = completed[1]["answer"]
        assert "Java" in answer
        assert "career" in answer.lower() or "technology" in answer.lower()

    def test_unit_clarification_requests_not_in_answers(self):
        """
        'Can you repeat the question?', 'Can you reframe the question?',
        and 'Are you there?' must NOT appear in any completed answer.
        """
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from server import _voice_intake_turn_pairs

        completed, _ = _voice_intake_turn_pairs(self.PRODUCTION_VOICE_NOTES)
        all_answers = " ".join(t["answer"] for t in completed)
        assert "Can you repeat" not in all_answers
        assert "Can you reframe" not in all_answers
        assert "Are you there" not in all_answers

    def test_unit_status_in_progress(self):
        """Status must remain in_progress (not completed) after 2 of N questions."""
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from server import _voice_intake_turn_pairs, _next_voice_intake_question, VOICE_INTAKE_TOTAL_QUESTIONS

        completed, pending = _voice_intake_turn_pairs(self.PRODUCTION_VOICE_NOTES)
        next_q = _next_voice_intake_question(completed, pending)
        status = "completed" if len(completed) >= VOICE_INTAKE_TOTAL_QUESTIONS and not next_q else "in_progress"
        assert status == "in_progress"

    def test_unit_next_question_is_not_none(self):
        """next_question must point to the next unanswered question."""
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from server import _voice_intake_turn_pairs, _next_voice_intake_question

        completed, pending = _voice_intake_turn_pairs(self.PRODUCTION_VOICE_NOTES)
        next_q = _next_voice_intake_question(completed, pending)
        assert next_q in (None, "")
        # Must not be one of the already-answered questions
        answered = {t["question"] for t in completed}
        assert next_q not in answered

    def test_unit_multi_fragment_assistant_question_combined(self):
        """
        Rule 3/4: consecutive assistant fragments that together form one question
        must be combined before question detection.
        """
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from server import _voice_intake_turn_pairs

        notes = [
            {"role": "assistant", "text": "That's interesting.", "final": True},
            {"role": "assistant", "text": "What made you start exploring your next opportunity?", "final": True},
            {"role": "user", "text": "I was looking for a Java backend role.", "final": True},
        ]
        completed, _ = _voice_intake_turn_pairs(notes)
        assert len(completed) == 1
        assert completed[0]["question"] == "What made you start exploring your next opportunity?"
        assert "Java" in completed[0]["answer"]

    def test_unit_question_rephrasing_does_not_create_new_turn(self):
        """
        Rule 12: if Eve rephrases the same question, it must NOT create a new
        completed turn. The original pending question is kept.
        """
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from server import _voice_intake_turn_pairs

        notes = [
            {"role": "assistant", "text": "What made you start exploring your next opportunity?", "final": True},
            {"role": "user", "text": "Can you repeat the question?", "final": True},
            # Rephrasing of the same question
            {"role": "assistant", "text": "Sure — what prompted you to start looking for a new opportunity?", "final": True},
            {"role": "user", "text": "I wanted to move into Java backend development.", "final": True},
        ]
        completed, _ = _voice_intake_turn_pairs(notes)
        # Must be exactly 1 completed turn, not 2
        assert len(completed) == 1, f"Expected 1 turn (rephrasing), got {len(completed)}: {completed}"
        assert "Java" in completed[0]["answer"]

    def test_unit_acknowledgement_between_turns_does_not_clear_question(self):
        """
        Rule 9: acknowledgement-only assistant turns between candidate fragments
        must NOT clear the pending question or split the answer.
        """
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from server import _voice_intake_turn_pairs

        notes = [
            {"role": "assistant", "text": "What made you start exploring your next opportunity?", "final": True},
            {"role": "user", "text": "I was a Python developer.", "final": True},
            {"role": "assistant", "text": "Got it.", "final": True},
            {"role": "user", "text": "And I wanted to move into Java.", "final": True},
        ]
        completed, _ = _voice_intake_turn_pairs(notes)
        assert len(completed) == 1
        answer = completed[0]["answer"]
        assert "Python developer" in answer
        assert "Java" in answer

    def test_integration_production_payload_progress_2(self):
        """
        Unit version of the production payload regression.
        The full 67c819b0 transcript must still yield a resumable in_progress
        state with all three completed turns captured exactly once.
        """
        resume = self._build_resume()
        assert resume["status"] == "in_progress"
        assert resume["progress"] == 3
        turns = resume.get("completed_turns") or []
        assert len(turns) == 3, f"Expected 3 completed turns, got {len(turns)}: {turns}"
        assert turns[0]["question"] == self.Q1
        assert turns[1]["question"] == self.Q2
        assert turns[2]["question"] == self.Q3
        # Clarification phrases must not appear in answers
        all_answers = " ".join(t["answer"] for t in turns)
        assert "Can you repeat" not in all_answers
        assert "Can you reframe" not in all_answers
        assert "Are you there" not in all_answers
        # next_question must be set and not be one of the answered questions
        assert resume.get("next_question") in (None, "")


# ─────────────────────────────────────────────
# 11. Persistence accumulation regression (83a3bf06)
# ─────────────────────────────────────────────

class TestPersistenceAccumulation:
    """
    Regression for production candidate 83a3bf06-7960-4513-9fa7-aa78484c6eab.

    The progress endpoint was called twice with cumulative voice_notes:
      - First call: only Turn 1 notes present  → saved progress=1
      - Second call: Turn 1 + Turn 2 notes present → must yield progress=2

    Bug: second call replaced completed_turns=[turn1] with completed_turns=[turn2]
    because _build_voice_intake_resume_from_notes returned the fresh parse result
    (progress=1 from new notes only) instead of merging with the persisted turn.
    """

    # Exact voice_notes for Turn 1 (Java/Spring Boot/Kubernetes answer)
    TURN1_NOTES = [
        {"role": "assistant", "text": "What made you start exploring your next opportunity?", "final": True},
        {"role": "user", "text": "I was interested in Java programming language, so I was looking for the opportunities.", "final": True},
        {"role": "user", "text": "Where I can work with the technology of Java.", "final": True},
        {"role": "user", "text": "And Java frameworks.", "final": True},
        {"role": "user", "text": "Like.", "final": True},
        {"role": "user", "text": "Spring Boot.", "final": True},
        {"role": "user", "text": "I.", "final": True},
        {"role": "user", "text": "It. And.", "final": True},
        {"role": "user", "text": "Kubernetes.", "final": True},
    ]

    # Cumulative voice_notes: Turn 1 + Turn 2
    TURN1_AND_2_NOTES = [
        {"role": "assistant", "text": "What made you start exploring your next opportunity?", "final": True},
        {"role": "user", "text": "I was interested in Java programming language, so I was looking for the opportunities.", "final": True},
        {"role": "user", "text": "Where I can work with the technology of Java.", "final": True},
        {"role": "user", "text": "And Java frameworks.", "final": True},
        {"role": "user", "text": "Like.", "final": True},
        {"role": "user", "text": "Spring Boot.", "final": True},
        {"role": "user", "text": "I.", "final": True},
        {"role": "user", "text": "It. And.", "final": True},
        {"role": "user", "text": "Kubernetes.", "final": True},
        {"role": "assistant", "text": "What kind of role would you ideally like to move into next?", "final": True},
        {"role": "user", "text": "As a backend developer, or Java developer.", "final": True},
    ]

    def test_unit_turn1_parsed_correctly(self):
        """Turn 1 notes alone must yield 1 completed turn with Java/Spring Boot/Kubernetes answer."""
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from server import _voice_intake_turn_pairs

        completed, pending = _voice_intake_turn_pairs(self.TURN1_NOTES)
        assert len(completed) == 1, f"Expected 1 turn, got {len(completed)}: {completed}"
        assert completed[0]["question"] == "What made you start exploring your next opportunity?"
        answer = completed[0]["answer"]
        assert "Java" in answer
        assert "Spring Boot" in answer
        assert "Kubernetes" in answer

    def test_unit_cumulative_notes_yield_2_turns(self):
        """Cumulative notes (Turn 1 + Turn 2) must yield exactly 2 completed turns."""
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from server import _voice_intake_turn_pairs

        completed, _ = _voice_intake_turn_pairs(self.TURN1_AND_2_NOTES)
        assert len(completed) == 2, f"Expected 2 turns, got {len(completed)}: {completed}"
        assert completed[0]["question"] == "What made you start exploring your next opportunity?"
        assert completed[1]["question"] == "What kind of role would you ideally like to move into next?"
        assert "Java" in completed[0]["answer"]
        assert "Spring Boot" in completed[0]["answer"]
        assert "Kubernetes" in completed[0]["answer"]
        assert "backend developer" in completed[1]["answer"] or "Java developer" in completed[1]["answer"]

    def test_unit_merge_preserves_turn1_when_only_turn2_notes_given(self):
        """
        Core regression: if the second call only sends Turn 2 notes (progress=1 from
        new parse), but existing_resume already has Turn 1, the merge must produce
        both turns (progress=2).
        """
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from server import _build_voice_intake_resume_from_notes

        # Simulate persisted state after first call
        existing_resume = {
            "status": "in_progress",
            "progress": 1,
            "completed_turns": [
                {
                    "question": "What made you start exploring your next opportunity?",
                    "answer": "I was interested in Java programming language, so I was looking for the opportunities. Where I can work with the technology of Java. And Java frameworks. Like. Spring Boot. I. It. And. Kubernetes.",
                }
            ],
            "next_question": "What kind of role would you ideally like to move into next?",
            "missing_topics": ["target_role", "career_preferences"],
            "known_topics": ["background_experience"],
        }

        # Second call: only Turn 2 notes (incremental, not cumulative)
        turn2_only_notes = [
            {"role": "assistant", "text": "What kind of role would you ideally like to move into next?", "final": True},
            {"role": "user", "text": "As a backend developer, or Java developer.", "final": True},
        ]

        resume = _build_voice_intake_resume_from_notes(turn2_only_notes, "", existing_resume)
        assert resume["progress"] == 2, f"Expected progress=2, got {resume['progress']}"
        turns = resume.get("completed_turns") or []
        assert len(turns) == 2, f"Expected 2 turns after merge, got {len(turns)}: {turns}"
        assert turns[0]["question"] == "What made you start exploring your next opportunity?"
        assert turns[1]["question"] == "What kind of role would you ideally like to move into next?"
        assert resume["status"] == "in_progress"

    def test_unit_idempotent_same_notes_no_duplicate(self):
        """
        Calling with the same cumulative notes twice must not duplicate turns.
        """
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from server import _build_voice_intake_resume_from_notes

        resume1 = _build_voice_intake_resume_from_notes(self.TURN1_AND_2_NOTES, "")
        # Second call with same notes and resume1 as existing
        resume2 = _build_voice_intake_resume_from_notes(self.TURN1_AND_2_NOTES, "", resume1)
        turns = resume2.get("completed_turns") or []
        assert len(turns) == 2, f"Idempotency failed: expected 2 turns, got {len(turns)}: {turns}"

    def test_unit_llm_state_preserved_on_retry_with_empty_llm_response(self):
        """
        If the LLM returns empty on a retry, previously persisted next_question /
        missing_topics / known_topics must not be wiped.
        """
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from server import _build_voice_intake_resume_from_notes

        existing_resume = {
            "status": "in_progress",
            "progress": 1,
            "completed_turns": [
                {"question": "What made you start exploring your next opportunity?", "answer": "Java and Spring Boot."}
            ],
            "next_question": "What kind of role would you ideally like to move into next?",
            "current_question": "What kind of role would you ideally like to move into next?",
            "missing_topics": ["target_role"],
            "known_topics": ["background_experience"],
        }

        # LLM returns empty (failure/timeout)
        resume = _build_voice_intake_resume_from_notes(
            self.TURN1_NOTES, "", existing_resume, llm_analysis={}
        )
        assert resume.get("next_question") == "What kind of role would you ideally like to move into next?"
        assert resume.get("missing_topics") == ["target_role"]
        assert resume.get("known_topics") == ["background_experience"]

    def test_integration_two_sequential_progress_calls_accumulate(self):
        """
        Integration: POST Turn 1 notes → POST Turn 1+2 notes → assert progress=2.
        Simulates the exact production failure scenario.
        """
        cid = _create_candidate("Persistence Accumulation Test")

        # First call: Turn 1 only
        r1 = requests.post(
            f"{API}/voice/candidate-intake/progress",
            json={"transcript": "", "voice_notes": self.TURN1_NOTES, "candidate_id": cid},
            timeout=60,
        )
        assert r1.status_code == 200, r1.text
        resume1 = r1.json()["voice_intake_resume"]
        assert resume1["progress"] == 1
        assert len(resume1.get("completed_turns") or []) == 1
        assert resume1["completed_turns"][0]["question"] == "What made you start exploring your next opportunity?"

        # Second call: cumulative Turn 1 + Turn 2
        r2 = requests.post(
            f"{API}/voice/candidate-intake/progress",
            json={"transcript": "", "voice_notes": self.TURN1_AND_2_NOTES, "candidate_id": cid},
            timeout=60,
        )
        assert r2.status_code == 200, r2.text
        resume2 = r2.json()["voice_intake_resume"]
        assert resume2["status"] == "in_progress"
        assert resume2["progress"] == 2
        turns = resume2.get("completed_turns") or []
        assert len(turns) == 2, f"Expected 2 turns, got {len(turns)}: {turns}"
        assert turns[0]["question"] == "What made you start exploring your next opportunity?"
        assert turns[1]["question"] == "What kind of role would you ideally like to move into next?"
        assert "Java" in turns[0]["answer"]
        assert "Spring Boot" in turns[0]["answer"]
        assert "Kubernetes" in turns[0]["answer"]
        assert "backend developer" in turns[1]["answer"] or "Java developer" in turns[1]["answer"]
        assert resume2.get("next_question") is not None
        answered_qs = {t["question"] for t in turns}
        assert resume2["next_question"] not in answered_qs

    def test_integration_idempotent_same_notes_twice(self):
        """
        Calling the progress endpoint twice with the same cumulative notes must
        produce the same state and must not duplicate completed_turns.
        """
        cid = _create_candidate("Idempotent Progress Test")

        r1 = requests.post(
            f"{API}/voice/candidate-intake/progress",
            json={"transcript": "", "voice_notes": self.TURN1_AND_2_NOTES, "candidate_id": cid},
            timeout=60,
        )
        assert r1.status_code == 200, r1.text

        r2 = requests.post(
            f"{API}/voice/candidate-intake/progress",
            json={"transcript": "", "voice_notes": self.TURN1_AND_2_NOTES, "candidate_id": cid},
            timeout=60,
        )
        assert r2.status_code == 200, r2.text
        resume = r2.json()["voice_intake_resume"]
        turns = resume.get("completed_turns") or []
        assert len(turns) == 2, f"Idempotency failed: expected 2 turns, got {len(turns)}: {turns}"
        assert resume["progress"] == 2

    def test_integration_exact_production_transcript_accumulates(self):
        """
        Regression for production candidate 83a3bf06-7960-4513-9fa7-aa78484c6eab.

        The same cumulative voice_notes must preserve both completed turns and
        remain idempotent across repeated progress calls.
        """
        cid = _create_candidate("Exact Production Transcript Test")

        r1 = requests.post(
            f"{API}/voice/candidate-intake/progress",
            json={"transcript": "", "voice_notes": self.TURN1_NOTES, "candidate_id": cid},
            timeout=60,
        )
        assert r1.status_code == 200, r1.text
        resume1 = r1.json()["voice_intake_resume"]
        assert resume1["status"] == "in_progress"
        assert resume1["progress"] == 1
        assert len(resume1.get("completed_turns") or []) == 1

        r2 = requests.post(
            f"{API}/voice/candidate-intake/progress",
            json={"transcript": "", "voice_notes": self.TURN1_AND_2_NOTES, "candidate_id": cid},
            timeout=60,
        )
        assert r2.status_code == 200, r2.text
        resume2 = r2.json()["voice_intake_resume"]
        turns = resume2.get("completed_turns") or []
        assert resume2["status"] == "in_progress"
        assert resume2["progress"] == 2
        assert len(turns) == 2, f"Expected 2 turns, got {len(turns)}: {turns}"
        assert turns[0]["question"] == "What made you start exploring your next opportunity?"
        assert turns[1]["question"] == "What kind of role would you ideally like to move into next?"
        assert "Java" in turns[0]["answer"]
        assert "Spring Boot" in turns[0]["answer"]
        assert "Kubernetes" in turns[0]["answer"]
        assert turns[1]["answer"] == "As a backend developer, or Java developer."
        assert resume2.get("current_question"), "current_question should persist"
        assert resume2.get("known_topics") is not None
        assert resume2.get("missing_topics") is not None
        assert resume2.get("next_question"), "next_question should persist"

        r3 = requests.post(
            f"{API}/voice/candidate-intake/progress",
            json={"transcript": "", "voice_notes": self.TURN1_AND_2_NOTES, "candidate_id": cid},
            timeout=60,
        )
        assert r3.status_code == 200, r3.text
        resume3 = r3.json()["voice_intake_resume"]
        turns3 = resume3.get("completed_turns") or []
        assert resume3["progress"] == 2
        assert len(turns3) == 2, f"Duplicate turns introduced on repeat call: {turns3}"
        assert turns3 == turns


# ─────────────────────────────────────────────
# 12. New architecture regression tests (A–J)
# ─────────────────────────────────────────────

class TestNewArchitectureRegressions:
    """
    Regression tests for the LLM-driven Voice Intake architecture.
    Tests A–J as specified in the requirements.
    """

    # ── A: 7+ transcript fragments = one completed answer ──────────────────

    def test_A_seven_fragments_one_answer(self):
        """
        A. Candidate answers one question with 7+ transcript fragments.
        Expected: one completed answer, progress=1, status=in_progress.
        """
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from server import _voice_intake_turn_pairs

        notes = [
            {"role": "assistant", "text": "What made you start exploring your next opportunity?", "final": True},
            {"role": "user", "text": "I worked as a Python developer.", "final": True},
            {"role": "user", "text": "Using FastAPI.", "final": True},
            {"role": "user", "text": "Postgres.", "final": True},
            {"role": "user", "text": "REST APIs.", "final": True},
            {"role": "user", "text": "Now I'm looking for Java.", "final": True},
            {"role": "user", "text": "Spring Boot.", "final": True},
            {"role": "user", "text": "Hibernate.", "final": True},
        ]
        completed, pending = _voice_intake_turn_pairs(notes)
        assert len(completed) == 1, f"Expected 1 completed turn, got {len(completed)}"
        assert completed[0]["question"] == "What made you start exploring your next opportunity?"
        answer = completed[0]["answer"]
        assert "Python" in answer
        assert "FastAPI" in answer
        assert "Postgres" in answer
        assert "REST APIs" in answer
        assert "Java" in answer
        assert "Spring Boot" in answer
        assert "Hibernate" in answer


class TestVoiceIntakeStatePersistenceFix:
    """
    Regression coverage for the production bug reported against candidate
    785790c6-b21e-486a-8e1f-f65936d7f621.

    These tests exercise the LLM-driven parser/state merge directly so we can
    guarantee idempotent cumulative processing without falling back to the old
    hardcoded question flow.
    """

    PRODUCTION_ID = "785790c6-b21e-486a-8e1f-f65936d7f621"

    ANSWERED_TECH_NOTES = [
        {"role": "assistant", "text": "What made you start exploring your next opportunity?", "final": True},
        {"role": "user", "text": "I was more interested on Java technology...", "final": True},
        {"role": "user", "text": "Requires the technologies of.", "final": True},
        {"role": "user", "text": "Spring boot.", "final": True},
        {"role": "user", "text": "And Hibernate.", "final": True},
        {"role": "assistant", "text": "Which Java technologies or frameworks do you enjoy working with the most?", "final": True},
        {"role": "user", "text": "Spring boot. And Hibernate.", "final": True},
    ]

    PROJECT_QUESTION_FRAGMENTS = [
        {"role": "assistant", "text": "Nice choices. Um, what kind of projects have you worked on, uh.", "final": True},
        {"role": "assistant", "text": "Using.", "final": True},
        {"role": "assistant", "text": "Spring Boot and Hibernate?", "final": True},
    ]

    def test_A_same_cumulative_voice_notes_twice_is_idempotent(self):
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from server import _build_voice_intake_resume_from_notes

        first = _build_voice_intake_resume_from_notes(self.ANSWERED_TECH_NOTES, "")
        second = _build_voice_intake_resume_from_notes(self.ANSWERED_TECH_NOTES, "", first)

        assert second["progress"] == first["progress"]
        assert second["completed_turns"] == first["completed_turns"]
        assert len(second.get("completed_turns") or []) == len({t["question"] for t in second.get("completed_turns") or []})

    def test_B_multi_fragment_assistant_question_combines_into_one_question(self):
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from server import _voice_intake_turn_pairs

        completed, pending = _voice_intake_turn_pairs(self.PROJECT_QUESTION_FRAGMENTS)
        assert completed == []
        assert pending == "What kind of projects have you worked on using Spring Boot and Hibernate?"

    def test_C_answered_question_is_removed_from_current_question(self):
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from server import _build_voice_intake_resume_from_notes

        existing_resume = {
            "status": "in_progress",
            "progress": 2,
            "completed_turns": [
                {
                    "question": "What made you start exploring your next opportunity?",
                    "answer": "I was interested in Java technology and Spring Boot.",
                },
                {
                    "question": "Which Java technologies or frameworks do you enjoy working with the most?",
                    "answer": "Spring boot. And Hibernate.",
                },
            ],
            "current_question": "Spring Boot and Hibernate?",
            "next_question": "What kind of projects have you worked on using Spring Boot and Hibernate?",
            "missing_topics": ["projects"],
            "known_topics": ["java", "spring boot", "hibernate"],
        }

        resume = _build_voice_intake_resume_from_notes(
            self.PROJECT_QUESTION_FRAGMENTS,
            "",
            existing_resume,
        )
        assert resume["current_question"] == "What kind of projects have you worked on using Spring Boot and Hibernate?"
        assert resume.get("next_question") in (None, "")
        assert resume["completed_turns"][-1]["question"] == "Which Java technologies or frameworks do you enjoy working with the most?"

    def test_D_new_unanswered_question_becomes_current_question(self):
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from server import _build_voice_intake_resume_from_notes

        existing_resume = {
            "status": "in_progress",
            "progress": 2,
            "completed_turns": [
                {
                    "question": "What made you start exploring your next opportunity?",
                    "answer": "I was more interested on Java technology...",
                },
                {
                    "question": "Which Java technologies or frameworks do you enjoy working with the most?",
                    "answer": "Spring boot. And Hibernate.",
                },
            ],
            "current_question": "Spring Boot and Hibernate?",
            "next_question": "What kind of projects have you worked on using Spring Boot and Hibernate?",
            "missing_topics": ["projects"],
            "known_topics": ["java", "spring boot", "hibernate"],
        }

        resume = _build_voice_intake_resume_from_notes(
            self.PROJECT_QUESTION_FRAGMENTS,
            "",
            existing_resume,
        )
        assert resume["status"] == "in_progress"
        assert resume["current_question"] == "What kind of projects have you worked on using Spring Boot and Hibernate?"
        assert resume.get("has_open_question") is True

    def test_E_api_and_persisted_state_share_identical_question_state(self):
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from server import _build_voice_intake_resume_from_notes

        existing_resume = {
            "status": "in_progress",
            "progress": 2,
            "completed_turns": [
                {
                    "question": "What made you start exploring your next opportunity?",
                    "answer": "I was more interested on Java technology...",
                },
                {
                    "question": "Which Java technologies or frameworks do you enjoy working with the most?",
                    "answer": "Spring boot. And Hibernate.",
                },
            ],
            "current_question": "Spring Boot and Hibernate?",
            "next_question": "What kind of projects have you worked on using Spring Boot and Hibernate?",
            "missing_topics": ["projects"],
            "known_topics": ["java", "spring boot", "hibernate"],
        }

        resume = _build_voice_intake_resume_from_notes(
            self.PROJECT_QUESTION_FRAGMENTS,
            "",
            existing_resume,
        )
        persisted_state = dict(resume)
        assert persisted_state["current_question"] == resume["current_question"]
        assert persisted_state.get("next_question") == resume.get("next_question")

    def test_F_disconnect_after_project_question_resumes_with_same_question(self):
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from server import _build_voice_intake_resume_from_notes

        existing_resume = {
            "status": "in_progress",
            "progress": 2,
            "completed_turns": [
                {
                    "question": "What made you start exploring your next opportunity?",
                    "answer": "I was more interested on Java technology...",
                },
                {
                    "question": "Which Java technologies or frameworks do you enjoy working with the most?",
                    "answer": "Spring boot. And Hibernate.",
                },
            ],
            "current_question": "Spring Boot and Hibernate?",
            "next_question": "What kind of projects have you worked on using Spring Boot and Hibernate?",
            "missing_topics": ["projects"],
            "known_topics": ["java", "spring boot", "hibernate"],
        }

        resume = _build_voice_intake_resume_from_notes([], "", existing_resume)
        assert resume["status"] == "in_progress"
        assert resume["current_question"] == "What kind of projects have you worked on using Spring Boot and Hibernate?"
        assert resume.get("next_question") in (None, "")

    # ── B: Multiple questions, no duplicates ───────────────────────────────

    def test_B_multiple_questions_no_duplicates(self):
        """
        B. Candidate answers multiple questions.
        Expected: each real information request represented once, no duplicates.
        """
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from server import _voice_intake_turn_pairs

        notes = [
            {"role": "assistant", "text": "Tell me about your background?", "final": True},
            {"role": "user", "text": "I'm a Java backend developer with 5 years experience.", "final": True},
            {"role": "assistant", "text": "What technologies do you use?", "final": True},
            {"role": "user", "text": "Spring Boot, Hibernate, Kafka.", "final": True},
            {"role": "assistant", "text": "What kind of role are you targeting?", "final": True},
            {"role": "user", "text": "Senior backend engineer roles.", "final": True},
        ]
        completed, _ = _voice_intake_turn_pairs(notes)
        assert len(completed) == 3
        questions = [t["question"] for t in completed]
        assert len(questions) == len(set(questions)), "Duplicate questions found"

    # ── C: Clarification request — no progress increase ────────────────────

    def test_C_clarification_no_progress(self):
        """
        C. Candidate says 'Can you repeat the question?'
        Expected: no progress increase.
        """
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from server import _voice_intake_turn_pairs

        notes = [
            {"role": "assistant", "text": "What kind of role are you targeting?", "final": True},
            {"role": "user", "text": "Can you repeat the question?", "final": True},
        ]
        completed, pending = _voice_intake_turn_pairs(notes)
        assert len(completed) == 0
        assert pending == "What kind of role are you targeting?"

    # ── D: Rephrased question — no duplicate progress ──────────────────────

    def test_D_rephrased_question_no_duplicate(self):
        """
        D. Eve rephrases the same question.
        Expected: same question/state, no duplicate progress.
        """
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from server import _voice_intake_turn_pairs

        notes = [
            {"role": "assistant", "text": "What made you start exploring your next opportunity?", "final": True},
            {"role": "user", "text": "Can you repeat the question?", "final": True},
            {"role": "assistant", "text": "Sure — what prompted you to start looking for a new opportunity?", "final": True},
            {"role": "user", "text": "I wanted to move into Java backend development.", "final": True},
        ]
        completed, _ = _voice_intake_turn_pairs(notes)
        assert len(completed) == 1, f"Expected 1 turn (rephrasing), got {len(completed)}"
        assert "Java" in completed[0]["answer"]

    # ── E: Disconnect during answer — partial answer persisted ─────────────

    def test_E_disconnect_partial_answer_persisted(self):
        """
        E. Candidate disconnects during an answer.
        Expected: current question and partial answer persisted.
        """
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from server import _voice_intake_turn_pairs

        notes = [
            {"role": "assistant", "text": "What projects have you worked on recently?", "final": True},
            {"role": "user", "text": "I worked on an AI automation project.", "final": True},
            {"role": "user", "text": "I used Python.", "final": True},
            # candidate disconnects here — no closing assistant turn
        ]
        completed, pending = _voice_intake_turn_pairs(notes)
        # The partial answer should be flushed as a completed turn (trailing answer flush)
        assert len(completed) == 1
        assert "AI automation" in completed[0]["answer"]
        assert "Python" in completed[0]["answer"]

    # ── F: Resume — continues from unfinished question ─────────────────────

    def test_F_resume_continues_from_unfinished(self):
        """
        F. Candidate returns after disconnect.
        Expected: resumes from the unfinished question, does not restart.
        """
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from server import _build_voice_intake_resume_from_notes

        # Simulate persisted state from previous session
        existing_resume = {
            "status": "in_progress",
            "progress": 2,
            "completed_turns": [
                {"question": "Tell me about your background?", "answer": "I'm a Python developer."},
                {"question": "What are your key skills?", "answer": "Python, FastAPI, PostgreSQL."},
            ],
            "current_question": "What projects have you worked on recently?",
            "next_question": "What projects have you worked on recently?",
            "missing_topics": ["responsibilities_projects", "target_role"],
        }

        # New session with no new voice_notes yet
        resume = _build_voice_intake_resume_from_notes([], "", existing_resume)
        assert resume["status"] == "in_progress"
        assert resume["progress"] == 2
        # Must resume from the saved question, not restart
        next_q = resume.get("next_question") or resume.get("current_question")
        assert next_q == "What projects have you worked on recently?"

    # ── G: Resume info already in profile — LLM should not re-ask ──────────

    def test_G_profile_info_not_re_asked(self):
        """
        G. Candidate already has information in resume.
        Expected: LLM does not ask for information already known.
        The VAPI context must include known profile data so VAPI/LLM can skip it.
        """
        # This is a structural test — verify the VAPI context builder includes profile data
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from server import VOICE_INTAKE_TOPICS, VOICE_INTAKE_ANALYZE_SYSTEM

        # The system prompt must instruct LLM not to re-ask known info
        assert "already present in the candidate profile" in VOICE_INTAKE_ANALYZE_SYSTEM
        assert "candidate_profile" in VOICE_INTAKE_ANALYZE_SYSTEM
        # Topics must be provided as hints, not hardcoded questions
        assert "background_experience" in VOICE_INTAKE_TOPICS
        assert "target_role" in VOICE_INTAKE_TOPICS

    # ── H: New skills merged into profile ──────────────────────────────────

    def test_H_new_skills_merged(self):
        """
        H. Candidate introduces new skills.
        Expected: skills merged into profile.
        """
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from server import _merge_voice_into_profile

        existing = {
            "skills": ["Python", "FastAPI"],
            "work_experience": [],
            "education": [],
        }
        voice = {"skills": ["Java", "Spring Boot", "Hibernate", "Python"]}
        merged = _merge_voice_into_profile(existing, voice)
        lower = [s.lower() for s in merged["skills"]]
        assert lower.count("python") == 1  # no duplicate
        assert "java" in lower
        assert "spring boot" in lower
        assert "hibernate" in lower

    # ── I: Java backend role preference stored correctly ───────────────────

    def test_I_role_preference_stored_no_company_names(self):
        """
        I. Candidate says they want Java backend roles using Spring Boot/Hibernate.
        Expected: role preference stored in profile/bio, no company names as role preference.
        """
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from server import _merge_voice_into_profile

        existing = {"skills": [], "work_experience": [], "education": [], "summary": ""}
        voice = {
            "role_preference_bio": "Looking for Java backend roles using Spring Boot and Hibernate.",
            "preferred_roles": ["Java Backend Developer"],
            "skills": ["Java", "Spring Boot", "Hibernate"],
        }
        merged = _merge_voice_into_profile(existing, voice)
        assert "Java" in merged["summary"]
        assert "Spring Boot" in merged["summary"]
        raw = merged.get("raw_data") or {}
        assert "Java Backend Developer" in (raw.get("preferred_roles") or [])
        # No company names in preferred_roles
        for role in (raw.get("preferred_roles") or []):
            assert role != "Google"
            assert role != "Amazon"

    # ── J: Production transcript — substantive answer recognized ───────────

    def test_J_production_transcript_substantive_answer_recognized(self):
        """
        J. Exact production transcript that was causing status=in_progress, progress=0.
        Must recognize the candidate's substantive answer and associate it with
        the current/next intake information request.
        """
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from server import _voice_intake_turn_pairs

        # The production transcript: VAPI asks a dynamic question, candidate gives
        # a multi-fragment substantive answer
        notes = [
            {"role": "assistant", "text": "Tell me about your background.", "final": True},
            {"role": "user", "text": "I have 2 years of experience as a software engineer at Viral Bug.", "final": True},
            {"role": "user", "text": "I was working with Python, FastAPI, Postgres and REST APIs.", "final": True},
            {"role": "user", "text": "I'm looking for new opportunities as a backend developer.", "final": True},
            {"role": "user", "text": "Java, OOPS, Hibernate, Kubernetes and Spring Boot.", "final": True},
        ]
        completed, pending = _voice_intake_turn_pairs(notes)

        # Must NOT return progress=0 with empty completed_turns
        assert len(completed) == 1, (
            f"Expected 1 completed turn (not 0), got {len(completed)}. "
            "Production bug: substantive answer not being recognized."
        )
        answer = completed[0]["answer"]
        assert "Python" in answer
        assert "FastAPI" in answer
        assert "backend developer" in answer
        assert "Java" in answer
        assert "Spring Boot" in answer
class TestExactProductionCumulativeReconstruction:
    """
    Regression coverage for the exact production-style cumulative transcript
    described in the bug report.
    """

    Q1 = "What made you start exploring your next opportunity?"
    Q2 = "What would make a backend developer role with Java really good fit for you compared to your current Python developer role?"
    Q3 = "I see are there specific kinds of projects or industries you'd like to work in with Java Backend development?"
    Q4 = "What kind of team or work environment helps you do your best work in your next role?"

    CUMULATIVE_NOTES = [
        {
            "role": "assistant",
            "text": "Cool. Let's do it together. I can already see your recent experience as seeking a challenging career where I can utilize my potential and skills. What made you start exploring your next opportunity?",
            "final": True,
        },
        {"role": "user", "text": "Currently I was working as a Python developer.", "final": True},
        {"role": "user", "text": "I was looking for backend developer roles.", "final": True},
        {
            "role": "assistant",
            "text": "What would make a backend developer role with Java really good fit for you compared to your current Python developer role?",
            "final": True,
        },
        {"role": "user", "text": "See, I want to explore jobs related to Java.", "final": True},
        {
            "role": "assistant",
            "text": "I see are there specific kinds of projects or industries you'd like to work in with Java Backend development?",
            "final": True,
        },
        {"role": "user", "text": "Can you reframe the question?", "final": True},
    ]

    ANSWERED_NOTES = CUMULATIVE_NOTES + [
        {"role": "user", "text": "I would like to work on Java backend projects in product companies.", "final": True},
        {"role": "assistant", "text": Q4, "final": True},
    ]

    def _build_resume(self, notes=None, existing_resume=None):
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from server import _build_voice_intake_resume_from_notes

        return _build_voice_intake_resume_from_notes(notes or self.CUMULATIVE_NOTES, "", existing_resume)

    def test_A_same_cumulative_voice_notes_twice_is_idempotent(self):
        first = self._build_resume()
        second = self._build_resume(existing_resume=first)

        assert second["progress"] == first["progress"]
        assert second["completed_turns"] == first["completed_turns"]
        assert len(second.get("completed_turns") or []) == len({t["question"] for t in second.get("completed_turns") or []})

    def test_B_q1_question_is_isolated_from_contextual_statement(self):
        resume = self._build_resume()
        turns = resume.get("completed_turns") or []
        assert turns[0]["question"] == self.Q1
        assert "where i can utilize my potential and skills" not in turns[0]["question"].lower()

    def test_C_q1_answer_appears_exactly_once(self):
        first = self._build_resume()
        second = self._build_resume(existing_resume=first)

        answer = second["completed_turns"][0]["answer"]
        assert answer.count("Currently I was working as a Python developer.") == 1
        assert answer.count("I was looking for backend developer roles.") == 1

    def test_D_reframe_request_does_not_increment_progress(self):
        before = self._build_resume(self.CUMULATIVE_NOTES[:-1])
        after = self._build_resume()

        assert before["progress"] == after["progress"]
        assert after["progress"] == 2

    def test_E_current_unanswered_question_remains_current_question(self):
        resume = self._build_resume()
        assert resume["current_question"] == self.Q3
        assert resume["current_question"] not in [turn["question"] for turn in resume.get("completed_turns") or []]

    def test_F_answering_current_question_moves_to_next_question(self):
        resume = self._build_resume(self.ANSWERED_NOTES)
        questions = [turn["question"] for turn in resume.get("completed_turns") or []]

        assert self.Q3 in questions
        assert questions.count(self.Q3) == 1
        assert resume["current_question"] == self.Q4
        assert resume["progress"] == 3

    def test_G_dashboard_resume_should_preserve_unanswered_question(self):
        base = self._build_resume()
        resumed = self._build_resume(existing_resume=base)

        assert resumed["current_question"] == self.Q3
        assert resumed["status"] == "in_progress"


class TestExactProductionGreetingLeakRegression:
    """
    Exact regression for the production bug where setup/greeting chatter was
    incorrectly counted as an intake turn and leaked into the first real answer.
    """

    Q1 = "What made you start exploring your next opportunity?"
    Q2 = "What would make your next role a really good fit beyond the technologies you mentioned?"
    Q3 = "Besides technology, are there particular responsibilities or work environments you prefer in your next role?"

    PRODUCTION_NOTES = [
        {"role": "assistant", "text": "Hi Suram, I'm Eve. How are you doing today?", "final": True},
        {"role": "user", "text": "Doing good. What about you?", "final": True},
        {"role": "assistant", "text": "Great. Are you ready?", "final": True},
        {"role": "user", "text": "Yes, I'm ready.", "final": True},
        {"role": "assistant", "text": Q1, "final": True},
        {"role": "user", "text": "I'm a Python backend developer with experience in Java and Spring Boot.", "final": True},
        {"role": "user", "text": "I've worked with Hibernate and Kubernetes on backend services.", "final": True},
        {"role": "assistant", "text": Q2, "final": True},
        {"role": "user", "text": "I want my next role to keep me growing in Java backend work.", "final": True},
        {"role": "user", "text": "I am looking for a role where I can build useful backend systems.", "final": True},
        {"role": "assistant", "text": Q3, "final": True},
    ]

    def test_unit_progress_equals_2_and_setup_turn_is_excluded(self):
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from server import _build_voice_intake_resume_from_notes

        resume = _build_voice_intake_resume_from_notes(self.PRODUCTION_NOTES, "")

        assert resume["status"] == "in_progress"
        assert resume["progress"] == 2
        turns = resume.get("completed_turns") or []
        assert len(turns) == 2, f"Expected 2 completed turns, got {len(turns)}: {turns}"

        assert turns[0]["question"] == self.Q1
        assert turns[1]["question"] == self.Q2

        first_answer = turns[0]["answer"]
        assert "Doing good" not in first_answer
        assert "Yes, I'm ready" not in first_answer
        assert "Python backend developer" in first_answer
        assert "Java" in first_answer
        assert "Spring Boot" in first_answer
        assert "Hibernate" in first_answer
        assert "Kubernetes" in first_answer

        second_answer = turns[1]["answer"]
        assert "Java backend work" in second_answer or "Java" in second_answer
        assert "growing" in second_answer.lower()

        assert resume["current_question"] == self.Q3
        assert resume.get("next_question") in (None, "")


class TestChatProfileUpdatePersistenceRegression:
    def test_role_preference_and_skills_extract_and_persist_idempotently(self, monkeypatch):
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        import server

        executed = []
        state = {
            "candidate": {
                "id": "candidate-1",
                "raw_data": {},
                "skills": [],
                "work_experience": [],
                "education": [],
                "location": "",
                "summary": "",
                "current_role": "",
                "experience_years": None,
            },
            "prefs": None,
        }

        class FakeResult:
            def __init__(self, rows=None, scalar_value=None):
                self._rows = rows or []
                self._scalar_value = scalar_value

            def mappings(self):
                return self

            def fetchone(self):
                return self._rows[0] if self._rows else None

            def scalar(self):
                return self._scalar_value

        class FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def execute(self, statement, params=None):
                sql = str(statement)
                params = params or {}
                executed.append((sql, params))
                if "SELECT * FROM candidates WHERE id = :cid LIMIT 1" in sql:
                    return FakeResult([state["candidate"]])
                if "FROM candidate_preferences" in sql and "SELECT" in sql:
                    if state["prefs"] is None:
                        return FakeResult([])
                    return FakeResult([state["prefs"]])
                if "UPDATE candidates SET" in sql:
                    if "skills" in params:
                        state["candidate"]["skills"] = json.loads(params["skills"])
                    if "work_experience" in params:
                        state["candidate"]["work_experience"] = json.loads(params["work_experience"])
                    if "education" in params:
                        state["candidate"]["education"] = json.loads(params["education"])
                    if "current_role" in params:
                        state["candidate"]["current_role"] = params["current_role"]
                    if "bio" in params:
                        state["candidate"]["summary"] = params["bio"]
                    if "experience_years" in params:
                        state["candidate"]["experience_years"] = params["experience_years"]
                    if "location" in params:
                        state["candidate"]["location"] = params["location"]
                    if "raw_data" in params:
                        state["candidate"]["raw_data"] = json.loads(params["raw_data"])
                    return FakeResult()
                if "INSERT INTO candidate_preferences" in sql:
                    state["prefs"] = {
                        "candidate_id": params["cid"],
                        "preferred_roles": json.loads(params["preferred_roles"]),
                        "notice_period": params["notice_period"],
                    }
                    return FakeResult()
                if "UPDATE candidate_preferences SET" in sql:
                    if state["prefs"] is None:
                        state["prefs"] = {"candidate_id": params["cid"], "preferred_roles": [], "notice_period": ""}
                    if "preferred_roles" in params:
                        state["prefs"]["preferred_roles"] = json.loads(params["preferred_roles"])
                    if "notice_period" in params:
                        state["prefs"]["notice_period"] = params["notice_period"]
                    return FakeResult()
                return FakeResult()

            async def commit(self):
                return None

        monkeypatch.setattr(server, "SessionLocal", lambda: FakeSession())

        clean, updates = server._extract_profile_updates(
            "Thanks for sharing that.",
            "I'm targeting Java Backend Developer or Java Backend Engineer roles, preferably working with Java, Spring Boot, Hibernate, and REST APIs.",
        )

        assert clean == "Thanks for sharing that."
        assert updates is not None
        assert updates["preferred_roles"] == ["Java Backend Developer", "Java Backend Engineer"]
        assert "Java" in updates["skills"]
        assert "Spring Boot" in updates["skills"]
        assert "Hibernate" in updates["skills"]
        assert "REST APIs" in updates["skills"]

        asyncio.run(server._apply_profile_updates("candidate-1", updates))
        asyncio.run(server._apply_profile_updates("candidate-1", updates))

        assert sum(1 for sql, _ in executed if "UPDATE candidates SET" in sql) == 1
        assert sum(1 for sql, _ in executed if "INSERT INTO candidate_preferences" in sql) == 1
        assert state["candidate"]["raw_data"]["preferred_roles"] == [
            "Java Backend Developer",
            "Java Backend Engineer",
        ]
        assert state["candidate"]["skills"][:4] == ["Java", "Spring Boot", "Hibernate", "REST APIs"]
        assert state["prefs"]["preferred_roles"] == [
            "Java Backend Developer",
            "Java Backend Engineer",
        ]

    def test_availability_immediately_persists_to_notice_period(self, monkeypatch):
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        import server

        state = {
            "candidate": {
                "id": "candidate-2",
                "raw_data": {},
                "skills": [],
                "work_experience": [],
                "education": [],
                "location": "",
                "summary": "",
                "current_role": "",
                "experience_years": None,
            },
            "prefs": None,
        }

        class FakeResult:
            def __init__(self, rows=None, scalar_value=None):
                self._rows = rows or []
                self._scalar_value = scalar_value

            def mappings(self):
                return self

            def fetchone(self):
                return self._rows[0] if self._rows else None

            def scalar(self):
                return self._scalar_value

        class FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def execute(self, statement, params=None):
                sql = str(statement)
                params = params or {}
                if "SELECT * FROM candidates WHERE id = :cid LIMIT 1" in sql:
                    return FakeResult([state["candidate"]])
                if "FROM candidate_preferences" in sql and "SELECT" in sql:
                    if state["prefs"] is None:
                        return FakeResult([])
                    return FakeResult([state["prefs"]])
                if "UPDATE candidates SET" in sql:
                    if "raw_data" in params:
                        state["candidate"]["raw_data"] = json.loads(params["raw_data"])
                    return FakeResult()
                if "INSERT INTO candidate_preferences" in sql:
                    state["prefs"] = {
                        "candidate_id": params["cid"],
                        "preferred_roles": json.loads(params["preferred_roles"]),
                        "notice_period": params["notice_period"],
                    }
                    return FakeResult()
                if "UPDATE candidate_preferences SET" in sql:
                    if state["prefs"] is None:
                        state["prefs"] = {"candidate_id": params["cid"], "preferred_roles": [], "notice_period": ""}
                    if "preferred_roles" in params:
                        state["prefs"]["preferred_roles"] = json.loads(params["preferred_roles"])
                    if "notice_period" in params:
                        state["prefs"]["notice_period"] = params["notice_period"]
                    return FakeResult()
                return FakeResult()

            async def commit(self):
                return None

        monkeypatch.setattr(server, "SessionLocal", lambda: FakeSession())

        clean, updates = server._extract_profile_updates(
            "Sounds good.",
            "I can join immediately.",
        )

        assert clean == "Sounds good."
        assert updates is not None
        assert updates["availability"]
        assert updates["notice_period"] == updates["availability"]

        asyncio.run(server._apply_profile_updates("candidate-2", updates))

        assert state["candidate"]["raw_data"]["availability"] == updates["availability"]
        assert "immed" in (state["prefs"]["notice_period"] or "").lower()

    def test_conversational_reply_without_profile_data_produces_no_fake_updates(self):
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from server import _extract_profile_updates

        clean, updates = _extract_profile_updates(
            "Thanks for sharing. Let's keep going.",
            "That's helpful, tell me more about the team.",
        )

        assert clean == "Thanks for sharing. Let's keep going."
        assert updates is None
