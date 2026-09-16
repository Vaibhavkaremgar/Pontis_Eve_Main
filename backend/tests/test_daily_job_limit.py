"""Regression coverage for calendar-date based free job access."""
import asyncio
import os
from datetime import date

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://unused:unused@localhost/unused")
import server


class _Result:
    def __init__(self, rows=(), scalar_value=None):
        self._rows, self._scalar_value = list(rows), scalar_value

    def scalar(self): return self._scalar_value
    def fetchall(self): return self._rows


class _DailyAccessSession:
    def __init__(self, state): self.state = state
    async def __aenter__(self): return self
    async def __aexit__(self, exc_type, exc, tb): return False

    async def execute(self, statement, params=None):
        query, params = str(statement), params or {}
        if "pg_advisory_xact_lock" in query:
            return _Result()
        if "SELECT COUNT(*) FROM candidate_daily_job_access" in query:
            used = [entry for entry in self.state["access"]
                    if entry[0] == params["cid"] and entry[2] == params["access_date"]]
            return _Result(scalar_value=len(used))
        if "SELECT cjr.id" in query:
            # All historic access is excluded: tomorrow receives new recommendations.
            claimed = {rec_id for _, rec_id, _ in self.state["access"]}
            visible = [rec_id for rec_id, hidden in self.state["recommendations"] if not hidden and rec_id not in claimed]
            return _Result([(rec_id,) for rec_id in visible[:params["slots"]]])
        if "INSERT INTO candidate_daily_job_access" in query:
            self.state["access"].add((params["cid"], params["rid"], params["access_date"]))
            return _Result()
        raise AssertionError(f"Unexpected query: {query}")

    async def commit(self): pass


class _DailyAccessSessionFactory:
    def __init__(self, state): self.state = state
    def __call__(self): return _DailyAccessSession(self.state)


def _state():
    return {"recommendations": [(f"rec-{i}", False) for i in range(13)], "access": set()}


def _claim(monkeypatch, state, day, request_more=False, candidate=None):
    monkeypatch.setattr(server, "_product_current_date", lambda: day)
    monkeypatch.setattr(server, "SessionLocal", _DailyAccessSessionFactory(state))
    return asyncio.run(server._claim_daily_job_access("candidate", candidate or {}, request_more))


def _day_accesses(state, day):
    return {rec_id for _, rec_id, access_day in state["access"] if access_day == day}


def test_september_15_grants_exactly_three_jobs(monkeypatch):
    state, day = _state(), date(2026, 9, 15)
    assert _claim(monkeypatch, state, day)
    assert _day_accesses(state, day) == {"rec-0", "rec-1", "rec-2"}


def test_fourth_job_on_september_15_returns_existing_limit_response(monkeypatch):
    state, day = _state(), date(2026, 9, 15)
    _claim(monkeypatch, state, day)
    with pytest.raises(server.HTTPException) as exc_info:
        _claim(monkeypatch, state, day, request_more=True)
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == {"code": "daily_job_limit_reached", "message": "Unlock more jobs by subscribing."}


def test_repeated_requests_on_same_date_do_not_reset_usage(monkeypatch):
    state, day = _state(), date(2026, 9, 15)
    _claim(monkeypatch, state, day)
    _claim(monkeypatch, state, day)
    assert len(state["access"]) == 3


def test_next_calendar_date_gets_next_three_previously_unaccessed_jobs(monkeypatch):
    state = _state()
    _claim(monkeypatch, state, date(2026, 9, 15))
    _claim(monkeypatch, state, date(2026, 9, 16))
    assert _day_accesses(state, date(2026, 9, 16)) == {"rec-3", "rec-4", "rec-5"}


def test_calendar_boundary_not_rolling_24_hour_window(monkeypatch):
    state = _state()
    # Adjacent calendar dates work independently; accessed_at is never consulted.
    _claim(monkeypatch, state, date(2026, 9, 15))
    _claim(monkeypatch, state, date(2026, 9, 16))
    assert "accessed_at" not in str(server._claim_daily_job_access.__code__.co_consts)
    assert _day_accesses(state, date(2026, 9, 16)) == {"rec-3", "rec-4", "rec-5"}


def test_subscriber_bypasses_limit_and_does_not_consume_free_access(monkeypatch):
    state = _state()
    assert not _claim(monkeypatch, state, date(2026, 9, 15), True, {"subscription_active": True})
    assert state["access"] == set()


def test_hidden_recommendations_are_never_claimed(monkeypatch):
    state = _state()
    state["recommendations"][0] = ("rec-0", True)
    _claim(monkeypatch, state, date(2026, 9, 15))
    assert _day_accesses(state, date(2026, 9, 15)) == {"rec-1", "rec-2", "rec-3"}


def test_schema_uses_product_calendar_date():
    assert "access_date" in server.CREATE_DAILY_JOB_ACCESS_TABLE
    assert "CURRENT_DATE" not in str(server._claim_daily_job_access.__code__.co_consts)
    assert server.PRODUCT_TIMEZONE == "Asia/Kolkata"
