from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.job_ingestion.scheduler import IST, _ats_sync_trigger, _fantastic_sync_trigger
from app.job_ingestion import scheduler


def _next_run(startup_time: datetime) -> datetime:
    return _ats_sync_trigger().get_next_fire_time(None, startup_time)


def test_ats_sync_schedule_runs_every_six_hours_in_ist():
    trigger = _ats_sync_trigger()

    assert trigger.timezone == IST
    assert trigger.fields[5].__str__() == "0,6,12,18"


def test_next_ats_sync_is_aligned_independently_of_startup_time():
    startup_times_and_expected_runs = [
        (datetime(2026, 9, 24, 0, 1, tzinfo=IST), datetime(2026, 9, 24, 6, 0, tzinfo=IST)),
        (datetime(2026, 9, 24, 17, 59, 59, tzinfo=IST), datetime(2026, 9, 24, 18, 0, tzinfo=IST)),
        (datetime(2026, 9, 24, 18, 1, tzinfo=IST), datetime(2026, 9, 25, 0, 0, tzinfo=IST)),
        (datetime(2026, 9, 24, 23, 59, 59, tzinfo=IST), datetime(2026, 9, 25, 0, 0, tzinfo=IST)),
    ]

    for startup_time, expected_run in startup_times_and_expected_runs:
        assert _next_run(startup_time) == expected_run


def test_next_ats_sync_from_utc_startup_remains_an_ist_clock_slot():
    startup_time = datetime(2026, 9, 24, 6, 31, tzinfo=ZoneInfo("UTC"))

    next_run = _next_run(startup_time)

    assert next_run.astimezone(IST) == datetime(2026, 9, 24, 18, 0, tzinfo=IST)


def test_ats_sync_slots_are_exactly_six_hours_apart_in_ist():
    trigger = _ats_sync_trigger()
    first_run = trigger.get_next_fire_time(None, datetime(2026, 9, 24, 0, 0, 0, tzinfo=IST))
    second_run = trigger.get_next_fire_time(first_run, first_run)

    assert first_run == datetime(2026, 9, 24, 0, 0, tzinfo=IST)
    assert second_run == datetime(2026, 9, 24, 6, 0, tzinfo=IST)
    assert second_run - first_run == timedelta(hours=6)


def test_fantastic_sync_schedule_matches_the_six_hour_ats_slots_in_ist():
    trigger = _fantastic_sync_trigger()
    first_run = trigger.get_next_fire_time(None, datetime(2026, 9, 24, 0, 1, tzinfo=IST))
    second_run = trigger.get_next_fire_time(first_run, first_run)

    assert trigger.timezone == IST
    assert trigger.fields[5].__str__() == "0,6,12,18"
    assert first_run == datetime(2026, 9, 24, 6, 0, tzinfo=IST)
    assert second_run == datetime(2026, 9, 24, 12, 0, tzinfo=IST)
    assert second_run - first_run == timedelta(hours=6)


def test_scheduler_initialization_is_idempotent_and_does_not_sync(monkeypatch):
    """Registering cron jobs must not execute an ingestion cycle at startup."""
    calls = []

    async def sync_jobs():
        calls.append("sync")

    scheduler.stop_scheduler()
    monkeypatch.setenv("JOB_SCHEDULER_ENABLED", "true")
    monkeypatch.setattr(scheduler, "sync_jobs", sync_jobs)

    try:
        scheduler.start_scheduler()
        first = scheduler._scheduler
        assert first is not None and first.running
        assert [job.id for job in first.get_jobs()] == ["job_sync"]
        assert first.get_job("job_sync").trigger.timezone == IST
        assert calls == []

        scheduler.start_scheduler()
        assert scheduler._scheduler is first
        assert [job.id for job in first.get_jobs()] == ["job_sync"]
        assert calls == []
    finally:
        scheduler.stop_scheduler()


def test_disabled_scheduler_has_no_instance_or_jobs(monkeypatch):
    scheduler.stop_scheduler()
    monkeypatch.setenv("JOB_SCHEDULER_ENABLED", "false")

    scheduler.start_scheduler()

    assert scheduler._scheduler is None
    assert scheduler._sync_lock is None


def test_disabled_startup_stops_an_existing_scheduler(monkeypatch):
    scheduler.stop_scheduler()
    monkeypatch.setenv("JOB_SCHEDULER_ENABLED", "true")
    scheduler.start_scheduler()
    assert scheduler._scheduler is not None and scheduler._scheduler.running

    monkeypatch.setenv("JOB_SCHEDULER_ENABLED", "false")
    scheduler.start_scheduler()

    assert scheduler._scheduler is None


def test_scheduler_flag_truthy_values_and_invalid_values(monkeypatch):
    for value in ("1", "true", "TRUE", "yes", "on"):
        monkeypatch.setenv("JOB_SCHEDULER_ENABLED", value)
        assert scheduler._scheduler_enabled() is True

    for value in ("0", "false", "", "invalid"):
        monkeypatch.setenv("JOB_SCHEDULER_ENABLED", value)
        assert scheduler._scheduler_enabled() is False

    monkeypatch.delenv("JOB_SCHEDULER_ENABLED", raising=False)
    assert scheduler._scheduler_enabled() is True


def test_disabled_guarded_sync_does_not_call_ingestion(monkeypatch):
    calls = []

    async def sync_jobs():
        calls.append("sync")

    monkeypatch.setenv("JOB_SCHEDULER_ENABLED", "false")
    monkeypatch.setattr(scheduler, "sync_jobs", sync_jobs)
    monkeypatch.setattr(scheduler, "_sync_lock", __import__("asyncio").Lock())

    import asyncio
    asyncio.run(scheduler._sync_jobs_guarded())

    assert calls == []
