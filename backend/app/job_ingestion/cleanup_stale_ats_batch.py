"""Dry-run first cleanup for the known 2026-08-07 URL-less ATS batch.

Run with ``--apply`` only after reviewing the printed records.  This never
deletes jobs or recommendations; it closes jobs and hides recommendations.
"""
import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from sqlalchemy import text
from server import SessionLocal

TARGET = """
 LOWER(jd.ats_type) IN ('ashby', 'lever')
 AND NULLIF(BTRIM(jd.job_url), '') IS NULL
 AND jd.last_synced_at IS NULL AND jd.is_active IS TRUE
 AND LOWER(jd.status) = 'open' AND LOWER(jd.job_status) = 'active'
 AND LOWER(jd.source_app) = 'ui'
 AND jd.created_at >= TIMESTAMPTZ '2026-08-07 00:00:00+00'
 AND jd.created_at < TIMESTAMPTZ '2026-08-08 00:00:00+00'
"""

async def rows():
    async with SessionLocal() as db:
        result = await db.execute(text(f"""
          SELECT jd.id, jd.company_name, jd.title, jd.ats_type, jd.ats_job_id,
                 jd.is_active, jd.status, jd.job_status, jd.job_url, jd.last_synced_at,
                 COUNT(cjr.id) AS recommendation_count
          FROM job_descriptions jd LEFT JOIN candidate_job_recommendations cjr ON cjr.job_id = jd.id
          WHERE {TARGET}
          GROUP BY jd.id
          ORDER BY jd.company_name, jd.title, jd.id
        """))
        return [dict(row) for row in result.mappings().fetchall()]

async def main(apply: bool = False):
    targets = await rows()
    print(f"Dry run: {len(targets)} stale ATS jobs would be closed; recommendations shown per row.")
    for row in targets:
        print(row)
    if not apply:
        return
    async with SessionLocal() as db:
        result = await db.execute(text(f"""
          UPDATE job_descriptions jd SET is_active=FALSE, status='closed', job_status='closed', updated_at=NOW()
          WHERE {TARGET} RETURNING jd.id
        """))
        ids = [str(r[0]) for r in result.fetchall()]
        if ids:
            await db.execute(text("""
              UPDATE candidate_job_recommendations SET hidden_at=COALESCE(hidden_at, NOW())
              WHERE job_id = ANY(CAST(:ids AS uuid[]))
            """), {"ids": ids})
        await db.commit()
    print(f"Applied: closed {len(ids)} jobs and hid associated recommendations.")

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true', help='perform the reviewed targeted update')
    args = parser.parse_args()
    asyncio.run(main(args.apply))
