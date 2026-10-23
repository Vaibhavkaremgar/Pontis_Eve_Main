import os
import sys

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import server


class _Mappings:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _Result:
    def __init__(self, row=None, mapping=None):
        self._row = row
        self._mapping = mapping

    def fetchone(self):
        return self._row

    def mappings(self):
        return _Mappings(self._mapping)


class _Session:
    def __init__(self, results):
        self.results = iter(results)
        self.statements = []
        self.rollback_count = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def execute(self, statement, params=None):
        self.statements.append((str(statement), params or {}))
        return next(self.results)

    async def rollback(self):
        self.rollback_count += 1


def test_google_profile_requires_stable_subject():
    with pytest.raises(HTTPException) as exc:
        server._google_profile_from_userinfo(
            {"email": "person@example.test", "email_verified": True}
        )

    assert exc.value.status_code == 400


def test_google_profile_never_trusts_unverified_email():
    profile = server._google_profile_from_userinfo(
        {
            "sub": "google-subject",
            "email": "person@example.test",
            "email_verified": False,
        }
    )

    assert profile["google_id"] == "google-subject"
    assert profile["email"] is None
    assert profile["email_verified"] is False


def test_google_registration_token_is_scoped_and_bound(monkeypatch):
    monkeypatch.setattr(server, "CANDIDATE_SESSION_SECRET", "x" * 32)

    token = server._issue_google_registration_token(
        "google-subject", "person@example.test"
    )

    assert server._verify_google_registration_token(token) == (
        "google-subject",
        "person@example.test",
    )
    with pytest.raises(HTTPException):
        server._verify_candidate_session_token(token, "candidate-id")


@pytest.mark.asyncio
async def test_returning_google_subject_resolves_mapped_candidate(monkeypatch):
    session = _Session([
        _Result(mapping={
            "id": "candidate-id",
            "name": "Candidate",
            "phone": "+10000000000",
            "summary": "Profile",
            "parsing_status": "completed",
        })
    ])
    monkeypatch.setattr(server, "SessionLocal", lambda: session)

    candidate_id, needs_onboarding = await server._resolve_google_identity(
        {"google_id": "google-subject", "email": "person@example.test"}
    )

    assert candidate_id == "candidate-id"
    assert needs_onboarding is False
    assert len(session.statements) == 1
    assert "provider_subject" in session.statements[0][0]


@pytest.mark.asyncio
async def test_unknown_subject_with_existing_email_uses_new_registration(monkeypatch):
    session = _Session([_Result(mapping=None)])
    monkeypatch.setattr(server, "SessionLocal", lambda: session)

    candidate_id, account_linking_required = await server._resolve_google_identity(
        {"google_id": "new-google-subject", "email": "person@example.test"}
    )

    assert candidate_id is None
    assert account_linking_required is False
    assert len(session.statements) == 1


@pytest.mark.asyncio
async def test_concurrent_google_registration_uses_verified_winner():
    session = _Session([
        _Result(row=None),
        _Result(row=("winning-candidate", "Person@Example.Test")),
    ])

    candidate_id, created = await server._create_google_identity_or_resolve_winner(
        session,
        candidate_id="losing-candidate",
        subject="google-subject",
        verified_email="person@example.test",
    )

    assert candidate_id == "winning-candidate"
    assert created is False
    assert session.rollback_count == 1


@pytest.mark.asyncio
async def test_google_identity_mapping_cannot_be_reassigned_to_conflicting_candidate():
    session = _Session([
        _Result(row=None),
        _Result(row=("other-candidate", "different@example.test")),
    ])

    with pytest.raises(HTTPException) as exc:
        await server._create_google_identity_or_resolve_winner(
            session,
            candidate_id="new-candidate",
            subject="google-subject",
            verified_email="person@example.test",
        )

    assert exc.value.status_code == 409
    assert session.rollback_count == 1


class _GoogleClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def post(self, *args, **kwargs):
        return server.httpx.Response(200, json={"access_token": "provider-access-token"})

    async def get(self, *args, **kwargs):
        return server.httpx.Response(200, json={
            "sub": "new-google-subject",
            "email": "person@example.test",
            "email_verified": True,
        })


@pytest.mark.asyncio
async def test_missing_mapping_issues_new_registration_not_email_matched_session(monkeypatch):
    monkeypatch.setattr(server, "GOOGLE_CLIENT_ID", "client-id")
    monkeypatch.setattr(server, "GOOGLE_CLIENT_SECRET", "client-secret")
    monkeypatch.setattr(server.httpx, "AsyncClient", _GoogleClient)

    async def consume_transaction(provider, returned_state, stored_state):
        return "transaction-id", "pkce-verifier"

    async def resolve_identity(profile):
        return None, False

    captured = {}

    async def create_exchange(transaction_id, payload):
        captured.update(payload)
        return "exchange-code"

    def unexpected_session(candidate_id):
        raise AssertionError("email-only lookup reached candidate session issuance")

    monkeypatch.setattr(server, "_consume_oauth_transaction", consume_transaction)
    monkeypatch.setattr(server, "_resolve_google_identity", resolve_identity)
    monkeypatch.setattr(server, "_create_oauth_exchange", create_exchange)
    monkeypatch.setattr(server, "_issue_candidate_session_token", unexpected_session)

    response = await server.google_callback(
        code="authorization-code",
        state="state",
        google_oauth_state="state",
    )

    assert response.status_code == 302
    assert captured["candidate_id"] is None
    assert captured["candidate_token"] == ""
    assert captured["account_linking_required"] is False
    assert captured["google_registration_token"]
    assert captured["needs_onboarding"] is True


@pytest.mark.asyncio
async def test_unverified_unknown_google_user_is_not_sent_to_registration(monkeypatch):
    monkeypatch.setattr(server, "GOOGLE_CLIENT_ID", "client-id")
    monkeypatch.setattr(server, "GOOGLE_CLIENT_SECRET", "client-secret")

    class UnverifiedGoogleClient(_GoogleClient):
        async def get(self, *args, **kwargs):
            return server.httpx.Response(200, json={
                "sub": "new-google-subject",
                "email": "person@example.test",
                "email_verified": False,
            })

    monkeypatch.setattr(server.httpx, "AsyncClient", UnverifiedGoogleClient)

    async def consume_transaction(provider, returned_state, stored_state):
        return "transaction-id", "pkce-verifier"

    async def resolve_identity(profile):
        assert profile["email"] is None
        return None, False

    captured = {}

    async def create_exchange(transaction_id, payload):
        captured.update(payload)
        return "exchange-code"

    monkeypatch.setattr(server, "_consume_oauth_transaction", consume_transaction)
    monkeypatch.setattr(server, "_resolve_google_identity", resolve_identity)
    monkeypatch.setattr(server, "_create_oauth_exchange", create_exchange)

    await server.google_callback(
        code="authorization-code",
        state="state",
        google_oauth_state="state",
    )

    assert captured["needs_onboarding"] is False
    assert captured["google_registration_blocked"] is True
    assert captured["google_registration_token"] == ""
