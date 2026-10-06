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
METADATA_RETRY_BATCH_SIZE = 25

_scheduler: AsyncIOScheduler | None = None
_sync_lock: asyncio.Lock | None = None


async def reconcile_missing_ats_jobs(db, *, company_registry_id, ats_type: str, current_ats_ids: set[str]) -> list[str]:
    """Close jobs absent from a *complete successful* current ATS board.

    The registry foreign key scopes this to one configured board, avoiding
    accidental cross-company deactivation where display names collide.
    """
    # These collectors return the complete public board in one response.  Do
    # not add bounded/search-feed providers here: absence from those feeds is
    # not evidence that a stored job closed.
    if ats_type not in {"ashby", "lever", "greenhouse", "workable"}:
        logger.info(
            "[job-lifecycle] provider=%s snapshot_status=unsupported_for_reconciliation "
            "pages_fetched=0 jobs_seen=%d jobs_closed=0 jobs_reactivated=0 jobs_skipped=0 "
            "reason=provider_response_is_not_a_complete_board_snapshot",
            ats_type, len(current_ats_ids),
        )
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
        logger.info(
            "[job-lifecycle] provider=%s snapshot_status=complete pages_fetched=1 "
            "jobs_seen=%d jobs_closed=0 jobs_reactivated=0 jobs_skipped=0 reason=none",
            ats_type, len(current_ats_ids),
        )
        return []
    await db.execute(text("""
        UPDATE job_descriptions
        SET is_active = FALSE, status = 'closed', job_status = 'closed', updated_at = NOW()
        WHERE id = ANY(CAST(:job_ids AS uuid[]))
    """), {"job_ids": missing})
    logger.info(
        "[job-lifecycle] provider=%s snapshot_status=complete pages_fetched=1 "
        "jobs_seen=%d jobs_closed=%d jobs_reactivated=0 jobs_skipped=0 reason=missing_from_complete_board",
        ats_type, len(current_ats_ids), len(missing),
    )
    # Preserve recommendation/application history while removing it from all
    # candidate-visible paths.
    await db.execute(text("""
        UPDATE candidate_job_recommendations
        SET hidden_at = COALESCE(hidden_at, NOW())
        WHERE job_id = ANY(CAST(:job_ids AS uuid[]))
    """), {"job_ids": missing})
    return missing


async def _close_confirmed_jobs(db, *, ats_type: str, ats_job_ids: set[str]) -> list[str]:
    """Close only provider-confirmed jobs and preserve their history."""
    if not ats_job_ids:
        return []
    result = await db.execute(text("""
        SELECT id, ats_job_id
        FROM job_descriptions
        WHERE LOWER(ats_type) = :ats_type
          AND is_active IS TRUE
          AND ats_job_id = ANY(:ats_job_ids)
    """), {"ats_type": ats_type, "ats_job_ids": list(ats_job_ids)})
    db_ids = [str(row[0]) for row in result.fetchall()]
    if not db_ids:
        return []
    await db.execute(text("""
        UPDATE job_descriptions
        SET is_active = FALSE, status = 'closed', job_status = 'closed', updated_at = NOW()
        WHERE id = ANY(CAST(:job_ids AS uuid[]))
    """), {"job_ids": db_ids})
    await db.execute(text("""
        UPDATE candidate_job_recommendations
        SET hidden_at = COALESCE(hidden_at, NOW())
        WHERE job_id = ANY(CAST(:job_ids AS uuid[]))
    """), {"job_ids": db_ids})
    return db_ids


