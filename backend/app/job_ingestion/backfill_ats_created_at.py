"""Explicit, idempotent repair of ATS job posting timestamps.

Run manually after setting the normal provider credentials:

    python -m app.job_ingestion.backfill_ats_created_at --apply

Without ``--apply`` this command only fetches and reports eligible matches.
It is deliberately not imported or scheduled by the regular ATS scheduler.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.job_ingestion.normalize import (  # noqa: E402
    normalize_ashby,
    normalize_fantastic,
    normalize_lever,
    parse_ats_datetime,
)

logger = logging.getLogger(__name__)

TIMESTAMP_PROVIDERS = ("ashby", "lever", "fantastic")
NO_ORIGINAL_TIMESTAMP_PROVIDER = "greenhouse"


def _get_session_local():
    from server import SessionLocal  # noqa: PLC0415
    return SessionLocal


async def _target_rows(SessionLocal) -> list[dict[str, Any]]:
    """Load only stable ATS identities; no job fields are changed here."""
    async with SessionLocal() as db:
        result = await db.execute(text("""
            SELECT jd.id, LOWER(jd.ats_type) AS ats_type, jd.ats_job_id,
                   jd.company_name, cr.identifier
            FROM job_descriptions jd
            LEFT JOIN company_registry cr ON cr.id = jd.company_registry_id
            WHERE LOWER(jd.ats_type) = ANY(:ats_types)
              AND NULLIF(BTRIM(jd.ats_job_id), '') IS NOT NULL
            ORDER BY LOWER(jd.ats_type), jd.company_name, jd.id
        """), {"ats_types": [*TIMESTAMP_PROVIDERS, NO_ORIGINAL_TIMESTAMP_PROVIDER]})
        return [dict(row) for row in result.mappings().fetchall()]


def _normalizer(ats_type: str) -> Callable[[dict[str, Any], str], dict[str, Any]]:
    return {"ashby": normalize_ashby, "lever": normalize_lever}[ats_type]


def _timestamp_index(ats_type: str, raw_jobs: list[dict[str, Any]], company_name: str) -> dict[str, Any]:
    """Index valid, parsed provider timestamps by the provider's job ID."""
    normalize = _normalizer(ats_type)
    timestamps: dict[str, Any] = {}
    for raw_job in raw_jobs:
        # JobCollector returns normalized values, while tests and alternative
        # callers can supply the connector's raw provider response.
        normalized = raw_job if "ats_job_id" in raw_job else normalize(raw_job, company_name)
        ats_job_id = str(normalized.get("ats_job_id") or "").strip()
        created_at = parse_ats_datetime(normalized.get("created_at"))
        if ats_job_id and created_at is not None:
            timestamps[ats_job_id] = created_at
    return timestamps


async def _apply_created_at(db, row: dict[str, Any], created_at) -> bool:
    """Change only created_at, and only when the exact ATS identity still matches."""
    result = await db.execute(text("""
        UPDATE job_descriptions
        SET created_at = :created_at
        WHERE id = :id
          AND LOWER(ats_type) = :ats_type
          AND ats_job_id = :ats_job_id
          AND created_at IS DISTINCT FROM :created_at
        RETURNING id
    """), {
        "id": row["id"], "ats_type": row["ats_type"],
        "ats_job_id": row["ats_job_id"], "created_at": created_at,
    })
    return result.scalar_one_or_none() is not None


async def backfill_ats_created_at(SessionLocal, collector, fantastic_client=None, *, apply: bool = False) -> dict[str, int]:
    """Fetch live ATS records and optionally repair exact-ID timestamp matches.

    ``created_at`` is never bound from an unparsed value.  Greenhouse rows are
    counted and skipped because its public response's ``updated_at`` is not a
    posting timestamp.  A failed board fetch or missing/invalid source date
    leaves every corresponding database row untouched.
    """
    rows = await _target_rows(SessionLocal)
    stats = Counter(rows_seen=len(rows), matched=0, updated=0, unchanged=0,
                    missing_timestamp=0, fetch_failed=0, greenhouse_skipped=0)
    by_board: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    fantastic_rows: list[dict[str, Any]] = []
    for row in rows:
        ats_type = str(row["ats_type"] or "").strip().lower()
        if ats_type == NO_ORIGINAL_TIMESTAMP_PROVIDER:
            stats["greenhouse_skipped"] += 1
        elif ats_type == "fantastic":
            fantastic_rows.append(row)
        else:
            identifier = str(row.get("identifier") or "").strip()
            if not identifier:
                stats["fetch_failed"] += 1
                continue
            by_board[(ats_type, identifier, str(row.get("company_name") or "").strip())].append(row)

    async with SessionLocal() as db:
        for (ats_type, identifier, company_name), board_rows in by_board.items():
            try:
                timestamps = _timestamp_index(
                    ats_type, collector.collect_company_jobs(ats_type, identifier, company_name), company_name,
                )
            except Exception as exc:
                logger.warning("[ats-created-at-backfill] fetch failed ats=%s identifier=%s: %s", ats_type, identifier, exc)
                stats["fetch_failed"] += len(board_rows)
                continue
            for row in board_rows:
                created_at = timestamps.get(str(row["ats_job_id"]).strip())
                if created_at is None:
                    stats["missing_timestamp"] += 1
                    continue
                stats["matched"] += 1
                if apply and await _apply_created_at(db, row, created_at):
                    stats["updated"] += 1
                else:
                    stats["unchanged"] += 1

        if fantastic_rows:
            try:
                if fantastic_client is None:
                    from app.job_ingestion.connectors.fantastic import FantasticClient  # noqa: PLC0415
                    fantastic_client = FantasticClient()
                raw_jobs = await fantastic_client.fetch_active_ats()
                timestamps = {
                    str(normalized.get("ats_job_id") or "").strip(): normalized["created_at"]
                    for raw_job in raw_jobs
                    if (normalized := normalize_fantastic(raw_job)).get("created_at") is not None
                    and str(normalized.get("ats_job_id") or "").strip()
                }
            except Exception as exc:
                logger.warning("[ats-created-at-backfill] Fantastic fetch failed: %s", exc)
                stats["fetch_failed"] += len(fantastic_rows)
                timestamps = {}
            for row in fantastic_rows:
                created_at = timestamps.get(str(row["ats_job_id"]).strip())
                if created_at is None:
                    stats["missing_timestamp"] += 1
                    continue
                stats["matched"] += 1
                if apply and await _apply_created_at(db, row, created_at):
                    stats["updated"] += 1
                else:
                    stats["unchanged"] += 1
        if apply:
            await db.commit()
    return {key: int(value) for key, value in stats.items()}


async def main(*, apply: bool = False) -> dict[str, int]:
    from app.job_ingestion.collect_jobs import JobCollector
    stats = await backfill_ats_created_at(_get_session_local(), JobCollector(), apply=apply)
    print("ATS created_at backfill " + ("applied" if apply else "dry run") + ": " + ", ".join(f"{key}={value}" for key, value in sorted(stats.items())))
    return stats


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="One-time ATS original-posting-timestamp backfill")
    parser.add_argument("--apply", action="store_true", help="perform the created_at-only updates (default is dry run)")
    args = parser.parse_args()
    asyncio.run(main(apply=args.apply))
