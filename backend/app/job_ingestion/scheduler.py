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


async def reconcile_missing_ats_jobs(db, *, company_registry_id, ats_type: str, current_ats_ids: set[str]) -> list[str]:
    """Close jobs absent from a *complete successful* current ATS board.

    The registry foreign key scopes this to one configured board, avoiding
    accidental cross-company deactivation where display names collide.
    """
    if ats_type not in {"ashby", "lever"}:
        return []
    stored = await db.execute(text("""
        SELECT id, ats_job_id
        FROM job_descriptions
        WHERE company_registry_id = :company_registry_id
          AND LOWER(ats_type) = :ats_type
          AND is_active IS TRUE
    """), {"company_registry_id": company_registry_id, "ats_type": ats_type})
    missing = [str(row[0]) for row in stored.fetchall() if str(row[1] or "").strip() not in current_ats_ids]
    if not missing:
        return []
    await db.execute(text("""
        UPDATE job_descriptions
        SET is_active = FALSE, status = 'closed', job_status = 'closed', updated_at = NOW()
        WHERE id = ANY(CAST(:job_ids AS uuid[]))
    """), {"job_ids": missing})
    # Preserve recommendation/application history while removing it from all
    # candidate-visible paths.
    await db.execute(text("""
        UPDATE candidate_job_recommendations
        SET hidden_at = COALESCE(hidden_at, NOW())
        WHERE job_id = ANY(CAST(:job_ids AS uuid[]))
    """), {"job_ids": missing})
    return missing


def _complete_board_payload(jobs) -> tuple[bool, set[str]]:
    """Reject malformed/incomplete payloads; an empty valid list means no jobs."""
    if not isinstance(jobs, list):
        return False, set()
    ids = set()
    for job in jobs:
        job_id = str(job.get("ats_job_id") or "").strip() if isinstance(job, dict) else ""
        if not job_id:
            return False, set()
        ids.add(job_id)
    return True, ids


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

        complete, current_ats_ids = _complete_board_payload(jobs)
        if not complete:
            logger.error("[job-scheduler] incomplete ATS payload for company=%s; reconciliation skipped", company_name)
            continue

        total = len(jobs)
        logger.info("[job-scheduler] fetched %d jobs for %s", total, company_name)

        inserted = 0
        skipped = 0
        failed = 0

        async with SessionLocal() as db:
            for job in jobs:
                job_ats_id = str(job.get("ats_job_id") or "")
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
                    missing_ids = await reconcile_missing_ats_jobs(
                        db, company_registry_id=company_id, ats_type=str(ats_type).lower(),
                        current_ats_ids=current_ats_ids,
                    )
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
                    for job_id in missing_ids:
                        try:
                            from app.job_ingestion.qdrant_service import delete_job_embedding
                            delete_job_embedding(job_id)
                        except Exception as exc:
                            # DB eligibility and hidden recommendations remain the safety net.
                            logger.warning("[job-scheduler] Qdrant delete failed for job_id=%s: %s", job_id, exc)
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
