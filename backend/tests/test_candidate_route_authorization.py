import asyncio
import os
import sys

import pytest
from fastapi import HTTPException
from jose import jwt

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import server


@pytest.mark.parametrize(
    "handler,args",
    [
        (server.download_candidate_profile, ("candidate-a",)),
        (server.get_candidate_jobs, ("candidate-a",)),
        (server.get_candidate_notifications, ("candidate-a",)),
    ],
)
def test_candidate_routes_reject_missing_session_before_business_logic(handler, args):
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(handler(*args))

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Missing candidate session."


def test_candidate_session_token_cannot_be_reused_for_another_candidate(monkeypatch):
    monkeypatch.setattr(server, "CANDIDATE_SESSION_SECRET", "x" * 32)
    token = server._issue_candidate_session_token("candidate-a")

    with pytest.raises(HTTPException) as exc_info:
        server._authorize_candidate("candidate-b", f"Bearer {token}")

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Forbidden."


def test_expired_candidate_session_is_rejected(monkeypatch):
    monkeypatch.setattr(server, "CANDIDATE_SESSION_SECRET", "x" * 32)
    token = jwt.encode(
        {"sub": "candidate-a", "scope": "candidate", "exp": 1},
        server.CANDIDATE_SESSION_SECRET,
        algorithm="HS256",
    )

    with pytest.raises(HTTPException) as exc_info:
        server._authorize_candidate("candidate-a", f"Bearer {token}")

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Invalid candidate session."
