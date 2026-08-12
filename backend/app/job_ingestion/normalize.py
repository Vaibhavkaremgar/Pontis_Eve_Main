from typing import Any
def normalize_greenhouse(
        job: dict[str, Any],
        company_name: str,
) -> dict[str, Any]:

    return {

        "ats_job_id": str(job.get("id")),

        "company_name": company_name,

        "title": job.get("title"),

        "description": job.get("content"),

        "department": (
            job.get("departments", [{}])[0].get("name")
            if job.get("departments")
            else None
        ),

        "location": (
            job.get("location", {}).get("name")
            if job.get("location")
            else None
        ),

        "employment_type": None,

        "salary_range": None,

        "job_url": job.get("absolute_url"),

        "ats_type": "greenhouse",
    }


def normalize_lever(
        job: dict[str, Any],
        company_name: str,
) -> dict[str, Any]:

    return {

        "ats_job_id": str(job.get("id")),

        "company_name": company_name,

        "title": job.get("text"),

        "description": job.get("descriptionPlain"),

        "department": (
            job.get("categories", {}).get("team")
            if job.get("categories")
            else None
        ),

        "location": (
            job.get("categories", {}).get("location")
            if job.get("categories")
            else None
        ),

        "employment_type": (
            job.get("categories", {}).get("commitment")
            if job.get("categories")
            else None
        ),

        "salary_range": None,

        "job_url": job.get("hostedUrl"),

        "ats_type": "lever",
    }


def normalize_ashby(
        job: dict[str, Any],
        company_name: str,
) -> dict[str, Any]:

    return {

        "ats_job_id": str(job.get("id")),

        "company_name": company_name,

        "title": job.get("title"),

        "description": job.get("descriptionHtml"),

        "department": (
            job.get("department", {}).get("name")
            if isinstance(job.get("department"), dict)
            else None
        ),

        "location": (
            job.get("location")
            if isinstance(job.get("location"), str)
            else None
        ),

        "employment_type": None,

        "salary_range": None,

        "job_url": job.get("jobUrl"),

        "ats_type": "ashby",
    }
def normalize_workable(
    job: dict[str, Any],
    company_name: str,
) -> dict[str, Any]:
    return {
        "ats_job_id": str(job.get("id")),
        "company_name": company_name,
        "title": job.get("title"),
        "description": job.get("description"),
        "department": job.get("department"),
        "location": job.get("location"),
        "employment_type": job.get("employment_type"),
        "salary_range": None,
        "job_url": job.get("url"),
        "ats_type": "workable",
    }