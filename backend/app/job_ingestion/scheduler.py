import asyncio
import logging
import sys
import os
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import text
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)

PROGRESS_INTERVAL = 25
MAX_LOGGED_JOB_FAILURES = 3
IST = ZoneInfo("Asia/Kolkata")
# Run at 00:00, 06:00, 12:00, and 18:00 IST.
SYNC_HOURS_IST = "0,6,12,18"

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
    from app.job_ingestion.job_skill_extraction import extract_missing_job_skills
    from app.job_ingestion.job_skill_extraction import extract_missing_job_skills

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
        logged_failures = 0

        async with SessionLocal() as db:
            for job in jobs:
                job_ats_id = str(job.get("ats_job_id") or "")
                try:
                    job = await extract_missing_job_skills(job)
                    await upsert_ats_job(db, job)
                    inserted += 1
                    if inserted % PROGRESS_INTERVAL == 0:
                        logger.info("[job-scheduler] inserted %d new jobs for %s so far", inserted, company_name)
                except Exception as exc:
                    failed += 1
                    # A provider-wide schema failure can affect every job on
                    # a board.  Keep enough examples for diagnosis without
                    # exhausting Railway's log-rate allowance.
                    if logged_failures < MAX_LOGGED_JOB_FAILURES:
                        logger.error(
                            "[job-scheduler] failed job id=%s title=%r for company=%s: %s",
                            job_ats_id, job.get("title"), company_name, exc,
                        )
                        logged_failures += 1
                    elif logged_failures == MAX_LOGGED_JOB_FAILURES:
                        logger.error(
                            "[job-scheduler] additional per-job failures for company=%s are suppressed",
                            company_name,
                        )
                        logged_failures += 1
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


