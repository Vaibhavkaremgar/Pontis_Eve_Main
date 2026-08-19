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
import uuid
import pytest
import requests

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
