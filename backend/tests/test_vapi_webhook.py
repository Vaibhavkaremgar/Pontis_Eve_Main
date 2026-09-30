"""Focused coverage for the production VAPI webhook endpoint."""

import os
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
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
