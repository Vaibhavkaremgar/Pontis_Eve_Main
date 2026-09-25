"""Conservative normalization for public ATS job-board payloads."""
import html
import re
from datetime import date, datetime, time, timezone
from typing import Any
from urllib.parse import urlsplit


def _valid_http_url(value: Any) -> str | None:
    if not isinstance(value, str): return None
    url = value.strip(); parsed = urlsplit(url)
    return url if parsed.scheme.lower() in {"http", "https"} and parsed.netloc else None

def _text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None

def _first(*values: Any) -> Any:
    return next((v for v in values if v not in (None, "", [], {})), None)

def parse_ats_datetime(value: Any) -> datetime | None:
    """Return an UTC-aware datetime from public ATS date representations.

    PostgreSQL's asyncpg driver validates Python bind values before applying
    SQL casts.  Keep dates as datetime objects here rather than serializing
    them to ISO strings, so the value is safe to bind to ``timestamptz``.
    """
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    if isinstance(value, date):
        return datetime.combine(value, time.min, tzinfo=timezone.utc)
    if isinstance(value, (int, float)) and value > 946684800000:
        try:
            return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            # ``fromisoformat`` accepts offsets and fractional seconds.  Python
            # versions before 3.11 do not consistently accept a trailing Z.
            parsed = datetime.fromisoformat(
                f"{raw[:-1]}+00:00" if raw.endswith(("Z", "z")) else raw
            )
        except (TypeError, ValueError, OverflowError):
            return None
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
    return None


# Kept as a local compatibility alias for callers that imported the former
# helper.  New code should use the explicit, shared parser above.
_date = parse_ats_datetime

def _html_text(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"</?(?:p|li|ul|ol|h[1-6]|br)\b[^>]*>", "\n", text, flags=re.I)
    return re.sub(r"<[^>]+>", "", text)

def _jd_evidence(description: Any) -> dict[str, Any]:
    """Extract only values stated under an explicit label in the JD."""
    text, found = _html_text(description), {}
    patterns = {
        "employment_type": r"(?im)^\s*(?:employment|job)\s*type\s*[:\-]\s*(full[ -]?time|part[ -]?time|contract|temporary|internship)\b",
        "remote_policy": r"(?im)^\s*(?:workplace|work location|remote policy)\s*[:\-]\s*(remote|hybrid|on[ -]?site)\b",
        "experience_level": r"(?im)^\s*(?:experience level|seniority|senior level)\s*[:\-]\s*([^\n]{2,80})",
        "experience_required": r"(?im)^\s*(?:experience required|required experience)\s*[:\-]\s*([^\n]{2,100})",
        "salary_range": r"(?im)^\s*(?:salary range|salary|compensation)\s*[:\-]\s*([^\n]{3,100})",
        "skills_required": r"(?im)^\s*(?:required skills|technical skills|skills required)\s*[:\-]\s*([^\n]{2,300})",
    }
    for key, pattern in patterns.items():
        if match := re.search(pattern, text):
            value = match.group(1).strip(" .;")
            found[key] = [v.strip() for v in re.split(r"[,;/|]", value) if v.strip()] if key == "skills_required" else value
    return found

def _metadata(job: dict[str, Any], source: str, *, description: Any, **explicit: Any) -> dict[str, Any]:
    evidence = _jd_evidence(description)
    metadata_keys = ("employment_type", "remote_policy", "experience_level", "experience_required", "salary_range", "skills_required", "created_at")
    result = {key: _first(explicit.get(key), evidence.get(key)) for key in metadata_keys}
    result["skills"] = result.get("skills_required")
    categories = job.get("categories") if isinstance(job.get("categories"), dict) else {}
    source_data = {k: v for k, v in {
        "requisition_id": _first(job.get("requisition_id"), job.get("requisitionId"), job.get("requisition")),
        "workplace_type": _first(job.get("workplaceType"), job.get("workplace_type")),
        "all_locations": categories.get("allLocations"),
        "departments": job.get("departments"), "offices": job.get("offices"),
        "team": job.get("team"), "compensation": job.get("compensation"),
        "metadata": job.get("metadata"), "custom_fields": job.get("customFields"),
        "location_details": job.get("location") if isinstance(job.get("location"), dict) else None,
        "workable_code": _first(job.get("shortcode"), job.get("code")),
    }.items() if v not in (None, "", [], {})}
    result["structured_data"] = {"source": source, **source_data}
    return result

def extract_lever_job_url(job: dict[str, Any]) -> str | None:
    urls = job.get("urls") if isinstance(job.get("urls"), dict) else {}
    return next((url for value in (urls.get("show"), job.get("hostedUrl"), urls.get("apply")) if (url := _valid_http_url(value))), None)

