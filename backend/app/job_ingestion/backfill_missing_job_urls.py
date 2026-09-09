"""
Dry-run backfill report for missing job_url values on existing ATS jobs.

This script:
  - finds active job_descriptions rows where:
      * ats_type IN ('lever', 'ashby')
      * job_url IS NULL
      * ats_job_id IS NOT NULL
  - re-fetches the matching ATS company/board through the existing collectors
  - matches rows by ats_job_id
  - reports whether a normalized URL can be recovered

It never writes to the database and never starts the scheduler.

Usage (from backend/):
    python -m app.job_ingestion.backfill_missing_job_urls
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from sqlalchemy import text


def _get_session_local():
    """Import SessionLocal from server to reuse the existing DB setup."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from server import SessionLocal  # noqa: PLC0415
    return SessionLocal


ATS_TYPES = ("lever", "ashby")


async def _load_target_rows(SessionLocal) -> list[dict[str, Any]]:
    async with SessionLocal() as db:
        result = await db.execute(
            text("""
                SELECT
                    jd.id,
                    jd.company_name,
                    jd.ats_type,
                    jd.ats_job_id,
                    jd.job_url,
                    cr.identifier
                FROM job_descriptions AS jd
                JOIN company_registry AS cr
                  ON LOWER(cr.company_name) = LOWER(jd.company_name)
                 AND LOWER(cr.ats_type) = LOWER(jd.ats_type)
                 AND cr.is_active = TRUE
                WHERE jd.is_active = TRUE
                  AND jd.ats_type = ANY(:ats_types)
                  AND jd.job_url IS NULL
                  AND jd.ats_job_id IS NOT NULL
                ORDER BY jd.ats_type, jd.company_name, jd.id
            """),
            {"ats_types": list(ATS_TYPES)},
        )
        return [dict(row) for row in result.mappings().fetchall()]


def _group_rows(rows: list[dict[str, Any]]) -> dict[tuple[str, str, str], list[dict[str, Any]]]:
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = (
            (row.get("ats_type") or "").strip().lower(),
            str(row.get("identifier") or "").strip(),
            str(row.get("company_name") or "").strip(),
        )
        grouped[key].append(row)
    return grouped


async def main() -> None:
    from app.job_ingestion.collect_jobs import JobCollector

    SessionLocal = _get_session_local()
    collector = JobCollector()

    rows = await _load_target_rows(SessionLocal)
    total_affected = len(rows)

    print(f"Total affected rows found: {total_affected}")

    if not rows:
        print("Rows successfully matched: 0")
        print("Rows where a URL was recovered: 0")
        print("Rows still missing a URL: 0")
        print("Rows whose ATS job could not be found: 0")
        return

    grouped_rows = _group_rows(rows)

    matched_count = 0
    recovered_count = 0
    still_missing_count = 0
    not_found_count = 0

    for (ats_type, identifier, company_name), group_rows in grouped_rows.items():
        if not ats_type or not identifier:
            not_found_count += len(group_rows)
            for row in group_rows:
                print(
                    f"ATS job not found: id={row['id']} ats_type={row['ats_type']} "
                    f"ats_job_id={row['ats_job_id']} company_name={row['company_name']}",
                )
            continue

        try:
            jobs = collector.collect_company_jobs(
                ats_type=ats_type,
                identifier=identifier,
                company_name=company_name,
            )
        except Exception as exc:
            print(
                f"Failed to fetch ATS jobs for company_name={company_name!r} "
                f"ats_type={ats_type!r} identifier={identifier!r}: {exc}",
            )
            not_found_count += len(group_rows)
            for row in group_rows:
                print(
                    f"ATS job not found: id={row['id']} ats_type={row['ats_type']} "
                    f"ats_job_id={row['ats_job_id']} company_name={row['company_name']}",
                )
            continue

        jobs_by_id = {
            str(job.get("ats_job_id") or "").strip(): job
            for job in jobs
            if str(job.get("ats_job_id") or "").strip()
        }

        for row in group_rows:
            row_ats_job_id = str(row.get("ats_job_id") or "").strip()
            job = jobs_by_id.get(row_ats_job_id)

            if job is None:
                not_found_count += 1
                print(
                    f"ATS job not found: id={row['id']} ats_type={row['ats_type']} "
                    f"ats_job_id={row['ats_job_id']} company_name={row['company_name']}",
                )
                continue

            matched_count += 1
            recovered_job_url = (job.get("job_url") or "").strip() or None

            if recovered_job_url:
                recovered_count += 1
                print(
                    f"id={row['id']} ats_type={row['ats_type']} ats_job_id={row['ats_job_id']} "
                    f"company_name={row['company_name']} recovered_job_url={recovered_job_url}",
                )
            else:
                still_missing_count += 1

    print(f"Rows successfully matched: {matched_count}")
    print(f"Rows where a URL was recovered: {recovered_count}")
    print(f"Rows still missing a URL: {still_missing_count}")
    print(f"Rows whose ATS job could not be found: {not_found_count}")


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    asyncio.run(main())
