import base64
import os
import sys
import time

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import server  # noqa: E402


def _b64_int(value):
    size = (value.bit_length() + 7) // 8
    return base64.urlsafe_b64encode(value.to_bytes(size, "big")).rstrip(b"=").decode()


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class _OidcClient:
    responses = []

    def __init__(self, *args, **kwargs):
        self._responses = iter(type(self).responses)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url):
        return _Response(next(self._responses))


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["issuer", "audience", "expired", "nonce", "signature", "missing_sub"])
async def test_linkedin_id_token_rejects_invalid_security_claims(monkeypatch, mutation, caplog):
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = private_key.public_key().public_numbers()
    jwk = {"kty": "RSA", "kid": "test-key", "use": "sig", "alg": "RS256", "n": _b64_int(public.n), "e": _b64_int(public.e)}
    claims = {
        "iss": server.LINKEDIN_EXPECTED_ISSUER,
        "sub": "linkedin-subject",
        "aud": "client-id",
        "iat": int(time.time()),
        "exp": int(time.time()) + 300,
        "nonce": "expected-nonce",
    }
    if mutation == "issuer":
        claims["iss"] = "https://attacker.example"
    elif mutation == "audience":
        claims["aud"] = "other-client"
    elif mutation == "expired":
        claims["exp"] = int(time.time()) - 60
    elif mutation == "missing_sub":
        claims.pop("sub")
    signing_key = other_key if mutation == "signature" else private_key
    token = server.pyjwt.encode(claims, signing_key, algorithm="RS256", headers={"kid": "test-key"})
    monkeypatch.setattr(server, "LINKEDIN_CLIENT_ID", "client-id")
    monkeypatch.setattr(server.httpx, "AsyncClient", _OidcClient)
    _OidcClient.responses = [
        {
            "issuer": server.LINKEDIN_EXPECTED_ISSUER,
            "jwks_uri": server.LINKEDIN_JWKS_URI,
            "id_token_signing_alg_values_supported": ["RS256"],
        },
        {"keys": [jwk]},
    ]
    expected_nonce = "wrong-nonce" if mutation == "nonce" else "expected-nonce"

    with pytest.raises(HTTPException) as exc:
        await server._validate_linkedin_id_token(token, expected_nonce)
    assert exc.value.status_code == 400
    if mutation == "missing_sub":
        assert "missing_claim=sub" in caplog.text


@pytest.mark.asyncio
async def test_linkedin_id_token_accepts_valid_verified_claims(monkeypatch):
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = private_key.public_key().public_numbers()
    jwk = {"kty": "RSA", "kid": "test-key", "use": "sig", "alg": "RS256", "n": _b64_int(public.n), "e": _b64_int(public.e)}
    claims = {
        "iss": server.LINKEDIN_EXPECTED_ISSUER,
        "sub": "linkedin-subject",
        "aud": "client-id",
        "iat": int(time.time()),
        "exp": int(time.time()) + 300,
        "nonce": "expected-nonce",
        "email": "member@example.test",
        "email_verified": True,
    }
    token = server.pyjwt.encode(claims, private_key, algorithm="RS256", headers={"kid": "test-key"})
    monkeypatch.setattr(server, "LINKEDIN_CLIENT_ID", "client-id")
    monkeypatch.setattr(server.httpx, "AsyncClient", _OidcClient)
    _OidcClient.responses = [
        {"issuer": server.LINKEDIN_EXPECTED_ISSUER, "jwks_uri": server.LINKEDIN_JWKS_URI, "id_token_signing_alg_values_supported": ["RS256"]},
        {"keys": [jwk]},
    ]
    validated = await server._validate_linkedin_id_token(token, "expected-nonce")
    assert validated["sub"] == "linkedin-subject"


@pytest.mark.asyncio
async def test_linkedin_id_token_without_nonce_is_accepted_per_provider_metadata(monkeypatch):
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = private_key.public_key().public_numbers()
    jwk = {"kty": "RSA", "kid": "test-key", "use": "sig", "alg": "RS256", "n": _b64_int(public.n), "e": _b64_int(public.e)}
    claims = {
        "iss": server.LINKEDIN_EXPECTED_ISSUER,
        "sub": "linkedin-subject",
        "aud": "client-id",
        "iat": int(time.time()),
        "exp": int(time.time()) + 300,
    }
    token = server.pyjwt.encode(claims, private_key, algorithm="RS256", headers={"kid": "test-key"})
    monkeypatch.setattr(server, "LINKEDIN_CLIENT_ID", "client-id")
    monkeypatch.setattr(server.httpx, "AsyncClient", _OidcClient)
    _OidcClient.responses = [
        {"issuer": server.LINKEDIN_EXPECTED_ISSUER, "jwks_uri": server.LINKEDIN_JWKS_URI, "id_token_signing_alg_values_supported": ["RS256"]},
        {"keys": [jwk]},
    ]
    validated = await server._validate_linkedin_id_token(token, "expected-nonce")
    assert validated["sub"] == "linkedin-subject"


@pytest.mark.asyncio
async def test_linkedin_id_token_nonce_is_checked_when_provider_returns_it(monkeypatch):
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = private_key.public_key().public_numbers()
    jwk = {"kty": "RSA", "kid": "test-key", "use": "sig", "alg": "RS256", "n": _b64_int(public.n), "e": _b64_int(public.e)}
    claims = {
        "iss": server.LINKEDIN_EXPECTED_ISSUER, "sub": "linkedin-subject", "aud": "client-id",
        "iat": int(time.time()), "exp": int(time.time()) + 300, "nonce": "provider-nonce",
    }
    token = server.pyjwt.encode(claims, private_key, algorithm="RS256", headers={"kid": "test-key"})
    monkeypatch.setattr(server, "LINKEDIN_CLIENT_ID", "client-id")
    monkeypatch.setattr(server.httpx, "AsyncClient", _OidcClient)
    _OidcClient.responses = [
        {"issuer": server.LINKEDIN_EXPECTED_ISSUER, "jwks_uri": server.LINKEDIN_JWKS_URI, "id_token_signing_alg_values_supported": ["RS256"]},
        {"keys": [jwk]},
    ]
    with pytest.raises(HTTPException):
        await server._validate_linkedin_id_token(token, "different-nonce")