async def sync_fantastic_jobs() -> dict:
    """Sync both bounded Fantastic active feeds through the shared persistence path."""
    from app.job_ingestion.connectors.fantastic import FantasticClient
    from app.job_ingestion.job_ingestion_service import upsert_ats_job
    from app.job_ingestion.job_skill_extraction import extract_missing_job_skills
    if os.getenv("FANTASTIC_ENABLED", "false").lower() not in {"1", "true", "yes", "on"}:
        return {"fetched": 0, "inserted": 0, "updated": 0, "skipped": 0, "failed": 0}
    logger.info("[fantastic] starting sync")
    client = FantasticClient()
    feeds = (("active-ats", client.fetch_active_ats), ("active-jb", client.fetch_active_job_boards))
    from app.job_ingestion.normalize import normalize_fantastic
    from location_matching import country_code
    per_feed = {}
    accepted = []
    seen_ids, seen_urls, seen_fingerprints = set(), set(), set()
    for feed_name, fetch in feeds:
        feed_stats = {"fetched": 0, "accepted_india": 0, "rejected_country": 0,
                      "rejected_expired": 0, "duplicates": 0, "pages_requested": 0, "requests_made": 0}
        try:
            raw_jobs = await fetch()
            feed_stats.update({key: value for key, value in getattr(client, "last_fetch_stats", {}).items()
                               if key in {"fetched", "pages_requested", "requests_made"}})
        except Exception as exc:
            logger.error("[fantastic] %s fetch failed: %s", feed_name, exc)
            feed_stats["failed"] = 1
            per_feed[feed_name] = feed_stats
            continue
        for raw in raw_jobs:
            job = normalize_fantastic(raw)
            job.setdefault("structured_data", {})["fantastic_feed"] = feed_name
            if country_code(job.get("country")) != "IN":
                feed_stats["rejected_country"] += 1
                continue
            valid_through = job.get("valid_through")
            if valid_through is not None and valid_through < datetime.now(valid_through.tzinfo or IST):
                feed_stats["rejected_expired"] += 1
                continue
            identity = (job.get("ats_job_id") or "").strip()
            url = (job.get("job_url") or "").strip().casefold()
            fingerprint = tuple(str(job.get(key) or "").strip().casefold()
                                for key in ("company_name", "title", "city", "state", "country"))
            duplicate = ((identity and identity in seen_ids) or (url and url in seen_urls)
                         or (all(fingerprint) and fingerprint in seen_fingerprints))
            if duplicate:
                feed_stats["duplicates"] += 1
                continue
            if identity: seen_ids.add(identity)
            if url: seen_urls.add(url)
            if all(fingerprint): seen_fingerprints.add(fingerprint)
            feed_stats["accepted_india"] += 1
            accepted.append((feed_name, job))
        per_feed[feed_name] = feed_stats

    stats = {"fetched": sum(s["fetched"] for s in per_feed.values()),
             "accepted": sum(s["accepted_india"] for s in per_feed.values()),
             "rejected": sum(s["rejected_country"] for s in per_feed.values()),
             "duplicates": sum(s["duplicates"] for s in per_feed.values()),
             "inserted": 0, "updated": 0, "skipped": 0,
             "failed": sum(s.get("failed", 0) for s in per_feed.values()),
             "pages_requested": sum(s["pages_requested"] for s in per_feed.values()),
             "requests_made": sum(s["requests_made"] for s in per_feed.values())}
    SessionLocal = _get_session_local()
    async with SessionLocal() as db:
        for feed_name, job in accepted:
            if not job["ats_job_id"] or not job["title"] or not job["job_url"]:
                stats["skipped"] += 1; continue
            try:
                existing = await db.execute(text("""
                    SELECT id, ats_job_id FROM job_descriptions
                    WHERE ats_type='fantastic' AND (ats_job_id=:ats_job_id OR job_url=:job_url)
                    ORDER BY CASE WHEN ats_job_id=:ats_job_id THEN 0 ELSE 1 END LIMIT 1
                """), {"ats_job_id": job["ats_job_id"], "job_url": job["job_url"]})
                existing_row = existing.first()
                if existing_row and str(existing_row[1] or "") != str(job["ats_job_id"]):
                    stats["duplicates"] += 1
                    per_feed[feed_name]["duplicates"] += 1
                    continue
                job = await extract_missing_job_skills(job)
                await upsert_ats_job(db, job)
                stats["updated" if existing_row else "inserted"] += 1
            except Exception as exc:
                stats["failed"] += 1
                _log_fantastic_upsert_error(exc, job)
                await db.rollback()
    for feed_name, feed_stats in per_feed.items():
        logger.info("[fantastic] feed=%s stats=%s", feed_name, feed_stats)
    logger.info("[fantastic] sync completed stats=%s", stats)
    stats["feeds"] = per_feed
    return stats

async def sync_theirstack_jobs() -> dict[str, int]:
    from app.job_ingestion.connectors.theirstack import TheirStackClient
    from app.job_ingestion.job_ingestion_service import upsert_ats_job
    from app.job_ingestion.normalize import normalize_theirstack
    if os.getenv("THEIRSTACK_ENABLED", "false").lower() not in {"1", "true", "yes", "on"}:
        return {"fetched": 0, "inserted": 0, "updated": 0, "skipped": 0, "failed": 0}
    try: raw_jobs = await TheirStackClient().fetch_jobs()
    except Exception as exc:
        logger.error("[theirstack] sync fetch failed: %s", exc); return {"fetched": 0, "inserted": 0, "updated": 0, "skipped": 0, "failed": 1}
    stats = {"fetched": len(raw_jobs), "inserted": 0, "updated": 0, "skipped": 0, "failed": 0}
    SessionLocal = _get_session_local()
    async with SessionLocal() as db:
        for raw in raw_jobs:
            job = normalize_theirstack(raw)
            if not job["ats_job_id"] or not job["title"] or not job["job_url"] or str(job.get("country") or "").casefold() not in {"india", "in"}:
                stats["skipped"] += 1; continue
            try:
                existing = await db.execute(text("SELECT ats_type FROM job_descriptions WHERE (ats_type='theirstack' AND ats_job_id=:id) OR job_url=:url LIMIT 1"), {"id": job["ats_job_id"], "url": job["job_url"]})
                row = existing.first()
                if row and row[0] != "theirstack":
                    stats["skipped"] += 1; continue
                job = await extract_missing_job_skills(job)
                await upsert_ats_job(db, job); stats["updated" if row else "inserted"] += 1
            except Exception as exc:
                stats["failed"] += 1; logger.warning("[theirstack] job upsert failed id=%s title=%r: %s", job.get("ats_job_id"), job.get("title"), exc); await db.rollback()
    logger.info("[theirstack] sync completed %s", stats); return stats

