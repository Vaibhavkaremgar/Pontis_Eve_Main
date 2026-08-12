import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from server import DATABASE_URL
from app.job_ingestion.collect_jobs import JobCollector
from app.job_ingestion.job_ingestion_service import upsert_ats_job


engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = async_sessionmaker(bind=engine, expire_on_commit=False)


async def main():
    collector = JobCollector()
    jobs = collector.collect_company_jobs(
        ats_type="greenhouse",
        identifier="jumio",
        company_name="Jumio",
    )
    print(f"Fetched {len(jobs)} jobs from Greenhouse")

    async with SessionLocal() as db:
        # Fetch all existing (ats_type, ats_job_id) pairs for these jobs.
        result = await db.execute(
            text("""
                SELECT ats_type, ats_job_id
                FROM job_descriptions
                WHERE ats_type = 'greenhouse'
                  AND ats_job_id = ANY(:ids)
            """),
            {"ids": [j["ats_job_id"] for j in jobs]},
        )
        existing_pairs = {(r[0], r[1]) for r in result.fetchall()}

        new_job = next(
            (j for j in jobs if (j["ats_type"].strip().lower(), str(j["ats_job_id"]).strip()) not in existing_pairs),
            None,
        )

        if new_job is None:
            print("No new jobs found")
            return

        print(f"\nInserting new job: {new_job['ats_job_id']} | {new_job['title']}")

        job_id = await upsert_ats_job(db, new_job)

        result = await db.execute(
            text("""
                SELECT
                    id,
                    title,
                    company_name,
                    ats_type,
                    ats_job_id,
                    agency_id,
                    company_registry_id,
                    job_url,
                    is_active,
                    job_status,
                    last_synced_at
                FROM job_descriptions
                WHERE id = :id
            """),
            {"id": job_id},
        )
        row = result.mappings().first()
        print("\nInserted job:")
        for k, v in dict(row).items():
            print(f"  {k}: {v}")

    await engine.dispose()


async def test_insert_path():
    collector = JobCollector()
    jobs = collector.collect_company_jobs(
        ats_type="greenhouse",
        identifier="jumio",
        company_name="Jumio",
    )

    template = jobs[0].copy()
    template["ats_job_id"] = "9999999999"
    template["title"] = "TEST - Account Executive, APAC"

    async with SessionLocal() as db:
        job_id = await upsert_ats_job(db, template)
        print(f"Inserted test job with id: {job_id}")

        result = await db.execute(
            text("""
                SELECT
                    id,
                    title,
                    company_name,
                    ats_type,
                    ats_job_id,
                    agency_id,
                    company_registry_id,
                    job_url,
                    is_active,
                    job_status,
                    last_synced_at
                FROM job_descriptions
                WHERE id = :id
            """),
            {"id": job_id},
        )
        row = result.mappings().first()
        assert row is not None, "Inserted row not found"
        assert str(row["id"]) == job_id
        assert row["title"] == "TEST - Account Executive, APAC"
        assert row["company_name"] == template["company_name"]
        assert row["ats_type"] == "greenhouse"
        assert row["ats_job_id"] == "9999999999"
        assert row["agency_id"] is not None
        assert row["company_registry_id"] is not None
        assert row["is_active"] is True
        assert row["job_status"] == "active"
        assert row["last_synced_at"] is not None

        print("\nVerified inserted row:")
        for k, v in dict(row).items():
            print(f"  {k}: {v}")

        # Clean up test row.
        await db.execute(
            text("DELETE FROM job_descriptions WHERE id = :id"),
            {"id": job_id},
        )
        await db.commit()
        print("\nTest row cleaned up.")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(test_insert_path())
