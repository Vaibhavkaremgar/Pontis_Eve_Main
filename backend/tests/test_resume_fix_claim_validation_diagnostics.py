import asyncio
import logging
import os
import uuid

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://unused:unused@localhost/unused")
import server


class Result:
    def __init__(self, row=None, scalar_value=None):
        self.row = row
        self.scalar_value = scalar_value

    def first(self):
        return self.row

    def scalar(self):
        return self.scalar_value


class Session:
    def __init__(self, row, update_succeeds):
        self.row = row
        self.update_succeeds = update_succeeds

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def execute(self, statement, params=None):
        query = str(statement)
        if query.lstrip().startswith("SELECT candidate_id, consumed_at"):
            return Result(row=self.row)
        if query.lstrip().startswith("UPDATE candidate_resume_fix_credit_claims"):
            return Result(scalar_value="claim" if self.update_succeeds else None)
        raise AssertionError(query)

    async def commit(self):
        pass


def run(monkeypatch, row, update_succeeds, claim_id):
    monkeypatch.setattr(server, "SessionLocal", lambda: Session(row, update_succeeds))
    return asyncio.run(server._validate_resume_fix_credit_claim("candidate-1", {}, claim_id))


def assert_diag(caplog, **values):
    message = next(record.message for record in caplog.records if "resume_fix_claim_diagnostic" in record.message)
    for key, value in values.items():
        assert f"{key}={str(value).lower() if isinstance(value, bool) else value}" in message


def test_missing_claim_id(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="server")
    with pytest.raises(server.HTTPException):
        run(monkeypatch, None, False, None)
    assert_diag(caplog, claim_present=False, claim_format_valid=False, candidate_match=False, already_consumed=False, validation_result="failure")


def test_wrong_candidate_claim(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="server")
    claim = str(uuid.uuid4())
    with pytest.raises(server.HTTPException):
        run(monkeypatch, ("other-candidate", None), False, claim)
    assert_diag(caplog, claim_present=True, claim_format_valid=True, candidate_match=False, already_consumed=False, validation_result="failure")


def test_already_consumed_claim(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="server")
    claim = str(uuid.uuid4())
    with pytest.raises(server.HTTPException):
        run(monkeypatch, ("candidate-1", "2026-10-03T04:05:44"), False, claim)
    assert_diag(caplog, claim_present=True, claim_format_valid=True, candidate_match=True, already_consumed=True, validation_result="failure")


def test_valid_unused_claim(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="server")
    claim = str(uuid.uuid4())
    run(monkeypatch, ("candidate-1", None), True, claim)
    assert_diag(caplog, claim_present=True, claim_format_valid=True, candidate_match=True, already_consumed=False, validation_result="success")
