import os
import sys

import pytest

from fastapi import HTTPException

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import server  # noqa: E402


VALID_SECRET = "candidate-session-test-secret-with-sufficient-length"
CANDIDATE_ID = "11111111-1111-1111-1111-111111111111"


def _set_secret(monkeypatch, value):
    monkeypatch.setattr(server, "CANDIDATE_SESSION_SECRET", value)


def test_production_requires_dedicated_secret():
    with pytest.raises(RuntimeError, match="required in production"):
        server._validated_candidate_session_secret("", environment="production")


def test_production_rejects_known_insecure_placeholder():
    with pytest.raises(RuntimeError, match="insecure placeholder"):
        server._validated_candidate_session_secret("dev-candidate-session-secret", environment="production")


def test_production_accepts_valid_dedicated_secret():
    assert server._validated_candidate_session_secret(VALID_SECRET, environment="production") == VALID_SECRET


def test_candidate_jwt_round_trip_uses_configured_secret(monkeypatch):
    _set_secret(monkeypatch, VALID_SECRET)
    token = server._issue_candidate_session_token(CANDIDATE_ID)
    server._verify_candidate_session_token(token, CANDIDATE_ID)


def test_candidate_jwt_rejects_token_signed_with_incorrect_secret(monkeypatch):
    _set_secret(monkeypatch, VALID_SECRET)
    token = server._issue_candidate_session_token(CANDIDATE_ID)
    _set_secret(monkeypatch, "different-candidate-session-secret-with-sufficient-length")

    with pytest.raises(HTTPException) as exc:
        server._verify_candidate_session_token(token, CANDIDATE_ID)
    assert exc.value.status_code == 401


def test_candidate_secret_cannot_equal_internal_service_secret(monkeypatch):
    monkeypatch.setenv("EVE_INTERNAL_TOKEN", VALID_SECRET)
    with pytest.raises(RuntimeError, match="separate from EVE_INTERNAL_TOKEN"):
        server._validated_candidate_session_secret(VALID_SECRET, environment="production")


def test_existing_development_test_configuration_remains_explicit(monkeypatch):
    _set_secret(monkeypatch, VALID_SECRET)
    monkeypatch.setenv("EVE_ENVIRONMENT", "development")
    assert server._validated_candidate_session_secret() == VALID_SECRET


@pytest.mark.asyncio
async def test_existing_candidate_resume_requires_matching_session_before_file_access(monkeypatch):
    class UnreadableUpload:
        filename = "resume.pdf"

        async def read(self):
            raise AssertionError("unauthorized request reached file processing")

    candidate_a = CANDIDATE_ID
    candidate_b = "22222222-2222-2222-2222-222222222222"
    _set_secret(monkeypatch, VALID_SECRET)
    token_for_a = server._issue_candidate_session_token(candidate_a)

    with pytest.raises(HTTPException) as exc:
        await server.parse_resume(
            file=UnreadableUpload(),
            existing_id=candidate_b,
            authorization=f"Bearer {token_for_a}",
        )
    assert exc.value.status_code in {401, 403}


@pytest.mark.asyncio
async def test_existing_candidate_resume_rejects_missing_session_before_file_access(monkeypatch):
    class UnreadableUpload:
        filename = "resume.pdf"

        async def read(self):
            raise AssertionError("unauthorized request reached file processing")

    _set_secret(monkeypatch, VALID_SECRET)
    with pytest.raises(HTTPException) as exc:
        await server.parse_resume(file=UnreadableUpload(), existing_id=CANDIDATE_ID)
    assert exc.value.status_code == 401
