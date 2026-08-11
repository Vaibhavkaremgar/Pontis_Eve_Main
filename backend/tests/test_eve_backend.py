"""Backend tests for Eve (candidate-side AI) API."""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://candidate-hub-97.preview.emergentagent.com").rstrip("/")


def test_root():
    r = requests.get(f"{BASE_URL}/api/", timeout=15)
    assert r.status_code == 200
    data = r.json()
    assert "Eve" in data.get("message", "") or "Pontis" in data.get("message", "")


def test_chat_eve_identity():
    payload = {
        "messages": [
            {"role": "user", "content": "Hello, please tell me your name in one short sentence."}
        ],
        "session_id": "test-eve-identity-1",
    }
    r = requests.post(f"{BASE_URL}/api/chat", json=payload, timeout=60)
    assert r.status_code == 200, r.text
    data = r.json()
    assert "reply" in data
    assert isinstance(data["reply"], str) and len(data["reply"]) > 0
    reply_lower = data["reply"].lower()
    # Should not self-identify as Jack
    assert "jack" not in reply_lower, f"Reply mentions Jack: {data['reply']}"


def test_chat_empty_messages():
    r = requests.post(f"{BASE_URL}/api/chat", json={"messages": [], "session_id": "t"}, timeout=15)
    assert r.status_code == 400


def test_chat_no_user_msg():
    r = requests.post(f"{BASE_URL}/api/chat", json={
        "messages": [{"role": "assistant", "content": "hi"}], "session_id": "t"
    }, timeout=15)
    assert r.status_code == 400
