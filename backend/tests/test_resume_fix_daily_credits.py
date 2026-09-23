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
        if "INSERT INTO candidate_resume_fix_credit_balances" in query:
            self.state.setdefault("starter_remaining", params["starter_credits"])
            return _Result()
        if "SELECT starter_credits_remaining" in query:
            return _Result(self.state.get("starter_remaining", 100))
        if "UPDATE candidate_resume_fix_credit_balances" in query:
            if self.state["starter_remaining"] < params["cost"]:
                return _Result()
            self.state["starter_remaining"] -= params["cost"]
            return _Result(self.state["starter_remaining"])
        if "SELECT COUNT(*) FROM candidate_resume_fix_credit_claims" in query:
            return _Result(sum(day == params["usage_date"] and source == "daily" for _, day, source in self.state["claims"]))
        if "INSERT INTO candidate_resume_fix_credit_claims" in query:
            claim_id = f"claim-{len(self.state['claims']) + 1}"
            source = "starter" if "'starter'" in query else "daily"
            self.state["claims"].append((claim_id, params["usage_date"], source))
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


def test_new_candidate_starts_with_one_hundred_starter_credits(monkeypatch):
    state = {"claims": []}
    balance = _balance(monkeypatch, state, date(2026, 9, 15))
    assert balance["remaining_credits"] == 100
    assert balance["credit_phase"] == "starter"


def test_subscriber_has_no_free_credit_balance(monkeypatch):
    result = asyncio.run(server._get_resume_fix_credit_balance(
        "candidate-1", {"subscription_active": True}, date(2026, 9, 15)
    ))
    assert result == {"remaining_credits": None, "is_subscribed": True}


def test_direct_resume_save_without_a_claim_is_blocked():
    """The profile-update endpoint cannot be used to bypass the credit claim."""
    with pytest.raises(server.HTTPException) as exc_info:
        asyncio.run(server._validate_resume_fix_credit_claim("candidate-1", {}, None))
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["code"] == "resume_fix_credits_insufficient"


def test_subscriber_bypasses_resume_fix_claim_validation():
    asyncio.run(server._validate_resume_fix_credit_claim(
        "candidate-1", {"subscription_active": True}, None
    ))


def test_starter_credits_deduct_exactly_three_per_resume_fix(monkeypatch):
    state = {"claims": []}
    assert _claim(monkeypatch, state, date(2026, 9, 15))["remaining_credits"] == 97
    assert _claim(monkeypatch, state, date(2026, 9, 15))["remaining_credits"] == 94


def test_daily_allowance_begins_after_starter_pool_is_no_longer_usable(monkeypatch):
    state = {"claims": [], "starter_remaining": 1}
    balance = _balance(monkeypatch, state, date(2026, 9, 15))
    assert balance == {"remaining_credits": 10, "credit_phase": "daily", "is_subscribed": False}
    claim = _claim(monkeypatch, state, date(2026, 9, 15))
    assert claim["remaining_credits"] == 7
    assert claim["credit_phase"] == "daily"


def test_three_daily_resume_fixes_leave_one_credit(monkeypatch):
    state = {"claims": [], "starter_remaining": 0}
    for expected in (7, 4, 1):
        assert _claim(monkeypatch, state, date(2026, 9, 15))["remaining_credits"] == expected


@pytest.mark.parametrize("prior_uses", [3, 4])
def test_one_or_zero_credits_cannot_be_deducted(monkeypatch, prior_uses):
    state = {"starter_remaining": 0, "claims": [(f"old-{i}", date(2026, 9, 15), "daily") for i in range(prior_uses)]}
    with pytest.raises(server.HTTPException) as exc_info:
        _claim(monkeypatch, state, date(2026, 9, 15))
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["code"] == "resume_fix_credits_insufficient"
    assert len(state["claims"]) == prior_uses


def test_unused_credits_do_not_carry_over_to_the_next_day(monkeypatch):
    state = {"starter_remaining": 0, "claims": []}
    _claim(monkeypatch, state, date(2026, 9, 15))  # leaves seven; do not carry it.
    assert _claim(monkeypatch, state, date(2026, 9, 16))["remaining_credits"] == 7
