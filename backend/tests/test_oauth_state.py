import json
import os
import sys

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import server  # noqa: E402


@pytest.mark.parametrize("provider", ["google", "linkedin"])
@pytest.mark.asyncio
async def test_oauth_state_rejects_missing_and_mismatched_values(provider):
    with pytest.raises(HTTPException) as missing:
        await server._consume_oauth_transaction(provider, None, "stored")
    assert missing.value.status_code == 400

    with pytest.raises(HTTPException) as mismatch:
        await server._consume_oauth_transaction(provider, "returned", "stored")
    assert mismatch.value.status_code == 400


@pytest.mark.asyncio
async def test_google_and_linkedin_init_bind_state_to_http_only_cookie(monkeypatch):
    monkeypatch.setattr(server, "GOOGLE_CLIENT_ID", "configured-google-client")
    monkeypatch.setattr(server, "LINKEDIN_CLIENT_ID", "configured-linkedin-client")
    monkeypatch.setattr(server, "LINKEDIN_CLIENT_SECRET", "configured-linkedin-secret")
    async def create_transaction(provider, state, verifier):
        return "transaction-id"
    monkeypatch.setattr(server, "_create_oauth_transaction", create_transaction)

    google_response = await server.google_init(redirect=False)
    linkedin_response = await server.linkedin_init(redirect=False)

    assert json.loads(google_response.body)["state"]
    assert json.loads(linkedin_response.body)["state"]
    assert "google_oauth_state=" in google_response.headers["set-cookie"]
    assert "linkedin_oauth_state=" in linkedin_response.headers["set-cookie"]
    assert "HttpOnly" in google_response.headers["set-cookie"]
    assert "HttpOnly" in linkedin_response.headers["set-cookie"]


@pytest.mark.asyncio
async def test_linkedin_top_level_init_redirect_sets_state_cookie(monkeypatch):
    monkeypatch.setattr(server, "LINKEDIN_CLIENT_ID", "configured-linkedin-client")
    monkeypatch.setattr(server, "LINKEDIN_CLIENT_SECRET", "configured-linkedin-secret")
    monkeypatch.setattr(server, "LINKEDIN_REDIRECT_URI", "https://api.example.test/api/auth/linkedin/callback")
    async def create_transaction(provider, state, verifier):
        return "transaction-id"
    monkeypatch.setattr(server, "_create_oauth_transaction", create_transaction)

    response = await server.linkedin_init()

    assert response.status_code == 302
    assert response.headers["location"].startswith("https://www.linkedin.com/oauth/v2/authorization?")
    assert "linkedin_oauth_state=" in response.headers["set-cookie"]
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "Path=/api/auth/linkedin" in response.headers["set-cookie"]
    assert "Secure" in response.headers["set-cookie"]
    assert "SameSite=lax" in response.headers["set-cookie"]


class _MockLinkedInClient:
    response = None
    get_response = None
    error = None
    posted = None

    def __init__(self, *args, **kwargs):
        self.timeout = kwargs.get("timeout")

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, **kwargs):
        type(self).posted = (url, kwargs)
        if type(self).error:
            raise type(self).error
        return type(self).response

    async def get(self, url, **kwargs):
        return type(self).get_response


def _response(status, payload):
    return server.httpx.Response(status, json=payload)


@pytest.fixture(autouse=True)
def _reset_linkedin_client():
    _MockLinkedInClient.response = None
    _MockLinkedInClient.get_response = None
    _MockLinkedInClient.error = None
    _MockLinkedInClient.posted = None


@pytest.mark.asyncio
async def test_linkedin_token_exchange_success_uses_confidential_pkce_request(monkeypatch):
    monkeypatch.setattr(server.httpx, "AsyncClient", _MockLinkedInClient)
    monkeypatch.setattr(server, "LINKEDIN_CLIENT_ID", "client-id")
    monkeypatch.setattr(server, "LINKEDIN_CLIENT_SECRET", "client-secret")
    monkeypatch.setattr(server, "LINKEDIN_REDIRECT_URI", "https://api.example.test/api/auth/linkedin/callback")
    _MockLinkedInClient.response = _response(200, {"access_token": "access-token"})

    result = await server._exchange_linkedin_authorization_code("authorization-code", "pkce-verifier")

    assert result == "access-token"
    url, request = _MockLinkedInClient.posted
    assert url == "https://www.linkedin.com/oauth/v2/accessToken"
    assert request["headers"]["Content-Type"] == "application/x-www-form-urlencoded"
    assert request["data"] == {
        "grant_type": "authorization_code",
        "code": "authorization-code",
        "redirect_uri": "https://api.example.test/api/auth/linkedin/callback",
        "client_id": "client-id",
        "client_secret": "client-secret",
        "code_verifier": "pkce-verifier",
    }


@pytest.mark.parametrize(
    "category,status",
    [
        ("invalid_client", 401),
        ("invalid_redirect_uri", 400),
        ("invalid_grant", 400),
        ("invalid_request", 400),
    ],
)
@pytest.mark.asyncio
async def test_linkedin_token_exchange_rejects_sanitized_oauth_errors(monkeypatch, caplog, category, status):
    monkeypatch.setattr(server.httpx, "AsyncClient", _MockLinkedInClient)
    _MockLinkedInClient.response = _response(status, {"error": category, "error_description": "sensitive upstream detail"})

    with pytest.raises(HTTPException) as exc:
        await server._exchange_linkedin_authorization_code("authorization-code", "pkce-verifier")

    assert exc.value.status_code == 400
    assert exc.value.detail == "LinkedIn token exchange failed."
    assert f"category={category}" in caplog.text
    assert "sensitive upstream detail" not in caplog.text


