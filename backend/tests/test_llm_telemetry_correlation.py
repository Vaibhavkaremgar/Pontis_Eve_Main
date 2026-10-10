"""Focused contract tests for opaque LLM request-to-candidate correlation."""
from types import SimpleNamespace

import pytest


class _Connection:
    def __init__(self, stored_candidate_id):
        self.stored_candidate_id = stored_candidate_id
        self.calls = []

    async def execute(self, statement, params=None):
        self.calls.append((str(statement), params))
        if "SELECT candidate_id" in str(statement):
            return SimpleNamespace(fetchone=lambda: (self.stored_candidate_id,))
        return SimpleNamespace()


@pytest.mark.asyncio
async def test_association_updates_existing_events_only_through_matching_stored_link():
    import llm_telemetry

    conn = _Connection("candidate-1")
    await llm_telemetry._associate_candidate_on_connection(conn, "request-1", "candidate-1")

    assert len(conn.calls) == 3
    update_sql, update_params = conn.calls[-1]
    assert "FROM llm_usage_request_candidate_links" in update_sql
    assert "l.candidate_id = :candidate_id" in update_sql
    assert update_params == {"request_id": "request-1", "candidate_id": "candidate-1"}


@pytest.mark.asyncio
async def test_conflicting_association_never_updates_usage_events(caplog):
    import llm_telemetry

    conn = _Connection("candidate-already-linked")
    await llm_telemetry._associate_candidate_on_connection(conn, "request-1", "candidate-new")

    assert len(conn.calls) == 2
    assert "conflict" in caplog.text.lower()


@pytest.mark.asyncio
async def test_transaction_association_failure_does_not_break_onboarding(caplog):
    import llm_telemetry

    class BrokenSavepoint:
        async def __aenter__(self):
            raise RuntimeError("telemetry unavailable")

        async def __aexit__(self, *_args):
            return False

    class BrokenSession:
        def begin_nested(self):
            return BrokenSavepoint()

    await llm_telemetry.associate_candidate_in_transaction(BrokenSession(), "request-1", "candidate-1")
    assert "association failed" in caplog.text.lower()


def test_historical_migration_is_immutable_and_upgrade_is_additive():
    from pathlib import Path

    migrations = Path(__file__).parents[1] / "migrations"
    original = (migrations / "20261008_llm_usage_events.sql").read_text(encoding="utf-8")
    upgrade = (migrations / "20261010_llm_usage_event_correlation.sql").read_text(encoding="utf-8")

    assert "session_id" not in original
    assert "llm_usage_request_candidate_links" not in original
    assert "ALTER TABLE llm_usage_events ADD COLUMN IF NOT EXISTS session_id" in upgrade
    assert "CREATE TABLE IF NOT EXISTS llm_usage_request_candidate_links" in upgrade
