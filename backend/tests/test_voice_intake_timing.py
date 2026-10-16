"""Focused coverage for metadata-only Voice Intake timing instrumentation."""

import asyncio
import json
import os
import sys


sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import server


CANDIDATE_ID = "11111111-1111-1111-1111-111111111111"


def _authorization(candidate_id=CANDIDATE_ID):
    return f"Bearer {server._issue_candidate_session_token(candidate_id)}"


def test_voice_intake_timing_excludes_candidate_and_sensitive_content(monkeypatch):
    logged = []
    monkeypatch.setattr(server.logger, "info", lambda *args: logged.append(args))

    server._voice_intake_timing(
        "request_finished",
        request_id="11111111-1111-4111-8111-111111111111",
        vapi_call_id="call-456",
        transcript_revision="4",
        endpoint="/api/voice/candidate-intake",
        duration_ms=12.34,
        outcome="success",
    )

    fields = json.loads(logged[0][1])
    assert fields["request_id"] == "11111111-1111-4111-8111-111111111111"
    assert fields["vapi_call_id"] == "call-456"
    assert "candidate_id" not in fields
    assert "transcript" not in fields
    assert "voice_notes" not in fields


def test_duplicate_finalization_records_completion_timing(monkeypatch):
    captured = []

    class DuplicateResult:
        def fetchone(self):
            return object()

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def execute(self, *_args, **_kwargs):
            return DuplicateResult()

    monkeypatch.setattr(server, "SessionLocal", lambda: Session())
    monkeypatch.setattr(server, "_get_candidate_row", lambda _candidate_id: _async_value({}))
    monkeypatch.setattr(server, "_voice_intake_timing", lambda event, **fields: captured.append((event, fields)))

    response = asyncio.run(server.candidate_voice_intake(
        server.VoiceCandidateIntakeRequest(
            candidate_id=CANDIDATE_ID,
            transcript="Candidate: already submitted this.",
            vapi_call_id="call-duplicate",
            transcript_revision="7",
        ),
        authorization=_authorization(),
        x_request_id="22222222-2222-4222-8222-222222222222",
    ))

    assert response["status"] == "duplicate"
    assert captured[-1] == ("request_finished", {
        "request_id": "22222222-2222-4222-8222-222222222222",
        "vapi_call_id": "call-duplicate",
        "transcript_revision": "7",
        "endpoint": "/api/voice/candidate-intake",
        "duration_ms": captured[-1][1]["duration_ms"],
        "outcome": "duplicate",
    })
    assert captured[-1][1]["duration_ms"] >= 0


def test_stale_progress_records_completion_timing(monkeypatch):
    captured = []

    async def get_candidate(_candidate_id):
        return {"raw_data": {"voice_intake": {
            "vapi_call_id": "call-stale", "transcript_revision": 3,
        }}}

    monkeypatch.setattr(server, "_get_candidate_row", get_candidate)
    monkeypatch.setattr(server, "_voice_intake_timing", lambda event, **fields: captured.append((event, fields)))

    response = asyncio.run(server.candidate_voice_intake_progress(
        server.VoiceCandidateIntakeProgressRequest(
            candidate_id=CANDIDATE_ID,
            transcript="Candidate: outdated snapshot.",
            vapi_call_id="call-stale",
            transcript_revision="2",
        ),
        authorization=_authorization(),
        x_request_id="33333333-3333-4333-8333-333333333333",
    ))

    assert response["status"] == "ignored_stale"
    assert captured[-1][0] == "request_finished"
    assert captured[-1][1]["request_id"] == "33333333-3333-4333-8333-333333333333"
    assert captured[-1][1]["vapi_call_id"] == "call-stale"
    assert captured[-1][1]["transcript_revision"] == "2"
    assert captured[-1][1]["endpoint"] == "/api/voice/candidate-intake/progress"
    assert captured[-1][1]["outcome"] == "stale"
    assert captured[-1][1]["duration_ms"] >= 0


async def _async_value(value):
    return value


def test_invalid_client_metadata_is_omitted_from_endpoint_timing_logs(monkeypatch):
    logged = []
    candidate_marker = CANDIDATE_ID
    transcript_marker = "Candidate: private transcript-like content"
    malicious_request_id = f"{candidate_marker}\n{transcript_marker}"
    malicious_vapi_id = f"call-{candidate_marker}\n{transcript_marker}"
    malicious_revision = "9\n" + ("x" * 300)

    class DuplicateResult:
        def fetchone(self):
            return object()

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def execute(self, *_args, **_kwargs):
            return DuplicateResult()

    monkeypatch.setattr(server, "SessionLocal", lambda: Session())
    monkeypatch.setattr(server, "_get_candidate_row", lambda _candidate_id: _async_value({}))
    monkeypatch.setattr(server.logger, "info", lambda *args: logged.append(args))

    response = asyncio.run(server.candidate_voice_intake(
        server.VoiceCandidateIntakeRequest(
            candidate_id=CANDIDATE_ID,
            transcript=transcript_marker,
            vapi_call_id=malicious_vapi_id,
            transcript_revision=malicious_revision,
        ),
        authorization=_authorization(),
        x_request_id=malicious_request_id,
    ))

    assert response["status"] == "duplicate"
    timing_logs = [json.loads(args[1]) for args in logged if args[0] == "[voice-intake-timing] %s"]
    assert timing_logs
    serialized_logs = "\n".join(json.dumps(fields) for fields in timing_logs)
    for unsafe_value in (candidate_marker, transcript_marker, malicious_request_id, malicious_vapi_id, malicious_revision):
        assert unsafe_value not in serialized_logs
    for fields in timing_logs:
        assert fields["request_id"] != malicious_request_id
        assert "vapi_call_id" not in fields
        assert "transcript_revision" not in fields


def test_valid_request_id_is_shared_by_analysis_and_extraction_telemetry(monkeypatch):
    captured = {}
    request_id = "44444444-4444-4444-8444-444444444444"
    candidate = {"id": CANDIDATE_ID, "raw_data": {"voice_intake": {}}}

    async def fake_get_candidate(_candidate_id):
        return candidate

    async def fake_analysis(*_args, **kwargs):
        captured["analysis"] = kwargs["request_id"]
        return {"known_topics": [], "missing_topics": [], "completed": False}

    async def fake_extract(*args):
        captured["extraction"] = args[4]
        return {}

    async def noop(*_args, **_kwargs):
        return candidate

    monkeypatch.setattr(server, "_get_candidate_row", fake_get_candidate)
    monkeypatch.setattr(server, "_llm_analyze_intake", fake_analysis)
    monkeypatch.setattr(server, "_extract_voice_info", fake_extract)
    monkeypatch.setattr(server, "_save_voice_intake_resume", noop)
    monkeypatch.setattr(server, "_persist_voice_intake_profile_state", noop)
    monkeypatch.setattr(server, "_sync_voice_ledger", noop)
    monkeypatch.setattr(server, "_sync_profile_updates_to_ledger", noop)
    monkeypatch.setattr(server, "_schedule_voice_intake_matching", lambda *_args: None)
    monkeypatch.setattr(server, "_load_candidate_certificates", noop)

    asyncio.run(server.candidate_voice_intake_progress(
        server.VoiceCandidateIntakeProgressRequest(
            candidate_id=CANDIDATE_ID, transcript="Candidate: I build APIs.",
            vapi_call_id="call-telemetry", transcript_revision="1",
        ),
        authorization=_authorization(), x_request_id=request_id,
    ))

    assert captured == {"analysis": request_id, "extraction": request_id}
