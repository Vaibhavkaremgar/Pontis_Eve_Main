import html
import re
from typing import Any
from urllib.parse import urlsplit


def _valid_http_url(value: Any) -> str | None:
    """Return a trimmed HTTP(S) URL, or None for an unusable ATS value."""
    if not isinstance(value, str):
        return None

    url = value.strip()
    parsed = urlsplit(url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return None
    return url


def extract_lever_job_url(job: dict[str, Any]) -> str | None:
    """Extract Lever's job-specific hosted/show/apply URL without constructing one."""
    urls = job.get("urls")
    urls = urls if isinstance(urls, dict) else {}
    for candidate in (urls.get("show"), job.get("hostedUrl"), urls.get("apply")):
        url = _valid_http_url(candidate)
        if url:
            return url
    return None


def extract_ashby_job_url(job: dict[str, Any]) -> str | None:
    """Extract Ashby's job-specific job/apply URL without constructing one."""
    for candidate in (job.get("jobUrl"), job.get("applyUrl")):
        url = _valid_http_url(candidate)
        if url:
            return url
    return None


def _lever_description(job: dict[str, Any]) -> str | None:
    """Preserve Lever's overview *and* labelled JD lists.

    Lever separates the opening prose in ``descriptionPlain`` from sections
    such as qualifications in ``lists``.  Persisting only the former makes a
    complete job look like it has no requirements downstream.
    """
    parts = [str(job.get("descriptionPlain") or "").strip()]
    for section in job.get("lists") or []:
        if not isinstance(section, dict):
            continue
        heading = str(section.get("text") or "").strip()
        content = html.unescape(str(section.get("content") or ""))
        content = re.sub(r"</?(?:p|li|ul|ol|h[1-6]|br)\b[^>]*>", "\n", content, flags=re.I)
        content = re.sub(r"<[^>]+>", "", content).strip()
        if heading:
            parts.append(heading)
        if content:
            parts.append(content)
    description = "\n".join(part for part in parts if part)
    return description or None


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

        "description": _lever_description(job),

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

        "job_url": extract_lever_job_url(job),

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

        "job_url": extract_ashby_job_url(job),

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
