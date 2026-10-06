"""Ingestion-time fallback for skills not exposed as ATS metadata."""
import json
import logging
import os
from typing import Any

from app.job_ingestion.normalize import _normalize_skill_values, _html_text
from skill_normalization import merge_skills, canonical_skill_key
from groq_client import AllKeysRateLimitedError

logger = logging.getLogger(__name__)

_SYSTEM = (
    "Extract job metadata and return exactly one JSON object with these keys: "
    "skills (array of strings), experience_required (string or null), "
    "salary_range (string or null), employment_type (string or null). "
    "Extract only concise professional or technical skills explicitly required or preferred "
    "Never return sentences, duties, marketing text, salary, location, education, or generic "
    "soft skills unless clearly a named job competency. Deduplicate the list."
)

async def extract_missing_job_skills(job: dict[str, Any]) -> dict[str, Any]:
    """Monotonically enrich provider skills with optional semantic extraction."""
    original = merge_skills(job.get("skills_required"), job.get("skills"))
    jd = _html_text("\n".join(str(job.get(key) or "") for key in ("title", "description", "requirements", "responsibilities")))
    if not jd.strip():
        return job
    try:
        from groq_client import GroqClientPool
        client = GroqClientPool()
        response = await client.chat.completions.create(
            model=os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile"),
            temperature=0,
            response_format={"type": "json_object"},
        messages=[{"role": "system", "content": _SYSTEM}, {"role": "user", "content": json.dumps({"title": job.get("title"), "description": job.get("description"), "requirements": job.get("requirements"), "responsibilities": job.get("responsibilities"), "native_metadata": {k: job.get(k) for k in ("skills", "skills_required", "salary_range", "employment_type", "location", "structured_data")}}, default=str)}],
        )
        payload = json.loads(response.choices[0].message.content or "{}")
        if not isinstance(payload, dict):
            raise ValueError("LLM returned non-object JSON")
        llm_skills = _normalize_skill_values(payload.get("skills"))
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
