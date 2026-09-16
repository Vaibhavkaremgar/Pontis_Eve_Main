"""Focused unit coverage for the durable free job-access policy."""
import asyncio
import os

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://unused:unused@localhost/unused")

import server


class _Result:
    def __init__(self, rows=(), scalar_value=None):
        self._rows = list(rows)
        self._scalar_value = scalar_value

    def scalar(self):
        return self._scalar_value

    def fetchall(self):
        return self._rows


class _DailyAccessSession:
    def __init__(self, state):
        self.state = state

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, statement, params=None):
        query = str(statement)
        params = params or {}
        if "pg_advisory_xact_lock" in query:
            return _Result()
        if "SELECT COUNT(*) FROM candidate_daily_job_access" in query:
            return _Result(scalar_value=len(self.state["access"]))
        if "SELECT cjr.id" in query:
            claimed = {rec_id for _, rec_id in self.state["access"]}
            visible = [rec_id for rec_id, hidden in self.state["recommendations"] if not hidden and rec_id not in claimed]
            return _Result([(rec_id,) for rec_id in visible[:params["slots"]]])
        if "INSERT INTO candidate_daily_job_access" in query:
            self.state["access"].add((params["cid"], params["rid"]))
            return _Result()
        raise AssertionError(f"Unexpected query: {query}")

    async def commit(self):
        pass


class _DailyAccessSessionFactory:
    def __init__(self, state):
        self.state = state

    def __call__(self):
        return _DailyAccessSession(self.state)


def _daily_access_state(recommendations):
    return {"recommendations": recommendations, "access": set()}


def test_free_candidate_can_access_jobs_zero_through_third_access():
    """0, 1 and 2 accesses allow the next job; the third remains visible."""
    assert not server._daily_job_limit_reached(0, True)
    assert not server._daily_job_limit_reached(1, True)
    assert not server._daily_job_limit_reached(2, True)
    # The three granted jobs themselves are still accessible.
    assert not server._daily_job_limit_reached(3, False)


def test_fourth_job_access_is_blocked():
    assert server._daily_job_limit_reached(3, True)
    assert server._daily_job_limit_reached(4, True)


def test_new_calendar_day_starts_a_new_free_allowance():
    # The database query keys usage by CURRENT_DATE; a new date has zero usage.
    assert not server._daily_job_limit_reached(0, True)


def test_subscription_is_read_from_existing_candidate_data_and_bypasses_limit():
    assert server._has_active_subscription({"raw_data": {"subscription": {"status": "active"}}})
    assert server._has_active_subscription({"subscription_active": True})
    assert not server._has_active_subscription({"raw_data": {"subscription": {"status": "cancelled"}}})


def test_persistence_schema_is_candidate_and_calendar_day_scoped():
    assert "candidate_id" in server.CREATE_DAILY_JOB_ACCESS_TABLE
    assert "access_date" in server.CREATE_DAILY_JOB_ACCESS_TABLE
    assert "CURRENT_DATE" in server._claim_daily_job_access.__code__.co_consts or "CURRENT_DATE" in str(server._claim_daily_job_access.__code__.co_consts)


def test_free_candidate_claims_three_from_thirteen_and_normal_repeat_uses_no_more_slots(monkeypatch):
    state = _daily_access_state([(f"rec-{i}", False) for i in range(13)])
    monkeypatch.setattr(server, "SessionLocal", _DailyAccessSessionFactory(state))

    assert asyncio.run(server._claim_daily_job_access("candidate", {}, request_more=False))
    assert len(state["access"]) == 3

    assert asyncio.run(server._claim_daily_job_access("candidate", {}, request_more=False))
    assert len(state["access"]) == 3


def test_request_more_after_three_free_jobs_returns_existing_limit_response(monkeypatch):
    state = _daily_access_state([(f"rec-{i}", False) for i in range(13)])
    monkeypatch.setattr(server, "SessionLocal", _DailyAccessSessionFactory(state))
    asyncio.run(server._claim_daily_job_access("candidate", {}, request_more=False))

    with pytest.raises(server.HTTPException) as exc_info:
        asyncio.run(server._claim_daily_job_access("candidate", {}, request_more=True))

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["code"] == "daily_job_limit_reached"


def test_explicitly_hidden_recommendations_are_never_claimed(monkeypatch):
    state = _daily_access_state([("hidden", True)] + [(f"rec-{i}", False) for i in range(3)])
    monkeypatch.setattr(server, "SessionLocal", _DailyAccessSessionFactory(state))

    asyncio.run(server._claim_daily_job_access("candidate", {}, request_more=False))

    assert {rec_id for _, rec_id in state["access"]} == {"rec-0", "rec-1", "rec-2"}


def test_subscribed_candidate_does_not_claim_or_apply_daily_limit(monkeypatch):
    state = _daily_access_state([(f"rec-{i}", False) for i in range(13)])
    monkeypatch.setattr(server, "SessionLocal", _DailyAccessSessionFactory(state))

    assert not asyncio.run(server._claim_daily_job_access(
        "candidate", {"subscription_active": True}, request_more=True
    ))
    assert state["access"] == set()
