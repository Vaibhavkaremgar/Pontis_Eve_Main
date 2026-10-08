"""Ingestion-time fallback for skills not exposed as ATS metadata."""
import json
import logging
import os
import asyncio
import re
from typing import Any

from app.job_ingestion.normalize import _normalize_skill_values, _html_text
from skill_normalization import merge_skills, canonical_skill_key
from groq_client import AllKeysRateLimitedError

logger = logging.getLogger(__name__)

_SYSTEM = (
    "Extract job metadata and return exactly one JSON object with these keys: "
    "skills (array of strings), experience_required (string or null), "
    "salary_range (string or null), employment_type (string or null). "
    "Extract only concise candidate-relevant skills or capabilities explicitly required or preferred. "
    "Include technologies, tools, methodologies, professional capabilities, and genuine soft skills. "
    "Never return company names, job titles, salary, location, benefits, employment type, years of "
    "experience, marketing language, business outcomes, or generic industry/domain labels. "
    "A product is valid only when a candidate can use it as a tool or platform. Return canonical names "
    "and avoid overlapping duplicates. Do not return sentences or duties."
)

_EXTRACTION_CONCURRENCY = max(1, int(os.getenv("JOB_SKILL_EXTRACTION_CONCURRENCY", "2")))
_EXTRACTION_SEMAPHORE = asyncio.Semaphore(_EXTRACTION_CONCURRENCY)
_FIELD_LIMITS = {"title": 500, "description": 12000, "requirements": 8000, "responsibilities": 8000}

_NON_SKILL_PATTERNS = (
    re.compile(r"^(?:remote|hybrid|on[- ]site)\s+(?:work|role|position)$", re.I),
    re.compile(r"^(?:esop|lta|salary|compensation|benefits?)$", re.I),
    re.compile(r"^(?:financial|healthcare|manufacturing|hospitality|education|infrastructure|energy)\s+(?:domain|sector|industry)$", re.I),
)
_DOMAIN_ONLY = {"healthcare", "manufacturing", "hospitality", "infrastructure", "education", "energy", "finance", "financial"}


def _clean_extracted_skills(values: Any, job: dict[str, Any]) -> list[str]:
    """Conservatively remove metadata/title/company contamination from skills."""
    title = " ".join(str(job.get(key) or "") for key in ("title", "job_title"))
    company = " ".join(str(job.get(key) or "") for key in ("company_name", "company", "organization"))
    excluded = {canonical_skill_key(title), canonical_skill_key(company)} - {""}
    cleaned, seen = [], set()
    for value in _normalize_skill_values(values):
        text = " ".join(str(value).split()).strip(" .,:;-")
        if not text or canonical_skill_key(text) in excluded:
            continue
        if any(pattern.fullmatch(text) for pattern in _NON_SKILL_PATTERNS) or text.casefold() in _DOMAIN_ONLY:
            continue
        folded = re.sub(r"\s+", " ", text.casefold())
        if folded == "gis technology":
            text = "GIS"
        elif folded in {"bim modelling", "bim modeling"}:
            text = "BIM"
        elif folded == "revit mep":
            text = "Autodesk Revit MEP"
        key = canonical_skill_key(text)
        if key and key not in seen:
            seen.add(key)
            cleaned.append(text)
    return cleaned


def _bounded_text(value: Any, limit: int) -> str:
    text = _html_text(str(value or "")).strip()
    return text[:limit]


def _extraction_input(job: dict[str, Any]) -> dict[str, Any]:
    """Build a deterministic, bounded request without truncating JSON."""
    return {
        key: _bounded_text(job.get(key), limit)
        for key, limit in _FIELD_LIMITS.items()
    } | {
        "native_metadata": {
            key: job.get(key)
            for key in ("skills", "skills_required", "salary_range", "employment_type", "location")
            if job.get(key) is not None
        }
    }

async def extract_missing_job_skills(job: dict[str, Any]) -> dict[str, Any]:
    """Monotonically enrich provider skills with optional semantic extraction."""
    original = _clean_extracted_skills(merge_skills(job.get("skills_required"), job.get("skills")), job)
    jd = _html_text("\n".join(str(job.get(key) or "") for key in ("title", "description", "requirements", "responsibilities")))
    if not jd.strip():
        return job
    # Provider metadata is already sufficient for this optional fallback. A
    # pending retry is the sole exception: it represents a prior unavailable
    # attempt and must be allowed to recover.
    status = (job.get("structured_data") or {}).get("metadata_extraction_status") if isinstance(job.get("structured_data"), dict) else None
    if status == "complete" or (job.get("skills") and not job.get("skills_required")):
        return job
    try:
        from groq_client import GroqClientPool
        client = GroqClientPool()
        model = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
        async with _EXTRACTION_SEMAPHORE:
            response = await client.chat.completions.create(
                model=model,
                temperature=0,
                response_format={"type": "json_object"},
                _telemetry={"workflow": "job_skill_extraction", "function_name": "extract_missing_job_skills", "job_id": job.get("id") or job.get("job_id"), "model": model},
                messages=[{"role": "system", "content": _SYSTEM}, {"role": "user", "content": json.dumps(_extraction_input(job), default=str)}],
            )
        payload = json.loads(response.choices[0].message.content or "{}")
        if not isinstance(payload, dict):
            raise ValueError("LLM returned non-object JSON")
        llm_skills = _clean_extracted_skills(payload.get("skills"), job)
        skills = merge_skills(original, llm_skills, canonical_display=True)
        if skills:
            job["skills_required"] = skills
            job["skills"] = skills
        exp = payload.get("experience_required")
        if isinstance(exp, str) and exp.strip():
            job["experience_required"] = exp.strip()
        elif exp is None and not job.get("experience_required"):
            job["experience_required"] = None
        if not job.get("salary_range") and isinstance(payload.get("salary_range"), str) and payload["salary_range"].strip():
            job["salary_range"] = payload["salary_range"].strip()
        if not job.get("employment_type") and isinstance(payload.get("employment_type"), str) and payload["employment_type"].strip():
            job["employment_type"] = payload["employment_type"].strip()
        if isinstance(job.get("structured_data"), dict):
            provenance: dict[str, list[str]] = {}
            for source_name, values in (("provider", original), ("llm", llm_skills)):
                for value in values:
                    key = canonical_skill_key(value)
                    if key:
                        provenance.setdefault(key, []).append(source_name)
            job["structured_data"]["llm_extraction"] = {"skills": llm_skills, "experience_required": job.get("experience_required"), "salary_range": job.get("salary_range"), "employment_type": job.get("employment_type")}
            job["structured_data"]["skill_extraction"] = {
                "provider_skills": original, "llm_skills": llm_skills,
                "final_skills": skills, "provenance": provenance,
            }
            job["structured_data"]["metadata_extraction_status"] = "complete"
    except (AllKeysRateLimitedError, RuntimeError) as exc:
        # This is retryable, not an empty extraction. Keep every provider value
        # untouched so persistence cannot erase metadata while keys recover.
        if isinstance(job.get("structured_data"), dict):
            job["structured_data"]["metadata_extraction_status"] = "pending_retry"
        job["metadata_extraction_status"] = "pending_retry"
        logger.warning("[job-skills] Groq unavailable for ats_job_id=%s; queued for retry: %s", job.get("ats_job_id"), exc)
    except Exception as exc:
        logger.warning("[job-skills] LLM extraction failed for ats_job_id=%s: %s", job.get("ats_job_id"), exc)
        if original:
            job["skills_required"] = original
            job["skills"] = original
    return job
