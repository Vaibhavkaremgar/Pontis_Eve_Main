"""Ingestion-time fallback for skills not exposed as ATS metadata."""
import json
import logging
import os
from typing import Any

from app.job_ingestion.normalize import _normalize_skill_values, _html_text

logger = logging.getLogger(__name__)

_SYSTEM = (
    "Extract only concise professional or technical skills explicitly required or preferred "
    "by the job description. Return JSON only as {\"skills\":[\"skill\"]}. "
    "Never return sentences, duties, marketing text, salary, location, education, or generic "
    "soft skills unless clearly a named job competency. Deduplicate the list."
)

async def extract_missing_job_skills(job: dict[str, Any]) -> dict[str, Any]:
    """Shared semantic extraction for every ATS; native values are fallback only."""
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
        messages=[{"role": "system", "content": _SYSTEM + " Also return experience_required, salary_range, and employment_type."}, {"role": "user", "content": json.dumps({"title": job.get("title"), "description": job.get("description"), "requirements": job.get("requirements"), "responsibilities": job.get("responsibilities"), "native_metadata": {k: job.get(k) for k in ("skills", "skills_required", "salary_range", "employment_type", "location", "structured_data")}}, default=str)}],
        )
        payload = json.loads(response.choices[0].message.content or "{}")
        if not isinstance(payload, dict):
            raise ValueError("LLM returned non-object JSON")
        skills = _normalize_skill_values(payload.get("skills"))
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
            job["structured_data"]["llm_extraction"] = {"skills": skills, "experience_required": job.get("experience_required"), "salary_range": job.get("salary_range"), "employment_type": job.get("employment_type")}
    except Exception as exc:
        logger.warning("[job-skills] LLM extraction failed for ats_job_id=%s: %s", job.get("ats_job_id"), exc)
    return job
