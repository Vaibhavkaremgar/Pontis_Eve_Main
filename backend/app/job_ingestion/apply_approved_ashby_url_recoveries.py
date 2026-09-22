"""Apply the three explicitly approved, verified Ashby URL recoveries.

This is intentionally not a general backfill.  Each approved URL is rechecked
against the live Ashby board and one existing, empty-URL database row before an
ID-scoped update that changes only ``job_url`` is allowed.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import text

from app.job_ingestion.backfill_missing_job_urls import _job_url_for_ats, _normal, _same_stable_identity

APPROVED_URLS = (
    ("ElevenLabs", "elevenlabs", "https://jobs.ashbyhq.com/elevenlabs/830e66a8-892c-4c68-b61c-494591236eef"),
    ("OpenAI", "OpenAI", "https://jobs.ashbyhq.com/OpenAI/4d0b1eb5-ea5d-460b-8fe6-23c45b0703f5"),
    ("OpenAI", "OpenAI", "https://jobs.ashbyhq.com/OpenAI/1958bdb2-dffc-4bff-a614-c13e2b8efe6f"),
)


def _get_session_local():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from server import SessionLocal  # noqa: PLC0415
    return SessionLocal


async def _empty_ashby_rows(db, company_name: str) -> list[dict[str, Any]]:
    result = await db.execute(text("""
        SELECT id, company_name, ats_type, ats_job_id, title, location, department, employment_type, job_url
        FROM job_descriptions
        WHERE LOWER(company_name) = LOWER(:company_name)
          AND LOWER(ats_type) = 'ashby'
          AND NULLIF(BTRIM(job_url), '') IS NULL
    """), {"company_name": company_name})
    return [dict(row) for row in result.mappings().fetchall()]


def _verified_row(rows: list[dict[str, Any]], current_job: dict[str, Any]) -> dict[str, Any]:
    """Find exactly one historical row with the same stored stable identity."""
    matches = [row for row in rows if _same_stable_identity(row, current_job) and str(row.get("ats_job_id") or "").strip()]
    if len(matches) != 1:
        raise RuntimeError(f"Expected one exact empty-URL row for approved current job; found {len(matches)}")
    return matches[0]


async def apply_approved_recoveries(SessionLocal, collector) -> list[str]:
    """Re-verify and apply only the three approved URLs. Returns updated IDs."""
    updated_ids: list[str] = []
    async with SessionLocal() as db:
        rows_by_company: dict[str, list[dict[str, Any]]] = {}
        board_jobs: dict[str, list[dict[str, Any]]] = {}
        for company_name, identifier, approved_url in APPROVED_URLS:
            key = _normal(company_name)
            if key not in rows_by_company:
                rows_by_company[key] = await _empty_ashby_rows(db, company_name)
                board_jobs[key] = collector.collect_company_jobs("ashby", identifier, company_name)
            candidates = [job for job in board_jobs[key] if _job_url_for_ats(job.get("job_url"), "ashby") == approved_url]
            if len(candidates) != 1:
                raise RuntimeError(f"Approved URL was not uniquely present on {company_name}'s current Ashby board: {approved_url}")
            row = _verified_row(rows_by_company[key], candidates[0])
            result = await db.execute(text("""
                UPDATE job_descriptions
                SET job_url = :job_url
                WHERE id = :id
                  AND LOWER(ats_type) = 'ashby'
                  AND NULLIF(BTRIM(job_url), '') IS NULL
                RETURNING id
            """), {"id": row["id"], "job_url": approved_url})
            updated_id = result.scalar_one_or_none()
            if updated_id is None:
                raise RuntimeError(f"Refused update because row was no longer empty/eligible: {row['id']}")
            updated_ids.append(str(updated_id))
            rows_by_company[key].remove(row)  # prevents one row satisfying two approvals
        await db.commit()

        verification = await db.execute(text("""
            SELECT id, company_name, ats_type, ats_job_id, title, job_url
            FROM job_descriptions WHERE id = ANY(:ids) ORDER BY company_name, id
        """), {"ids": updated_ids})
        for row in verification.mappings().fetchall():
            print(f"verified: id={row['id']} company={row['company_name']} ats_type={row['ats_type']} ats_job_id={row['ats_job_id']} title={row['title']!r} job_url={row['job_url']}")
    return updated_ids


async def main() -> None:
    from app.job_ingestion.collect_jobs import JobCollector
    updated = await apply_approved_recoveries(_get_session_local(), JobCollector())
    print("updated database IDs: " + ", ".join(updated))


if __name__ == "__main__":
    asyncio.run(main())