async def _sync_theirstack_guarded() -> None:
    try: await sync_theirstack_jobs()
    except Exception: logger.exception("[theirstack] unexpected scheduled sync failure")


def _log_fantastic_upsert_error(exc: Exception, job: dict) -> None:
    """Log PostgreSQL diagnostics for one Fantastic row without raw payload data.

    SQLAlchemy wraps asyncpg errors in ``DBAPIError``.  The driver exception is
    normally available as ``.orig`` (or as a chained exception), where its
    SQLSTATE and constraint/column diagnostics live.  Keep the record context
    limited to its public identity fields; ``structured_data`` can contain the
    provider's complete response and must never be logged here.
    """
    database_error = exc
    seen: set[int] = set()
    while database_error is not None and id(database_error) not in seen:
        seen.add(id(database_error))
        if any(hasattr(database_error, name) for name in ("sqlstate", "constraint_name", "column_name")):
            break
        database_error = (
            getattr(database_error, "orig", None)
            or getattr(database_error, "__cause__", None)
            or getattr(database_error, "__context__", None)
        )

    logger.warning(
        "[fantastic] job upsert failed id=%s title=%r exception_class=%s "
        "exception_message=%s sqlstate=%s constraint=%s column=%s",
        job.get("ats_job_id"),
        job.get("title"),
        type(database_error or exc).__name__,
        str(database_error or exc),
        getattr(database_error, "sqlstate", None),
        getattr(database_error, "constraint_name", None),
        getattr(database_error, "column_name", None),
    )


async def _sync_fantastic_guarded() -> None:
    try:
        await sync_fantastic_jobs()
    except Exception:
        logger.exception("[fantastic] unexpected scheduled sync failure")


def _apscheduler_listener(event) -> None:
    if event.exception:
        logger.error("[job-scheduler] scheduled run raised an exception: %s", event.exception)


def _ats_sync_trigger() -> CronTrigger:
    """Return the deployment-independent ATS sync schedule in IST."""
    return CronTrigger(hour=SYNC_HOURS_IST, minute=0, second=0, timezone=IST)


def _fantastic_sync_trigger() -> CronTrigger:
    """Return the Fantastic sync schedule aligned with the ATS schedule in IST."""
    return CronTrigger(hour=SYNC_HOURS_IST, minute=0, second=0, timezone=IST)


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

    _scheduler = AsyncIOScheduler(timezone=IST)
    _scheduler.add_listener(_apscheduler_listener, EVENT_JOB_ERROR | EVENT_JOB_EXECUTED)
    job = _scheduler.add_job(
        _sync_jobs_guarded,
        trigger=_ats_sync_trigger(),
        id="job_sync",
        replace_existing=True,
    )
    _scheduler.start()
    if os.getenv("FANTASTIC_ENABLED", "false").lower() in {"1", "true", "yes", "on"}:
        fantastic_job = _scheduler.add_job(
            _sync_fantastic_guarded,
            trigger=_fantastic_sync_trigger(),
            id="fantastic_job_sync", replace_existing=True,
        )
        logger.info("[fantastic] next sync scheduled for %s", fantastic_job.next_run_time)
    if os.getenv("THEIRSTACK_ENABLED", "false").lower() in {"1", "true", "yes", "on"}:
        _scheduler.add_job(_sync_theirstack_guarded, trigger=_fantastic_sync_trigger(), id="theirstack_job_sync", replace_existing=True)
    next_run = job.next_run_time
    if next_run is not None:
        logger.info(
            "[job-scheduler] Next ATS job sync scheduled for %s IST",
            next_run.astimezone(IST).strftime("%Y-%m-%d %I:%M:%S %p"),
        )


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
