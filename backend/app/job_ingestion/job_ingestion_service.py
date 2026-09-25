import json
import logging
from typing import Any

from sqlalchemy import text

from ats_agency_service import get_or_create_ats_agency
from app.job_ingestion.normalize import UNKNOWN_EXPERIENCE_LEVEL, _valid_http_url, parse_ats_datetime

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

    job = _persistence_safe_job(job)
    ats_type = job["ats_type"]
    ats_job_id = str(job.get("ats_job_id") or "").strip()

    if not ats_type:
        raise ValueError("ATS job is missing ats_type")

    if not ats_job_id:
        raise ValueError("ATS job is missing ats_job_id")

    # Check whether this ATS job already exists.
    result = await db.execute(
        text("""
            SELECT id, job_url, description
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
        existing_description = existing[2] or ""
        incoming_job_url = _valid_http_url(job.get("job_url"))
        incoming_description = str(job.get("description") or "").strip()
        # Refresh only when the newly collected JD contains strictly more
        # content.  This repairs historical rows that were saved from Lever's
        # overview-only field without allowing a partial provider response to
        # erase an already richer JD.
        refresh_description = len(incoming_description) > len(existing_description.strip())

        # ATS metadata is refreshed on every sync.  COALESCE prevents an
        # incomplete public-board response from erasing prior data, while
        # allowing historical empty rows to be backfilled.
        await db.execute(
            text("""
                UPDATE job_descriptions
                SET job_url = CASE WHEN :is_global_provider AND :job_url IS NOT NULL THEN :job_url ELSE COALESCE(job_url, :job_url) END,
                    description = CASE WHEN :refresh_description THEN :description ELSE description END,
                    employment_type = COALESCE(:employment_type, employment_type),
                    remote_policy = COALESCE(:remote_policy, remote_policy),
                    experience_level = COALESCE(:experience_level, experience_level),
                    experience_required = COALESCE(:experience_required, experience_required),
                    salary_range = COALESCE(:salary_range, salary_range),
                    skills_required = CASE WHEN :skills_required IS NOT NULL THEN CAST(:skills_required AS json) ELSE skills_required END,
                    skills = CASE WHEN :skills IS NOT NULL THEN CAST(:skills AS json) ELSE skills END,
                    structured_data = CASE WHEN :structured_data IS NOT NULL THEN CAST(:structured_data AS json) ELSE structured_data END,
                    created_at = COALESCE(:created_at, created_at),
                    is_active = TRUE, status = 'active', job_status = 'active',
                    updated_at = NOW(), last_synced_at = NOW()
                WHERE id = :id
            """),
            {
                "id": job_id, "job_url": incoming_job_url, "description": incoming_description,
                "is_global_provider": ats_type == "fantastic",
                "refresh_description": refresh_description, **_metadata_params(job),
            },
        )
        await db.commit()
        if (not (existing_job_url or "").strip() and incoming_job_url is not None) or refresh_description:
            logger.debug(
                "[job-scheduler] Refreshed existing ATS job ats_type=%s ats_job_id=%s db_id=%s description_refreshed=%s",
                ats_type, ats_job_id, job_id,
                refresh_description,
            )
        # Recreate a point that may have been removed while the job was closed.
        # URL-less jobs deliberately remain out of Qdrant.
        if _valid_http_url(existing_job_url) or incoming_job_url:
            try:
                from app.job_ingestion.embedding_service import generate_job_embedding
                from app.job_ingestion.qdrant_service import ensure_collection, upsert_job_embedding
                ensure_collection()
                upsert_job_embedding(str(job_id), generate_job_embedding(job), job)
            except Exception as exc:
                logger.error("Qdrant embedding upsert failed for reactivated job_id=%s: %s", job_id, exc)
        return str(job_id)

    # Fantastic is a global provider: it intentionally has neither a
    # company_registry row nor a company-scoped ATS agency.  In particular,
    # do not delegate it to get_or_create_ats_agency(), whose allow-list is
    # intentionally limited to the company-scoped ATS integrations.
    is_global_provider = ats_type == "fantastic"
    agency_id = None
    if not is_global_provider:
        # Get the default system agency for this company-scoped ATS.
        agency_id = await get_or_create_ats_agency(
            db,
            ats_type,
        )

    # Resolve the active company_registry record only for company-scoped ATSs.
    cr_row = None
    if not is_global_provider:
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
                "company_name": job["company_name"],
                "ats_type": ats_type,
            },
        )
        cr_row = cr_result.first()
    if cr_row is None and not is_global_provider:
        raise ValueError(
            f"No active company_registry record found for "
            f"company_name={job.get('company_name')!r}, ats_type={ats_type!r}"
        )
    company_registry_id = cr_row[0] if cr_row is not None else None

    # New ATS job.
    result = await db.execute(
        text("""
            INSERT INTO job_descriptions (
                title,
                company_name,
                department,
                location,
                employment_type,
                experience_required,
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
                skills,
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
                :experience_required,
                :salary_range,
                :description,
                TRUE,
                'active',
                COALESCE(:created_at, NOW()),
                NOW(),
                gen_random_uuid(),
                :agency_id,
                :company_registry_id,
                'PONTIS',
                'PONTIS',
                'ui',
                'active',
                'volume',
                CAST(:skills_required AS json),
                CAST(:skills AS json),
                :experience_level,
                CAST(:structured_data AS json),
                :remote_policy,
                :ats_job_id,
                :ats_type,
                :job_url,
                NOW()
            )
            RETURNING id
        """),
        {
            "title": job["title"],
            "company_name": job["company_name"],
            "department": job.get("department"),
            "location": job.get("location"),
            "employment_type": job.get("employment_type"),
            "salary_range": job.get("salary_range"),
            "description": job["description"],
            "agency_id": agency_id,
            "company_registry_id": company_registry_id,
            "ats_job_id": ats_job_id,
            "ats_type": ats_type,
            "job_url": _valid_http_url(job.get("job_url")),
            **_metadata_params(job),
        },
    )

    new_id = result.scalar_one()

    await db.commit()

    # A malformed/no-URL ATS job is retained for audit but never indexed.
    if _valid_http_url(job.get("job_url")):
      try:
        from app.job_ingestion.embedding_service import generate_job_embedding
        from app.job_ingestion.qdrant_service import ensure_collection, upsert_job_embedding

        ensure_collection()
        upsert_job_embedding(str(new_id), generate_job_embedding(job), job)
      except Exception as exc:
        logger.error("Qdrant embedding upsert failed for job_id=%s: %s", new_id, exc)

    return str(new_id)


def _metadata_params(job: dict[str, Any]) -> dict[str, Any]:
    """Serialize optional normalized JSON consistently for PostgreSQL binds."""
    # Normalizers use [] for an ATS job whose skills cannot be stated from
    # provider metadata or the JD.  Keep this guard for older/direct callers
    # so a new ATS insert can never bind SQL NULL to the required JSON column.
    safe = _persistence_safe_job(job)
    return {
        "employment_type": safe.get("employment_type"),
        "remote_policy": safe.get("remote_policy"),
        "experience_level": safe["experience_level"],
        "experience_required": safe.get("experience_required"),
        "salary_range": safe.get("salary_range"),
        "skills_required": json.dumps(safe["skills_required"]),
        "skills": json.dumps(safe["skills"]),
        "structured_data": json.dumps(safe["structured_data"]),
        # Defensive coercion also covers jobs normalized by older deployments
        # that stored ISO timestamps as strings.
        "created_at": parse_ats_datetime(safe.get("created_at")),
    }


def _persistence_safe_job(job: dict[str, Any]) -> dict[str, Any]:
    """Enforce the ATS insert contract immediately before SQL binding.

    Normalizers are intentionally permissive because public boards omit many
    fields.  This is the single defensive boundary for normalizer upgrades,
    old queued payloads, and direct callers; JSON values are serialized here,
    not delegated to PostgreSQL's implicit casts.
    """
    if not isinstance(job, dict):
        raise ValueError("ATS job must be a mapping")
    safe = dict(job)
    def text_value(key: str, fallback: str | None = None) -> str | None:
        value = safe.get(key)
        if value is None:
            return fallback
        value = str(value).strip()
        return value or fallback
    safe["ats_type"] = (text_value("ats_type") or "").lower()
    safe["ats_job_id"] = text_value("ats_job_id") or ""
    # These are core persisted text fields.  Empty description/title are an
    # honest representation of an incomplete board response, unlike invented
    # employment or seniority data.
    safe["title"] = text_value("title", "Untitled ATS job")
    safe["company_name"] = text_value("company_name", "Unknown company")
    safe["description"] = text_value("description", "")
    for key in ("department", "location", "employment_type", "remote_policy", "experience_required", "salary_range"):
        safe[key] = text_value(key)
    safe["experience_level"] = text_value("experience_level") or safe["experience_required"] or UNKNOWN_EXPERIENCE_LEVEL
    for key in ("skills_required", "skills"):
        value = safe.get(key)
        if isinstance(value, str):
            value = [part.strip() for part in value.replace(";", ",").split(",")]
        safe[key] = [part.strip() for part in value if isinstance(part, str) and part.strip()] if isinstance(value, (list, tuple, set)) else []
    structured = safe.get("structured_data")
    if not isinstance(structured, (dict, list)):
        structured = {}
    try:
        # Fail early with a clear per-job reason for unsupported metadata
        # rather than letting asyncpg abort the company transaction.
        json.dumps(structured)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"ATS structured_data is not JSON-compatible: {exc}") from exc
    safe["structured_data"] = structured
    safe["created_at"] = parse_ats_datetime(safe.get("created_at"))
    safe["job_url"] = _valid_http_url(safe.get("job_url"))
    return safe
