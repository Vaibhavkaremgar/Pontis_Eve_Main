"""
Backfill Qdrant embeddings for all existing jobs in job_descriptions
where ats_type is greenhouse, lever, ashby, or workable.

Safe to run multiple times — Qdrant upsert overwrites existing points.

Usage (from backend/):
    python -m app.job_ingestion.backfill_embeddings
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from sqlalchemy import text
from server import SessionLocal
from app.job_ingestion.embedding_service import generate_job_embedding
from app.job_ingestion.qdrant_service import ensure_collection, upsert_job_embedding

ATS_TYPES = ("greenhouse", "lever", "ashby", "workable")
BATCH_SIZE = 100


async def backfill():
    ensure_collection()

    async with SessionLocal() as db:
        count_row = await db.execute(
            text("SELECT COUNT(*) FROM job_descriptions WHERE ats_type = ANY(:types)"),
            {"types": list(ATS_TYPES)},
        )
        total = count_row.scalar() or 0

    print(f"Total jobs to index: {total}")

    succeeded = 0
    failed = 0
    offset = 0

    while offset < total:
        async with SessionLocal() as db:
            rows = await db.execute(
                text("""
                    SELECT id, ats_job_id, ats_type, title, company_name, department,
                           location, employment_type, salary_range, experience_level,
                           skills_required, description
                    FROM job_descriptions
                    WHERE ats_type = ANY(:types)
                    ORDER BY id
                    LIMIT :limit OFFSET :offset
                """),
                {"types": list(ATS_TYPES), "limit": BATCH_SIZE, "offset": offset},
            )
            jobs = rows.mappings().fetchall()

        for job in jobs:
            job_dict = dict(job)
            job_id = str(job_dict["id"])
            try:
                embedding = generate_job_embedding(job_dict)
                upsert_job_embedding(job_id, embedding, job_dict)
                succeeded += 1
            except Exception as e:
                print(f"  FAILED job_id={job_id}: {e}")
                failed += 1

        offset += BATCH_SIZE
        print(f"  Processed {min(offset, total)}/{total} — succeeded={succeeded} failed={failed}")

    print(f"\nDone. Successfully indexed: {succeeded} | Failures: {failed}")


if __name__ == "__main__":
    asyncio.run(backfill())