async def cleanup_fantastic_expired_jobs() -> dict[str, int]:
    """Apply only IDs explicitly returned by Fantastic's expiration feed."""
    from app.job_ingestion.connectors.fantastic import FantasticClient
    checked = confirmed = already_closed = unresolved = 0
    try:
        expired_ids = await FantasticClient().fetch_expired_ats_ids("1d")
        checked = len(expired_ids)
        SessionLocal = _get_session_local()
        async with SessionLocal() as db:
            closed_db_ids = await _close_confirmed_jobs(db, ats_type="fantastic", ats_job_ids=set(expired_ids))
            await db.commit()
            confirmed = len(closed_db_ids)
            unresolved = max(0, checked - confirmed)
            for job_id in closed_db_ids:
                try:
                    from app.job_ingestion.qdrant_service import delete_job_embedding
                    delete_job_embedding(job_id)
                except Exception as exc:
                    logger.warning("[job-lifecycle] provider=fantastic qdrant_cleanup_failed job_id=%s error=%s", job_id, exc)
    except Exception as exc:
        logger.error("[job-lifecycle] provider=fantastic checked_at=%s jobs_checked=%d jobs_confirmed_closed=0 jobs_already_closed=0 jobs_unresolved=0 errors=1 reason=%s", datetime.now(IST).isoformat(), checked, exc)
        return {"jobs_checked": checked, "jobs_confirmed_closed": 0, "jobs_already_closed": 0, "jobs_unresolved": 0, "errors": 1}
    logger.info("[job-lifecycle] provider=fantastic checked_at=%s jobs_checked=%d jobs_confirmed_closed=%d jobs_already_closed=0 jobs_unresolved=%d errors=0", datetime.now(IST).isoformat(), checked, confirmed, unresolved)
    return {"jobs_checked": checked, "jobs_confirmed_closed": confirmed, "jobs_already_closed": already_closed, "jobs_unresolved": unresolved, "errors": 0}


async def cleanup_theirstack_closed_jobs() -> dict[str, int]:
    """Batch exact TheirStack ID lookups and close explicit closed_at rows only."""
    from app.job_ingestion.connectors.theirstack import TheirStackClient
    SessionLocal = _get_session_local()
    async with SessionLocal() as db:
        result = await db.execute(text("""
            SELECT ats_job_id FROM job_descriptions
            WHERE LOWER(ats_type) = 'theirstack' AND is_active IS TRUE AND ats_job_id IS NOT NULL
        """))
        stored_ids = [str(row[0]).strip() for row in result.fetchall() if str(row[0]).strip().isdigit()]
    checked = confirmed = unresolved = errors = 0
    try:
        client = TheirStackClient()
        for offset in range(0, len(stored_ids), 100):
            batch = stored_ids[offset:offset + 100]
            jobs = await client.fetch_jobs_by_ids(batch)
            checked += len(batch)
            closed_ids = {str(job.get("id")).strip() for job in jobs if job.get("closed_at") is not None}
            if closed_ids:
                async with SessionLocal() as db:
                    closed_db_ids = await _close_confirmed_jobs(db, ats_type="theirstack", ats_job_ids=closed_ids)
                    await db.commit()
                    confirmed += len(closed_db_ids)
                    for job_id in closed_db_ids:
                        try:
                            from app.job_ingestion.qdrant_service import delete_job_embedding
                            delete_job_embedding(job_id)
                        except Exception as exc:
                            logger.warning("[job-lifecycle] provider=theirstack qdrant_cleanup_failed job_id=%s error=%s", job_id, exc)
            unresolved += len(set(batch) - closed_ids)
    except Exception as exc:
        errors = 1
        logger.error("[job-lifecycle] provider=theirstack checked_at=%s jobs_checked=%d jobs_confirmed_closed=%d jobs_already_closed=0 jobs_unresolved=%d errors=1 reason=%s", datetime.now(IST).isoformat(), checked, confirmed, unresolved, exc)
    else:
        logger.info("[job-lifecycle] provider=theirstack checked_at=%s jobs_checked=%d jobs_confirmed_closed=%d jobs_already_closed=0 jobs_unresolved=%d errors=0", datetime.now(IST).isoformat(), checked, confirmed, unresolved)
    return {"jobs_checked": checked, "jobs_confirmed_closed": confirmed, "jobs_already_closed": 0, "jobs_unresolved": unresolved, "errors": errors}


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

    try:
        await retry_pending_metadata_extraction()
    except Exception as exc:
        # Metadata retries are auxiliary to the provider sync; one database or
        # row-level problem must not prevent the normal ATS cycle.
        logger.error("[job-scheduler] pending metadata retry pass failed: %s", exc, exc_info=True)

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


