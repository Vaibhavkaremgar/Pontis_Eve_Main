"""
Integration test: fetch one job from PostgreSQL → embed → upsert to Qdrant → verify.
Run from backend/: python test_job_qdrant.py
"""
import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "app" / "job_ingestion"))

from sqlalchemy import text

from server import SessionLocal
from app.job_ingestion.embedding_service import generate_job_embedding
from app.job_ingestion.qdrant_service import (
    COLLECTION_NAME,
    ensure_collection,
    upsert_job_embedding,
    _client,
)

import uuid


async def fetch_one_job() -> dict:
    async with SessionLocal() as db:
        result = await db.execute(
            text("""
                SELECT id, ats_job_id, title, company_name, ats_type,
                       department, location, employment_type, salary_range,
                       description
                FROM job_descriptions
                WHERE ats_type IN ('greenhouse', 'lever', 'ashby', 'workable')
                  AND is_active = TRUE
                LIMIT 1
            """)
        )
        row = result.mappings().fetchone()
    if row is None:
        raise RuntimeError("No matching job found in job_descriptions")
    return dict(row)


def main():
    row = asyncio.run(fetch_one_job())

    job_id = str(row["id"])
    job = {
        "ats_job_id":      row["ats_job_id"],
        "title":           row["title"],
        "company_name":    row["company_name"],
        "ats_type":        row["ats_type"],
        "department":      row["department"],
        "location":        row["location"],
        "employment_type": row["employment_type"],
        "salary_range":    row["salary_range"],
        "description":     row["description"],
    }

    print(f"Fetched job: id={job_id}  title={job['title']!r}  ats_type={job['ats_type']}")

    embedding = generate_job_embedding(job)
    print(f"Embedding generated: size={len(embedding)}")

    ensure_collection()
    print(f"Collection '{COLLECTION_NAME}' ensured")

    upsert_job_embedding(job_id, embedding, job)
    print("Upserted embedding to Qdrant")

    point_id = uuid.UUID(job_id).int % (2 ** 63)
    results = _client.retrieve(
        collection_name=COLLECTION_NAME,
        ids=[point_id],
        with_vectors=True,
        with_payload=True,
    )

    assert results, f"Point {point_id} not found in Qdrant"
    point = results[0]

    print("\n--- Verification ---")
    print(f"Collection : {COLLECTION_NAME}")
    print(f"Job ID     : {point.payload['job_id']}")
    print(f"Vector size: {len(point.vector)}")
    print(f"Payload    : {point.payload}")
    print("\nAll assertions passed ✓")


if __name__ == "__main__":
    main()
