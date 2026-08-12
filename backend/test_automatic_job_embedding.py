"""
Integration test: upsert_ats_job() → verify PostgreSQL insert → verify Qdrant vector → cleanup.
Run from backend/: python test_automatic_job_embedding.py
"""
import asyncio
import sys
import uuid
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "app" / "job_ingestion"))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from server import DATABASE_URL
from app.job_ingestion.job_ingestion_service import upsert_ats_job
from app.job_ingestion.qdrant_service import COLLECTION_NAME, _client

engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = async_sessionmaker(bind=engine, expire_on_commit=False)

TEST_JOB = {
    "ats_type": "greenhouse",
    "ats_job_id": f"TEST-EMBED-{uuid.uuid4().hex[:12]}",
    "title": "TEST - Automatic Embedding Verification",
    "company_name": "Jumio",
    "department": "Engineering",
    "location": "Remote",
    "employment_type": "Full-time",
    "salary_range": "100000-120000",
    "description": "Temporary test job for embedding pipeline verification.",
    "job_url": "https://example.com/test-job",
}


async def run_test():
    async with SessionLocal() as db:
        # --- Insert via upsert_ats_job ---
        job_id = await upsert_ats_job(db, TEST_JOB)
        print(f"Upserted job: id={job_id}  ats_job_id={TEST_JOB['ats_job_id']}")

        # --- Verify PostgreSQL row ---
        result = await db.execute(
            text("SELECT id, title, ats_job_id, is_active FROM job_descriptions WHERE id = :id"),
            {"id": job_id},
        )
        row = result.mappings().first()
        assert row is not None, "Job row not found in job_descriptions"
        assert str(row["id"]) == job_id
        assert row["title"] == TEST_JOB["title"]
        assert row["ats_job_id"] == TEST_JOB["ats_job_id"]
        assert row["is_active"] is True
        print("PostgreSQL assertion passed ✓")

        # --- Verify Qdrant point ---
        point_id = uuid.UUID(job_id).int % (2**63)
        results = _client.retrieve(
            collection_name=COLLECTION_NAME,
            ids=[point_id],
            with_vectors=True,
            with_payload=True,
        )
        assert results, f"Qdrant point {point_id} not found in '{COLLECTION_NAME}'"
        point = results[0]

        assert len(point.vector) == 384, f"Expected vector size 384, got {len(point.vector)}"
        assert point.payload.get("embedding_version") == "v2_structured", (
            f"Expected embedding_version='v2_structured', got {point.payload.get('embedding_version')!r}"
        )
        assert point.payload.get("job_id") == job_id
        print(f"Qdrant assertions passed ✓  (vector_size={len(point.vector)}, embedding_version={point.payload['embedding_version']})")

        # --- Cleanup: PostgreSQL ---
        await db.execute(
            text("DELETE FROM job_descriptions WHERE id = :id"),
            {"id": job_id},
        )
        await db.commit()
        print("PostgreSQL row deleted ✓")

    # --- Cleanup: Qdrant ---
    _client.delete(
        collection_name=COLLECTION_NAME,
        points_selector=[point_id],
    )
    print("Qdrant point deleted ✓")

    await engine.dispose()
    print("\nAll assertions passed ✓")


if __name__ == "__main__":
    asyncio.run(run_test())