async def retry_pending_metadata_extraction() -> int:
    """Retry a small, FIFO batch of jobs whose optional metadata was deferred.

    This deliberately operates only on rows already in ``job_descriptions``.
    A failed/unavailable Groq attempt is not persisted, so the existing
    metadata remains untouched and the row stays eligible for a later cycle.
    """
    from app.job_ingestion.job_ingestion_service import upsert_ats_job
    from app.job_ingestion.job_skill_extraction import extract_missing_job_skills

    SessionLocal = _get_session_local()
    completed = 0
    async with SessionLocal() as db:
        result = await db.execute(text("""
            SELECT id, ats_type, ats_job_id, title, company_name, department,
                   location, responsibilities, city, state, country, remote,
                   company_website_url, company_logo_url, industry, valid_through,
                   employment_type, experience_required, salary_range, description,
                   requirements, skills_required, skills, experience_level,
                   structured_data, remote_policy, job_url
            FROM job_descriptions
            WHERE structured_data->>'metadata_extraction_status' = 'pending_retry'
            ORDER BY updated_at NULLS FIRST, id
            LIMIT :batch_size
        """), {"batch_size": METADATA_RETRY_BATCH_SIZE})
        rows = result.mappings().all()

        for row in rows:
            job = dict(row)
            try:
                job = await extract_missing_job_skills(job)
                structured = job.get("structured_data")
                # Do not persist an unavailable/failed attempt.  In
                # particular, this avoids replacing existing metadata with
                # the incomplete retry payload.
                if not isinstance(structured, dict) or structured.get("metadata_extraction_status") != "complete":
                    continue
                await upsert_ats_job(db, job)
                completed += 1
            except Exception as exc:
                logger.warning("[job-scheduler] metadata retry failed id=%s: %s", row.get("id"), exc)
                await db.rollback()
        if completed:
            await db.commit()
    logger.info("[job-scheduler] metadata retries completed=%d batch=%d", completed, len(rows))
    return completed


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
    logger.info(
        "[job-lifecycle] provider=fantastic snapshot_status=bounded_non_snapshot "
        "pages_fetched=%d jobs_seen=%d jobs_closed=0 jobs_reactivated=0 jobs_skipped=%d "
        "reason=bounded_active_feed_absence_is_not_safe_to_reconcile",
        stats["pages_requested"], stats["fetched"], stats["skipped"],
    )
    logger.info("[fantastic] sync completed stats=%s", stats)
    stats["feeds"] = per_feed
    return stats

async def sync_theirstack_jobs() -> dict[str, int]:
    from app.job_ingestion.connectors.theirstack import TheirStackClient
    from app.job_ingestion.job_ingestion_service import upsert_ats_job
    from app.job_ingestion.job_skill_extraction import extract_missing_job_skills
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
    logger.info(
        "[job-lifecycle] provider=theirstack snapshot_status=bounded_search_non_snapshot "
        "pages_fetched=unknown jobs_seen=%d jobs_closed=0 jobs_reactivated=0 jobs_skipped=%d "
        "reason=search_response_is_not_proven_complete",
        stats["fetched"], stats["skipped"],
    )
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
    if os.getenv("FANTASTIC_ENABLED", "false").lower() in {"1", "true", "yes", "on"}:
        _scheduler.add_job(cleanup_fantastic_expired_jobs, trigger=CronTrigger(hour=12, minute=30, timezone=IST), id="fantastic_expiration_cleanup", replace_existing=True)
    if os.getenv("THEIRSTACK_ENABLED", "false").lower() in {"1", "true", "yes", "on"}:
        _scheduler.add_job(cleanup_theirstack_closed_jobs, trigger=CronTrigger(hour=12, minute=30, timezone=IST), id="theirstack_expiration_cleanup", replace_existing=True)
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
