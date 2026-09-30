"""Focused coverage for the production VAPI webhook endpoint."""

import os
import sys
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import server
from server import app


client = TestClient(app)


def test_vapi_webhook_accepts_valid_event():
    response = client.post(
        "/api/webhooks/vapi",
        json={
            "type": "end-of-call-report",
            "call": {"id": "call-from-vapi"},
            "artifact": {"transcript": "hello"},
        },
    )

    assert 200 <= response.status_code < 300
    assert response.json() == {"status": "ok"}


def test_vapi_webhook_acknowledges_unknown_event_without_500():
    response = client.post(
        "/api/webhooks/vapi",
        json={"type": "some-future-vapi-event", "data": {"anything": True}},
    )

    assert 200 <= response.status_code < 300


def test_vapi_webhook_acknowledges_non_object_json_without_500():
    response = client.post("/api/webhooks/vapi", json=["unrelated", "payload"])

    assert 200 <= response.status_code < 300


class _FakeDb:
    def __init__(self, rowcount=1):
        self.execute = AsyncMock(return_value=type("Result", (), {"rowcount": rowcount})())
        self.commit = AsyncMock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


def _terminal_payload(transcript="complete transcript"):
    return {
        "type": "end-of-call-report",
        "call": {
            "id": "call-from-vapi",
            "metadata": {"candidateId": "9ef2e4f9-90a6-43f8-ba94-98af39c86fc4"},
            "endedReason": "silence-timed-out",
        },
        "artifact": {"transcript": transcript},
    }


def test_intermediate_vapi_event_does_not_complete_intake(monkeypatch):
    db = _FakeDb()
    monkeypatch.setattr(server, "SessionLocal", lambda: db)

    response = client.post(
        "/api/webhooks/vapi",
        json={"type": "conversation-update", "call": {"metadata": {"candidateId": "bad"}}},
    )

    assert response.status_code == 200
    db.execute.assert_not_awaited()


def test_final_vapi_event_completes_correct_intake_and_preserves_transcript(monkeypatch):
    db = _FakeDb()
    monkeypatch.setattr(server, "SessionLocal", lambda: db)
    monkeypatch.setattr(
        server,
        "_get_candidate_row",
        AsyncMock(return_value={"raw_data": {"voice_intake": {"status": "completed"}}}),
    )
    matching = AsyncMock()
    monkeypatch.setattr(server, "_trigger_matching", matching)

    response = client.post("/api/webhooks/vapi", json=_terminal_payload())

    assert response.status_code == 200
    db.execute.assert_awaited_once()
    params = db.execute.await_args.args[1]
    assert params["candidate_id"] == "9ef2e4f9-90a6-43f8-ba94-98af39c86fc4"
    assert params["transcript"] == "complete transcript"
    sql = str(db.execute.await_args.args[0])
    assert "status = 'completed'" in sql
    assert "completed_at = CASE WHEN :status = 'completed' THEN now()" in sql
    assert params["status"] == "completed"
    awaitable_commit = db.commit.assert_awaited_once()
    assert awaitable_commit is None
    matching.assert_awaited_once_with("9ef2e4f9-90a6-43f8-ba94-98af39c86fc4")


def test_duplicate_final_vapi_event_does_not_schedule_matching(monkeypatch):
    db = _FakeDb(rowcount=0)
    monkeypatch.setattr(server, "SessionLocal", lambda: db)
    monkeypatch.setattr(
        server,
        "_get_candidate_row",
        AsyncMock(return_value={"raw_data": {"voice_intake": {"status": "completed"}}}),
    )
    matching = AsyncMock()
    monkeypatch.setattr(server, "_trigger_matching", matching)

    response = client.post("/api/webhooks/vapi", json=_terminal_payload())

    assert response.status_code == 200
    matching.assert_not_awaited()


def test_incomplete_vapi_end_call_preserves_resumable_progress(monkeypatch):
    db = _FakeDb()
    monkeypatch.setattr(server, "SessionLocal", lambda: db)
    monkeypatch.setattr(
        server,
        "_get_candidate_row",
        AsyncMock(return_value={"raw_data": {"voice_intake": {
            "status": "in_progress",
            "current_question": "What are your key skills?",
        }}}),
    )
    matching = AsyncMock()
    monkeypatch.setattr(server, "_trigger_matching", matching)

    response = client.post("/api/webhooks/vapi", json=_terminal_payload("partial transcript"))

    assert response.status_code == 200
    params = db.execute.await_args.args[1]
    assert params["status"] == "in_progress"
    assert params["transcript"] == "partial transcript"
    matching.assert_not_awaited()


def test_manual_end_after_all_questions_does_not_complete(monkeypatch):
    db = _FakeDb()
    monkeypatch.setattr(server, "SessionLocal", lambda: db)
    monkeypatch.setattr(
        server,
        "_get_candidate_row",
        AsyncMock(return_value={"raw_data": {"voice_intake": {"status": "completed"}}}),
    )
    payload = _terminal_payload()
    payload["call"]["endedReason"] = "customer-ended-call"

    response = client.post("/api/webhooks/vapi", json=payload)

    assert response.status_code == 200
    assert db.execute.await_args.args[1]["status"] == "in_progress"
