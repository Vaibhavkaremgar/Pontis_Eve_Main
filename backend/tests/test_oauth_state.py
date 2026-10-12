import json
import os
import sys

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import server  # noqa: E402


@pytest.mark.parametrize("provider", ["google", "linkedin"])
def test_oauth_state_rejects_missing_and_mismatched_values(provider):
    with pytest.raises(HTTPException) as missing:
        server._validate_oauth_state(provider, None, "stored")
    assert missing.value.status_code == 400

    with pytest.raises(HTTPException) as mismatch:
        server._validate_oauth_state(provider, "returned", "stored")
    assert mismatch.value.status_code == 400


@pytest.mark.parametrize("provider", ["google", "linkedin"])
def test_oauth_state_accepts_only_matching_values(provider):
    server._remember_oauth_state(provider, "same-state")
    assert server._validate_oauth_state(provider, "same-state", "same-state") is None


@pytest.mark.parametrize("provider", ["google", "linkedin"])
def test_oauth_state_is_single_use(provider):
    server._remember_oauth_state(provider, "one-time-state")
    server._validate_oauth_state(provider, "one-time-state", "one-time-state")
    with pytest.raises(HTTPException) as replay:
        server._validate_oauth_state(provider, "one-time-state", "one-time-state")
    assert replay.value.status_code == 400


@pytest.mark.asyncio
async def test_google_and_linkedin_init_bind_state_to_http_only_cookie(monkeypatch):
    monkeypatch.setattr(server, "GOOGLE_CLIENT_ID", "configured-google-client")
    monkeypatch.setattr(server, "LINKEDIN_CLIENT_ID", "configured-linkedin-client")

    google_response = await server.google_init()
    linkedin_response = await server.linkedin_init()

    assert json.loads(google_response.body)["state"]
    assert json.loads(linkedin_response.body)["state"]
    assert "google_oauth_state=" in google_response.headers["set-cookie"]
    assert "linkedin_oauth_state=" in linkedin_response.headers["set-cookie"]
    assert "HttpOnly" in google_response.headers["set-cookie"]
    assert "HttpOnly" in linkedin_response.headers["set-cookie"]


@pytest.mark.asyncio
async def test_linkedin_top_level_init_redirect_sets_state_cookie(monkeypatch):
    monkeypatch.setattr(server, "LINKEDIN_CLIENT_ID", "configured-linkedin-client")

    response = await server.linkedin_init(redirect=True)

    assert response.status_code == 302
    assert response.headers["location"].startswith("https://www.linkedin.com/oauth/v2/authorization?")
    assert "linkedin_oauth_state=" in response.headers["set-cookie"]
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=lax" in response.headers["set-cookie"]
