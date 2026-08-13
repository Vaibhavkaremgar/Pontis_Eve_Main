import asyncio
import logging
import sys
import os
from pathlib import Path

from sqlalchemy import text
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED

logger = logging.getLogger(__name__)

PROGRESS_INTERVAL = 25
SYNC_INTERVAL_HOURS = int(os.environ.get("JOB_SYNC_INTERVAL_HOURS", "6"))

_scheduler: AsyncIOScheduler | None = None
_sync_lock: asyncio.Lock | None = None


def _get_session_local():
    """Import SessionLocal from server to reuse the existing DB setup."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from server import SessionLocal  # noqa: PLC0415
    return SessionLocal


async def sync_jobs() -> None:
    """Fetch and upsert jobs for every active company in company_registry."""
    from app.job_ingestion.collect_jobs import JobCollector
    from app.job_ingestion.job_ingestion_service import upsert_ats_job

    SessionLocal = _get_session_local()
    collector = JobCollector()

    logger.info("[job-scheduler] sync started")

    async with SessionLocal() as db:
        result = await db.execute(
            text("""
                SELECT id, company_name, ats_type, identifier
                FROM company_registry
                WHERE is_active = TRUE
            """)
        )
        companies = result.mappings().fetchall()

    logger.info("[job-scheduler] %d active companies found", len(companies))

    for company in companies:
        company_id = company["id"]
        company_name = company["company_name"]
        ats_type = company["ats_type"]
        identifier = company["identifier"]

        logger.info("[job-scheduler] syncing company=%s ats=%s", company_name, ats_type)

        try:
            jobs = collector.collect_company_jobs(ats_type, identifier, company_name)
        except Exception as exc:
            logger.error("[job-scheduler] failed to fetch jobs for company=%s: %s", company_name, exc, exc_info=True)
            continue

        total = len(jobs)
        logger.info("[job-scheduler] fetched %d jobs for %s", total, company_name)

        inserted = 0
        skipped = 0
        failed = 0

        # Collect existing ats_job_ids for this ats_type in one query
        ats_type_val = (jobs[0].get("ats_type") or "").strip().lower() if jobs else ""
        ats_ids = [str(j.get("ats_job_id") or "") for j in jobs if j.get("ats_job_id")]
        existing_ids: set = set()
        if ats_ids and ats_type_val:
            async with SessionLocal() as db:
                placeholders = ", ".join(f":aid_{i}" for i in range(len(ats_ids)))
                params = {f"aid_{i}": v for i, v in enumerate(ats_ids)}
                params["ats_type"] = ats_type_val
                result = await db.execute(
                    text(f"""
                        SELECT ats_job_id FROM job_descriptions
                        WHERE ats_type = :ats_type AND ats_job_id IN ({placeholders})
                    """),
                    params,
                )
                existing_ids = {str(r[0]) for r in result.fetchall()}

        async with SessionLocal() as db:
            for job in jobs:
                job_ats_id = str(job.get("ats_job_id") or "")
                if job_ats_id and job_ats_id in existing_ids:
                    skipped += 1
                    continue
                try:
                    await upsert_ats_job(db, job)
                    inserted += 1
                    if inserted % PROGRESS_INTERVAL == 0:
                        logger.info("[job-scheduler] inserted %d new jobs for %s so far", inserted, company_name)
                except Exception as exc:
                    failed += 1
                    logger.error(
                        "[job-scheduler] failed job id=%s title=%r for company=%s: %s",
                        job_ats_id, job.get("title"), company_name, exc,
                    )
                    try:
                        await db.rollback()
                    except Exception:
                        pass

            synced_at_updated = False
            if failed == 0:
                try:
                    await db.execute(
                        text("""
                            UPDATE company_registry
                            SET last_synced_at = NOW()
                            WHERE id = :id
                        """),
                        {"id": company_id},
                    )
                    await db.commit()
                    synced_at_updated = True
                except Exception as exc:
                    logger.error("[job-scheduler] failed to update last_synced_at for company=%s: %s", company_name, exc)

        logger.info(
            "[job-scheduler] ATS returned %d jobs", total,
        )
        logger.info("[job-scheduler] Existing jobs skipped: %d", skipped)
        logger.info("[job-scheduler] New jobs inserted: %d", inserted)
        logger.info("[job-scheduler] Embeddings generated: %d", inserted)
        logger.info(
            "[job-scheduler] company=%s failed=%d last_synced_at_updated=%s",
            company_name, failed, synced_at_updated,
        )

    logger.info("[job-scheduler] Job sync completed")


async def _sync_jobs_guarded() -> None:
    """Wrapper that prevents overlapping runs."""
    if _sync_lock is None:
        logger.warning("[job-scheduler] sync called before scheduler was started — skipping")
        return
    if _sync_lock.locked():
        logger.info("[job-scheduler] previous sync still running — skipping this interval")
        return
    async with _sync_lock:
        logger.info("[job-scheduler] Starting ATS job sync")
        await sync_jobs()
        logger.info("[job-scheduler] Job sync completed")
        logger.info("[job-scheduler] Next job sync in %d hours", SYNC_INTERVAL_HOURS)


def _apscheduler_listener(event) -> None:
    if event.exception:
        logger.error("[job-scheduler] scheduled run raised an exception: %s", event.exception)


def start_scheduler() -> None:
    """Start the APScheduler. Safe to call from FastAPI startup."""
    global _scheduler, _sync_lock

    # Guard against uvicorn --reload spawning a second scheduler in the same process
    if os.environ.get("_JOB_SCHEDULER_STARTED") == str(os.getpid()):
        logger.info("[job-scheduler] scheduler already started in this process — skipping")
        return
    os.environ["_JOB_SCHEDULER_STARTED"] = str(os.getpid())

    logger.info("[job-scheduler] Starting scheduler")

    _sync_lock = asyncio.Lock()

    _scheduler = AsyncIOScheduler()
    _scheduler.add_listener(_apscheduler_listener, EVENT_JOB_ERROR | EVENT_JOB_EXECUTED)
    _scheduler.add_job(
        _sync_jobs_guarded,
        trigger="interval",
        hours=SYNC_INTERVAL_HOURS,
        id="job_sync",
        replace_existing=True,
    )
    _scheduler.start()
    logger.info("[job-scheduler] Next job sync scheduled in %d hours", SYNC_INTERVAL_HOURS)


def stop_scheduler() -> None:
    """Shut down the APScheduler gracefully."""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("[job-scheduler] scheduler shut down")
    _scheduler = None


def run_sync() -> None:
    """Manual entry point: python -m app.job_ingestion.scheduler"""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    except ImportError:
        pass

    asyncio.run(sync_jobs())


if __name__ == "__main__":
    run_sync()
