import asyncio
import logging
from unittest.mock import AsyncMock

import pytest

from app.job_ingestion import scheduler


GUARDED_WRAPPERS = (
    (scheduler._sync_theirstack_guarded, "sync_theirstack_jobs"),
    (scheduler._sync_fantastic_guarded, "sync_fantastic_jobs"),
    (scheduler._cleanup_fantastic_expired_jobs_guarded, "cleanup_fantastic_expired_jobs"),
    (scheduler._cleanup_theirstack_closed_jobs_guarded, "cleanup_theirstack_closed_jobs"),
)


def test_empty_provider_sync_stats_preserves_disabled_sync_contract():
    assert scheduler._empty_provider_sync_stats() == {
        "fetched": 0,
        "inserted": 0,
        "updated": 0,
        "skipped": 0,
        "failed": 0,
    }


@pytest.mark.asyncio
async def test_guarded_operation_skips_when_scheduler_is_disabled(monkeypatch):
    monkeypatch.setenv("JOB_SCHEDULER_ENABLED", "false")

    for wrapper, operation_name in GUARDED_WRAPPERS:
        operation = AsyncMock()
        monkeypatch.setattr(scheduler, operation_name, operation)

        await wrapper()

        operation.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("wrapper, operation_name", GUARDED_WRAPPERS)
async def test_guarded_operation_calls_underlying_function_and_logs_success(
    monkeypatch, caplog, wrapper, operation_name
):
    monkeypatch.setenv("JOB_SCHEDULER_ENABLED", "true")
    operation = AsyncMock()
    monkeypatch.setattr(scheduler, operation_name, operation)

    with caplog.at_level(logging.INFO, logger=scheduler.logger.name):
        await wrapper()

    operation.assert_awaited_once_with()
    messages = [record.getMessage() for record in caplog.records]
    assert any("started" in message for message in messages)
    assert any("completed" in message for message in messages)


@pytest.mark.asyncio
@pytest.mark.parametrize("wrapper, operation_name", GUARDED_WRAPPERS)
async def test_guarded_operation_logs_failure_and_reraises_original_exception(
    monkeypatch, caplog, wrapper, operation_name
):
    monkeypatch.setenv("JOB_SCHEDULER_ENABLED", "true")
    failure = RuntimeError("wrapper failure")
    operation = AsyncMock(side_effect=failure)
    monkeypatch.setattr(scheduler, operation_name, operation)

    with caplog.at_level(logging.ERROR, logger=scheduler.logger.name):
        with pytest.raises(RuntimeError) as raised:
            await wrapper()

    assert raised.value is failure
    operation.assert_awaited_once_with()
    assert any("failed" in record.getMessage() for record in caplog.records)


@pytest.mark.asyncio
async def test_sync_jobs_guarded_skips_when_lock_is_already_held(monkeypatch):
    monkeypatch.setenv("JOB_SCHEDULER_ENABLED", "true")
    lock = asyncio.Lock()
    await lock.acquire()
    monkeypatch.setattr(scheduler, "_sync_lock", lock)
    sync_jobs = AsyncMock()
    monkeypatch.setattr(scheduler, "sync_jobs", sync_jobs)

    await scheduler._sync_jobs_guarded()

    sync_jobs.assert_not_awaited()
    lock.release()


@pytest.mark.asyncio
async def test_sync_jobs_guarded_executes_when_lock_is_available(monkeypatch):
    monkeypatch.setenv("JOB_SCHEDULER_ENABLED", "true")
    lock = asyncio.Lock()
    monkeypatch.setattr(scheduler, "_sync_lock", lock)
    sync_jobs = AsyncMock()
    monkeypatch.setattr(scheduler, "sync_jobs", sync_jobs)

    await scheduler._sync_jobs_guarded()

    sync_jobs.assert_awaited_once_with()
    assert not lock.locked()


@pytest.mark.asyncio
async def test_sync_jobs_guarded_releases_lock_when_sync_fails(monkeypatch):
    monkeypatch.setenv("JOB_SCHEDULER_ENABLED", "true")
    lock = asyncio.Lock()
    monkeypatch.setattr(scheduler, "_sync_lock", lock)
    failure = RuntimeError("sync failure")
    sync_jobs = AsyncMock(side_effect=failure)
    monkeypatch.setattr(scheduler, "sync_jobs", sync_jobs)

    with pytest.raises(RuntimeError) as raised:
        await scheduler._sync_jobs_guarded()

    assert raised.value is failure
    assert not lock.locked()
