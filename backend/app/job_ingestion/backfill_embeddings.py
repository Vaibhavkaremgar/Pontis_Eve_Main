"""Backfill Qdrant embeddings for candidate-visible active/open/published jobs.

Safe to run repeatedly: each job maps to one deterministic Qdrant point ID.
Use ``--reindex`` only when deliberately replacing an incompatible embedding version.

Usage (from backend/):
    python -m app.job_ingestion.backfill_embeddings --reindex
"""

import argparse
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from sqlalchemy import text
from server import SessionLocal
from app.job_ingestion.embedding_service import generate_job_embedding
from app.job_ingestion.qdrant_service import ensure_collection, upsert_job_embedding
from app.job_ingestion.lifecycle import candidate_visible_where

BATCH_SIZE = 100
logger = logging.getLogger(__name__)

# This intentionally mirrors candidate_job_matching_service's DB validation.
CANDIDATE_VISIBLE_WHERE = candidate_visible_where("job_descriptions")


async def backfill(*, reindex: bool = False):
    ensure_collection()
    async with SessionLocal() as db:
        count_row = await db.execute(
            text(f"SELECT COUNT(*) FROM job_descriptions WHERE {CANDIDATE_VISIBLE_WHERE}"),
        )
        total = count_row.scalar() or 0

    logger.info("[backfill] active candidate-visible jobs=%d", total)
    print(f"Total candidate-visible jobs to index: {total}")
    succeeded = failed = offset = 0
    while offset < total:
        async with SessionLocal() as db:
            rows = await db.execute(
                text(f"""
                    SELECT id, ats_job_id, ats_type, title, company_name, department,
                           location, employment_type, salary_range, experience_level,
                           skills_required, description
                    FROM job_descriptions
                    WHERE {CANDIDATE_VISIBLE_WHERE}
                    ORDER BY id
                    LIMIT :limit OFFSET :offset
                """),
                {"limit": BATCH_SIZE, "offset": offset},
            )
            jobs = rows.mappings().fetchall()

        logger.info("[backfill] jobs selected for embedding=%d", len(jobs))
        for job in jobs:
            job_dict = dict(job)
            job_id = str(job_dict["id"])
            try:
                embedding = generate_job_embedding(job_dict)
                upsert_job_embedding(job_id, embedding, job_dict, reindex=reindex)
                succeeded += 1
            except Exception as exc:
                logger.exception("[backfill] failed job_id=%s", job_id)
                print(f"  FAILED job_id={job_id}: {exc}")
                failed += 1

        offset += BATCH_SIZE
        logger.info("[backfill] Qdrant points written=%d failures=%d", succeeded, failed)
        print(f"  Processed {min(offset, total)}/{total} - succeeded={succeeded} failed={failed}")

    print(f"\nDone. Successfully indexed: {succeeded} | Failures: {failed}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--reindex", action="store_true", help="replace incompatible vector versions explicitly")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    asyncio.run(backfill(reindex=args.reindex))
