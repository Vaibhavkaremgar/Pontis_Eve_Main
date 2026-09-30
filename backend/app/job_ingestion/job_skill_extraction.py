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
    """Populate missing normalized skills once during ingestion, never per candidate."""
    if job.get("skills_required") or job.get("skills"):
        return job
    jd = _html_text("\n".join(str(job.get(key) or "") for key in ("title", "description", "requirements")))
    if not jd.strip() or not any(os.environ.get(key, "").strip() for key in ("GROQ_API_KEY_1", "GROQ_API_KEY")):
        return job
    try:
        from groq_client import GroqClientPool
        client = GroqClientPool()
        response = await client.chat.completions.create(
            model=os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile"),
            temperature=0,
            response_format={"type": "json_object"},
            messages=[{"role": "system", "content": _SYSTEM}, {"role": "user", "content": jd}],
        )
        payload = json.loads(response.choices[0].message.content or "{}")
        skills = _normalize_skill_values(payload.get("skills") if isinstance(payload, dict) else [])
        job["skills_required"] = skills
        job["skills"] = skills
    except Exception as exc:
        logger.warning("[job-skills] LLM extraction failed for ats_job_id=%s: %s", job.get("ats_job_id"), exc)
    return job