@pytest.mark.asyncio
async def test_linkedin_token_exchange_rejects_unexpected_non_json_response(monkeypatch, caplog):
    monkeypatch.setattr(server.httpx, "AsyncClient", _MockLinkedInClient)
    _MockLinkedInClient.response = server.httpx.Response(500, text="upstream internal detail")

    with pytest.raises(HTTPException) as exc:
        await server._exchange_linkedin_authorization_code("authorization-code", "pkce-verifier")

    assert exc.value.status_code == 400
    assert "category=unexpected_response" in caplog.text
    assert "upstream internal detail" not in caplog.text


@pytest.mark.asyncio
async def test_linkedin_token_exchange_handles_network_timeout(monkeypatch, caplog):
    monkeypatch.setattr(server.httpx, "AsyncClient", _MockLinkedInClient)
    request = server.httpx.Request("POST", "https://www.linkedin.com/oauth/v2/accessToken")
    _MockLinkedInClient.error = server.httpx.ReadTimeout("timed out", request=request)

    with pytest.raises(HTTPException) as exc:
        await server._exchange_linkedin_authorization_code("authorization-code", "pkce-verifier")

    assert exc.value.status_code == 502
    assert exc.value.detail == "LinkedIn token exchange failed."
    assert "exception_type=ReadTimeout" in caplog.text


@pytest.mark.asyncio
async def test_linkedin_callback_missing_pkce_cannot_create_exchange(monkeypatch):
    monkeypatch.setattr(server, "LINKEDIN_CLIENT_ID", "client-id")
    monkeypatch.setattr(server, "LINKEDIN_CLIENT_SECRET", "client-secret")
    monkeypatch.setattr(server, "LINKEDIN_REDIRECT_URI", "https://api.example.test/api/auth/linkedin/callback")
    async def missing_pkce(provider, returned_state, stored_state):
        raise HTTPException(status_code=400, detail="Invalid OAuth exchange.")
    monkeypatch.setattr(server, "_consume_oauth_transaction", missing_pkce)

    with pytest.raises(HTTPException) as exc:
        await server.linkedin_callback(
            code="authorization-code",
            state="state-without-pkce",
            linkedin_oauth_state="state-without-pkce",
        )

    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_failed_linkedin_exchange_cannot_create_candidate_session_exchange(monkeypatch):
    monkeypatch.setattr(server, "LINKEDIN_CLIENT_ID", "client-id")
    monkeypatch.setattr(server, "LINKEDIN_CLIENT_SECRET", "client-secret")
    monkeypatch.setattr(server, "LINKEDIN_REDIRECT_URI", "https://api.example.test/api/auth/linkedin/callback")

    async def rejected_exchange(code, verifier):
        raise HTTPException(status_code=400, detail="LinkedIn token exchange failed.")

    monkeypatch.setattr(server, "_exchange_linkedin_authorization_code", rejected_exchange)
    async def consume_transaction(provider, returned_state, stored_state):
        return "transaction-id", "verifier"
    exchange_created = False
    async def create_exchange(transaction_id, payload):
        nonlocal exchange_created
        exchange_created = True
        return "exchange-code"
    monkeypatch.setattr(server, "_consume_oauth_transaction", consume_transaction)
    monkeypatch.setattr(server, "_create_oauth_exchange", create_exchange)

    with pytest.raises(HTTPException):
        await server.linkedin_callback(
            code="authorization-code", state="failed-state", linkedin_oauth_state="failed-state"
        )

    assert exchange_created is False


@pytest.mark.asyncio
async def test_successful_linkedin_callback_creates_short_lived_exchange_code(monkeypatch):
    monkeypatch.setattr(server, "LINKEDIN_CLIENT_ID", "client-id")
    monkeypatch.setattr(server, "LINKEDIN_CLIENT_SECRET", "client-secret")
    monkeypatch.setattr(server, "LINKEDIN_REDIRECT_URI", "https://api.example.test/api/auth/linkedin/callback")
    monkeypatch.setattr(server, "FRONTEND_URL", "https://app.example.test")

    async def successful_exchange(code, verifier):
        return "access-token"

    class EmptySession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(server, "_exchange_linkedin_authorization_code", successful_exchange)
    monkeypatch.setattr(server.httpx, "AsyncClient", _MockLinkedInClient)
    monkeypatch.setattr(server, "SessionLocal", lambda: EmptySession())
    _MockLinkedInClient.get_response = _response(200, {"sub": "member-id", "name": "", "email": "", "picture": ""})
    async def consume_transaction(provider, returned_state, stored_state):
        return "transaction-id", "verifier"
    captured = {}
    async def create_exchange(transaction_id, payload):
        captured["transaction_id"] = transaction_id
        captured["payload"] = payload
        return "exchange-code"
    monkeypatch.setattr(server, "_consume_oauth_transaction", consume_transaction)
    monkeypatch.setattr(server, "_create_oauth_exchange", create_exchange)

    response = await server.linkedin_callback(
        code="authorization-code", state="successful-state", linkedin_oauth_state="successful-state"
    )

    assert response.status_code == 302
    assert response.headers["location"].endswith("exchange_code=exchange-code")
    assert captured["transaction_id"] == "transaction-id"
    assert captured["payload"]["candidate_id"] is None
    assert captured["payload"]["candidate_token"] == ""


@pytest.mark.asyncio
async def test_linkedin_init_requires_complete_confidential_client_configuration(monkeypatch):
    monkeypatch.setattr(server, "LINKEDIN_CLIENT_ID", "client-id")
    monkeypatch.setattr(server, "LINKEDIN_CLIENT_SECRET", "")

    with pytest.raises(HTTPException) as exc:
        await server.linkedin_init()

    assert exc.value.status_code == 503
