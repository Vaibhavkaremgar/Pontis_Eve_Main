"""Free-candidate credit rules for Fix My Resume."""
import asyncio
import os
from datetime import date

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://unused:unused@localhost/unused")
import server


class _Result:
    def __init__(self, scalar_value=None): self.scalar_value = scalar_value
    def scalar(self): return self.scalar_value


class _Session:
    def __init__(self, state): self.state = state
    async def __aenter__(self): return self
    async def __aexit__(self, *_args): return False
    async def execute(self, statement, params=None):
        query, params = str(statement), params or {}
        if "pg_advisory_xact_lock" in query: return _Result()
        if "SELECT COUNT(*) FROM candidate_resume_fix_credit_claims" in query:
            return _Result(sum(day == params["usage_date"] for _, day in self.state["claims"]))
        if "INSERT INTO candidate_resume_fix_credit_claims" in query:
            claim_id = f"claim-{len(self.state['claims']) + 1}"
            self.state["claims"].append((claim_id, params["usage_date"]))
            return _Result(claim_id)
        raise AssertionError(query)
    async def commit(self): pass


class _SessionFactory:
    def __init__(self, state): self.state = state
    def __call__(self): return _Session(self.state)


def _claim(monkeypatch, state, day):
    monkeypatch.setattr(server, "SessionLocal", _SessionFactory(state))
    return asyncio.run(server._claim_resume_fix_credits("candidate-1", {}, day))


def _balance(monkeypatch, state, day):
    monkeypatch.setattr(server, "SessionLocal", _SessionFactory(state))
    return asyncio.run(server._get_resume_fix_credit_balance("candidate-1", {}, day))


def test_balance_starts_at_ten_and_uses_daily_claim_records(monkeypatch):
    state = {"claims": []}
    assert _balance(monkeypatch, state, date(2026, 9, 15))["remaining_credits"] == 10
    _claim(monkeypatch, state, date(2026, 9, 15))
    assert _balance(monkeypatch, state, date(2026, 9, 15))["remaining_credits"] == 7
    assert _balance(monkeypatch, state, date(2026, 9, 16))["remaining_credits"] == 10


def test_subscriber_has_no_free_credit_balance(monkeypatch):
    result = asyncio.run(server._get_resume_fix_credit_balance(
        "candidate-1", {"subscription_active": True}, date(2026, 9, 15)
    ))
    assert result == {"remaining_credits": None, "is_subscribed": True}


def test_free_candidate_starts_each_day_with_ten_credits(monkeypatch):
    assert _claim(monkeypatch, {"claims": []}, date(2026, 9, 15))["remaining_credits"] == 7


def test_each_resume_fix_deducts_exactly_three_credits(monkeypatch):
    state = {"claims": []}
    assert _claim(monkeypatch, state, date(2026, 9, 15))["remaining_credits"] == 7
    assert _claim(monkeypatch, state, date(2026, 9, 15))["remaining_credits"] == 4


def test_three_consecutive_resume_fixes_leave_one_credit(monkeypatch):
    state = {"claims": []}
    for expected in (7, 4, 1):
        assert _claim(monkeypatch, state, date(2026, 9, 15))["remaining_credits"] == expected


@pytest.mark.parametrize("prior_uses", [3, 4])
def test_one_or_zero_credits_cannot_be_deducted(monkeypatch, prior_uses):
    state = {"claims": [(f"old-{i}", date(2026, 9, 15)) for i in range(prior_uses)]}
    with pytest.raises(server.HTTPException) as exc_info:
        _claim(monkeypatch, state, date(2026, 9, 15))
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["code"] == "resume_fix_credits_insufficient"
    assert len(state["claims"]) == prior_uses


def test_unused_credits_do_not_carry_over_to_the_next_day(monkeypatch):
    state = {"claims": []}
    _claim(monkeypatch, state, date(2026, 9, 15))  # leaves seven; do not carry it.
    assert _claim(monkeypatch, state, date(2026, 9, 16))["remaining_credits"] == 7
