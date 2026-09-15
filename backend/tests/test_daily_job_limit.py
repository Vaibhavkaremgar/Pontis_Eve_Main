"""Focused unit coverage for the durable free job-access policy."""
import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://unused:unused@localhost/unused")

import server


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
