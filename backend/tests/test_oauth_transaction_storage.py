import asyncio
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import server  # noqa: E402


class _Mappings:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class _Result:
    def __init__(self, row=None):
        self.row = row

    def mappings(self):
        return _Mappings(self.row)

    def fetchone(self):
        return self.row


class _SharedOAuthDatabase:
    """Transaction-aware stand-in for focused tests; production uses PostgreSQL."""

    def __init__(self):
        self.rows = {}
        self.lock = asyncio.Lock()

    def session(self):
        database = self

        class Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def commit(self):
                return None

            async def execute(self, statement, params=None):
                query = " ".join(str(statement).split())
                params = params or {}
                async with database.lock:
                    now = datetime.now(timezone.utc)
                    if query.startswith("INSERT INTO oauth_transactions"):
                        database.rows[params["id"]] = {
                            "id": params["id"],
                            "provider": params["provider"],
                            "state_hash": params["state_hash"],
                            "pkce_verifier": params["pkce_verifier"],
                            "state_expires_at": now + timedelta(seconds=params["ttl_seconds"]),
                            "state_consumed_at": None,
                            "exchange_code_hash": None,
                            "exchange_payload": None,
                            "exchange_expires_at": None,
                            "exchange_consumed_at": None,
                        }
                        return _Result()
                    if "SET state_consumed_at" in query:
                        row = next((row for row in database.rows.values()
                            if row["provider"] == params["provider"]
                            and row["state_hash"] == params["state_hash"]), None)
                        if (not row or row["state_consumed_at"] is not None
                                or row["state_expires_at"] <= now):
                            return _Result()
                        row["state_consumed_at"] = now
                        return _Result({"id": row["id"], "pkce_verifier": row["pkce_verifier"]})
                    if query.startswith("SELECT state_expires_at <= now() AS expired"):
                        row = next((row for row in database.rows.values()
                            if row["provider"] == params["provider"]
                            and row["state_hash"] == params["state_hash"]), None)
                        if not row:
                            return _Result()
                        return _Result({
                            "expired": row["state_expires_at"] <= now,
                            "already_used": row["state_consumed_at"] is not None,
                        })
                    if "SET exchange_code_hash" in query:
                        row = database.rows.get(params["transaction_id"])
                        if not row or row["state_consumed_at"] is None or row["exchange_code_hash"] is not None:
                            return _Result()
                        row["exchange_code_hash"] = params["code_hash"]
                        row["exchange_payload"] = json.loads(params["payload"])
                        row["exchange_expires_at"] = now + timedelta(seconds=params["ttl_seconds"])
                        return _Result((row["id"],))
                    if "SET exchange_consumed_at" in query:
                        row = next((row for row in database.rows.values()
                            if row["exchange_code_hash"] == params["code_hash"]), None)
                        if (not row or row["exchange_consumed_at"] is not None
                                or row["exchange_expires_at"] <= now):
                            return _Result()
                        row["exchange_consumed_at"] = now
                        return _Result({"exchange_payload": row["exchange_payload"]})
                    if query.startswith("DELETE FROM oauth_transactions"):
                        return _Result()
                    raise AssertionError(f"Unexpected SQL: {query}")

        return Session()


@pytest.fixture
def oauth_database(monkeypatch):
    database = _SharedOAuthDatabase()
    monkeypatch.setattr(server, "SessionLocal", database.session)
    monkeypatch.setattr(server, "_schedule_oauth_cleanup", lambda: None)
    return database


@pytest.mark.asyncio
async def test_state_survives_process_boundary_and_is_single_use(oauth_database):
    await server._create_oauth_transaction("linkedin", "state-a", "verifier-a")

    transaction_id, verifier = await server._consume_oauth_transaction("linkedin", "state-a", "state-a")
    assert transaction_id
    assert verifier == "verifier-a"

    with pytest.raises(HTTPException) as replay:
        await server._consume_oauth_transaction("linkedin", "state-a", "state-a")
    assert replay.value.status_code == 400
    assert all(row["state_hash"] != "state-a" for row in oauth_database.rows.values())


@pytest.mark.asyncio
async def test_expired_state_is_rejected(oauth_database):
    transaction_id = await server._create_oauth_transaction("linkedin", "expired-state", "verifier")
    oauth_database.rows[transaction_id]["state_expires_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)

    with pytest.raises(HTTPException) as expired:
        await server._consume_oauth_transaction("linkedin", "expired-state", "expired-state")
    assert expired.value.status_code == 400


@pytest.mark.asyncio
async def test_pkce_verifier_is_bound_to_exact_transaction(oauth_database):
    await server._create_oauth_transaction("linkedin", "state-a", "verifier-a")
    await server._create_oauth_transaction("linkedin", "state-b", "verifier-b")

    _, verifier = await server._consume_oauth_transaction("linkedin", "state-b", "state-b")
    assert verifier == "verifier-b"


@pytest.mark.asyncio
async def test_missing_pkce_verifier_fails_safely(oauth_database):
    transaction_id = await server._create_oauth_transaction("linkedin", "state-a", "verifier")
    oauth_database.rows[transaction_id]["pkce_verifier"] = ""

    with pytest.raises(HTTPException) as missing:
        await server._consume_oauth_transaction("linkedin", "state-a", "state-a")
    assert missing.value.status_code == 400


@pytest.mark.asyncio
async def test_exchange_survives_process_boundary_expires_and_is_single_use(oauth_database):
    transaction_id = await server._create_oauth_transaction("linkedin", "state-a", "verifier")
    await server._consume_oauth_transaction("linkedin", "state-a", "state-a")
    code = await server._create_oauth_exchange(transaction_id, {"candidate_id": "candidate-a"})

    assert await server._consume_oauth_exchange(code) == {"candidate_id": "candidate-a"}
    with pytest.raises(HTTPException) as replay:
        await server._consume_oauth_exchange(code)
    assert replay.value.status_code == 400

    transaction_id = await server._create_oauth_transaction("linkedin", "state-b", "verifier")
    await server._consume_oauth_transaction("linkedin", "state-b", "state-b")
    expired_code = await server._create_oauth_exchange(transaction_id, {"candidate_id": "candidate-b"})
    oauth_database.rows[transaction_id]["exchange_expires_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)
    with pytest.raises(HTTPException):
        await server._consume_oauth_exchange(expired_code)


@pytest.mark.asyncio
async def test_concurrent_state_consumption_has_exactly_one_winner(oauth_database):
    await server._create_oauth_transaction("linkedin", "state-a", "verifier")

    results = await asyncio.gather(*[
        server._consume_oauth_transaction("linkedin", "state-a", "state-a") for _ in range(8)
    ], return_exceptions=True)

    assert sum(not isinstance(result, Exception) for result in results) == 1
    assert sum(isinstance(result, HTTPException) for result in results) == 7


@pytest.mark.asyncio
async def test_concurrent_exchange_consumption_has_exactly_one_winner(oauth_database):
    transaction_id = await server._create_oauth_transaction("linkedin", "state-a", "verifier")
    await server._consume_oauth_transaction("linkedin", "state-a", "state-a")
    code = await server._create_oauth_exchange(transaction_id, {"candidate_id": "candidate-a"})

    results = await asyncio.gather(*[
        server._consume_oauth_exchange(code) for _ in range(8)
    ], return_exceptions=True)

    assert sum(not isinstance(result, Exception) for result in results) == 1
    assert sum(isinstance(result, HTTPException) for result in results) == 7