def extract_ashby_job_url(job: dict[str, Any]) -> str | None:
    return next((url for value in (job.get("jobUrl"), job.get("applyUrl")) if (url := _valid_http_url(value))), None)

def _lever_description(job: dict[str, Any]) -> str | None:
    parts = [_text(job.get("descriptionPlain")) or ""]
    for section in job.get("lists") or []:
        if isinstance(section, dict): parts.extend(filter(None, [_text(section.get("text")), _text(_html_text(section.get("content")))]))
    return "\n".join(parts) or None

def normalize_greenhouse(job: dict[str, Any], company_name: str) -> dict[str, Any]:
    description = job.get("content")
    meta = _metadata(job, "greenhouse", description=description, remote_policy=_first(job.get("remote_policy"), job.get("workplace_type")), created_at=parse_ats_datetime(_first(job.get("created_at"), job.get("updated_at"))))
    departments = job.get("departments") or []
    return {"ats_job_id": str(job.get("id")), "company_name": company_name, "title": job.get("title"), "description": description, "department": departments[0].get("name") if departments and isinstance(departments[0], dict) else None, "location": (job.get("location") or {}).get("name") if isinstance(job.get("location"), dict) else None, "employment_type": meta.get("employment_type"), "salary_range": meta.get("salary_range"), "job_url": job.get("absolute_url"), "ats_type": "greenhouse", **meta}

def normalize_lever(job: dict[str, Any], company_name: str) -> dict[str, Any]:
    description = _lever_description(job); categories = job.get("categories") if isinstance(job.get("categories"), dict) else {}
    meta = _metadata(job, "lever", description=description, employment_type=categories.get("commitment"), remote_policy=_first(job.get("workplaceType"), categories.get("workplace")), created_at=parse_ats_datetime(_first(job.get("createdAt"), job.get("created_at"))), skills_required=_first(job.get("skills"), job.get("skillsRequired")))
    return {"ats_job_id": str(job.get("id")), "company_name": company_name, "title": job.get("text"), "description": description, "department": categories.get("team"), "location": categories.get("location"), "salary_range": meta.get("salary_range"), "job_url": extract_lever_job_url(job), "ats_type": "lever", **meta}

def normalize_ashby(job: dict[str, Any], company_name: str) -> dict[str, Any]:
    description = job.get("descriptionHtml"); compensation = job.get("compensation") if isinstance(job.get("compensation"), dict) else {}
    salary = _first(job.get("salaryRange"), job.get("salary_range"), compensation.get("summary"))
    if not salary and compensation.get("minValue") is not None and compensation.get("maxValue") is not None: salary = f"{compensation.get('currency') or ''} {compensation['minValue']} - {compensation['maxValue']} {compensation.get('interval') or ''}".strip()
    meta = _metadata(job, "ashby", description=description, employment_type=_first(job.get("employmentType"), job.get("employment_type")), remote_policy=_first(job.get("workplaceType"), "Remote" if job.get("isRemote") is True else None), salary_range=salary, created_at=parse_ats_datetime(_first(job.get("publishedAt"), job.get("createdAt"))), skills_required=_first(job.get("skills"), job.get("skillsRequired")))
    department = job.get("department") if isinstance(job.get("department"), dict) else {}
    return {"ats_job_id": str(job.get("id")), "company_name": company_name, "title": job.get("title"), "description": description, "department": department.get("name"), "location": job.get("location") if isinstance(job.get("location"), str) else None, "salary_range": meta.get("salary_range"), "job_url": extract_ashby_job_url(job), "ats_type": "ashby", **meta}

def normalize_workable(job: dict[str, Any], company_name: str) -> dict[str, Any]:
    description = job.get("description"); location = job.get("location")
    meta = _metadata(job, "workable", description=description, employment_type=job.get("employment_type"), remote_policy=_first(job.get("remote_policy"), job.get("workplace_type"), "Remote" if isinstance(location, dict) and location.get("telecommuting") else None), salary_range=_first(job.get("salary_range"), job.get("salary")), created_at=parse_ats_datetime(_first(job.get("published_on"), job.get("published_at"), job.get("created_at"))), skills_required=_first(job.get("skills"), job.get("skills_required")))
    return {"ats_job_id": str(job.get("id")), "company_name": company_name, "title": job.get("title"), "description": description, "department": job.get("department"), "location": location.get("city") if isinstance(location, dict) else location, "salary_range": meta.get("salary_range"), "job_url": job.get("url"), "ats_type": "workable", **meta}
