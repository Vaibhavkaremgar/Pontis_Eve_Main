from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.job_ingestion.scheduler import IST, _ats_sync_trigger


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
