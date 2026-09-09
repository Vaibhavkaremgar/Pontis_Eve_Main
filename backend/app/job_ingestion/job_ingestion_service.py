import logging
from typing import Any

from sqlalchemy import text

from ats_agency_service import get_or_create_ats_agency

logger = logging.getLogger(__name__)


async def upsert_ats_job(
    db,
    job: dict[str, Any],
) -> str:
    """
    Insert or update one normalized ATS job.

    Returns:
        The job_descriptions.id UUID as a string.
    """

    ats_type = (job.get("ats_type") or "").strip().lower()
    ats_job_id = str(job.get("ats_job_id") or "").strip()

    if not ats_type:
        raise ValueError("ATS job is missing ats_type")

    if not ats_job_id:
        raise ValueError("ATS job is missing ats_job_id")

    # Check whether this ATS job already exists.
    result = await db.execute(
        text("""
            SELECT id, job_url
            FROM job_descriptions
            WHERE ats_type = :ats_type
              AND ats_job_id = :ats_job_id
            LIMIT 1
        """),
        {
            "ats_type": ats_type,
            "ats_job_id": ats_job_id,
        },
    )

    existing = result.first()

    if existing:
        job_id = existing[0]
        existing_job_url = existing[1]
        incoming_job_url = job.get("job_url")

        if existing_job_url is None and incoming_job_url is not None:
            await db.execute(
                text("""
                    UPDATE job_descriptions
                    SET job_url = :job_url,
                        updated_at = NOW(),
                        last_synced_at = NOW()
                    WHERE id = :id
                """),
                {
                    "id": job_id,
                    "job_url": incoming_job_url,
                },
            )
            await db.commit()
            logger.debug(
                "[job-scheduler] Filled missing job_url for existing job ats_type=%s ats_job_id=%s db_id=%s",
                ats_type, ats_job_id, job_id,
            )
        else:
            logger.debug(
                "[job-scheduler] Existing job unchanged ats_type=%s ats_job_id=%s db_id=%s",
                ats_type, ats_job_id, job_id,
            )
        return str(job_id)

    # Get the default system agency for this ATS.
    agency_id = await get_or_create_ats_agency(
        db,
        ats_type,
    )

    # Resolve the active company_registry record.
    cr_result = await db.execute(
        text("""
            SELECT id
            FROM company_registry
            WHERE LOWER(company_name) = LOWER(:company_name)
              AND LOWER(ats_type) = :ats_type
              AND is_active = TRUE
            LIMIT 1
        """),
        {
            "company_name": job.get("company_name") or "",
            "ats_type": ats_type,
        },
    )
    cr_row = cr_result.first()
    if cr_row is None:
        raise ValueError(
            f"No active company_registry record found for "
            f"company_name={job.get('company_name')!r}, ats_type={ats_type!r}"
        )
    company_registry_id = cr_row[0]

    # New ATS job.
    result = await db.execute(
        text("""
            INSERT INTO job_descriptions (
                title,
                company_name,
                department,
                location,
                employment_type,
                salary_range,
                description,
                is_active,
                status,
                created_at,
                updated_at,
                id,
                agency_id,
                company_registry_id,
                created_by_source,
                updated_by_source,
                source_app,
                job_status,
                vetting_mode,
                skills_required,
                experience_level,
                structured_data,
                remote_policy,
                ats_job_id,
                ats_type,
                job_url,
                last_synced_at
            )
            VALUES (
                :title,
                :company_name,
                :department,
                :location,
                :employment_type,
                :salary_range,
                :description,
                TRUE,
                'active',
                NOW(),
                NOW(),
                gen_random_uuid(),
                :agency_id,
                :company_registry_id,
                'PONTIS',
                'PONTIS',
                'ui',
                'active',
                'volume',
                '[]'::json,
                '',
                '{}'::json,
                '',
                :ats_job_id,
                :ats_type,
                :job_url,
                NOW()
            )
            RETURNING id
        """),
        {
            "title": job.get("title"),
            "company_name": job.get("company_name"),
            "department": job.get("department"),
            "location": job.get("location"),
            "employment_type": job.get("employment_type"),
            "salary_range": job.get("salary_range"),
            "description": job.get("description"),
            "agency_id": agency_id,
            "company_registry_id": company_registry_id,
            "ats_job_id": ats_job_id,
            "ats_type": ats_type,
            "job_url": job.get("job_url"),
        },
    )

    new_id = result.scalar_one()

    await db.commit()

    try:
        from app.job_ingestion.embedding_service import generate_job_embedding
        from app.job_ingestion.qdrant_service import ensure_collection, upsert_job_embedding

        ensure_collection()
        upsert_job_embedding(str(new_id), generate_job_embedding(job), job)
    except Exception as exc:
        logger.error("Qdrant embedding upsert failed for job_id=%s: %s", new_id, exc)

    return str(new_id)
