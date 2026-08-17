import json
from typing import Any, Dict


def build_candidate_text(candidate: Dict[str, Any]) -> str:
    """
    Build structured semantic text from a candidate DB row for embedding.
    Mirrors Adam's candidate text format for compatibility with the shared vector space.
    """
    raw_data = candidate.get("raw_data") or {}
    if isinstance(raw_data, str):
        try:
            raw_data = json.loads(raw_data)
        except Exception:
            raw_data = {}

    parts = []

    current_role = (candidate.get("current_role") or "").strip()
    if current_role:
        parts.append(f"Candidate Role:\n{current_role}")

    current_company = (candidate.get("current_company") or "").strip()
    if current_company:
        parts.append(f"Current Company:\n{current_company}")

    location = (candidate.get("location") or "").strip()
    if location:
        parts.append(f"Location:\n{location}")

    exp_years = candidate.get("experience_years") or candidate.get("total_experience_years")
    if exp_years is not None:
        parts.append(f"Experience:\n{exp_years} years of professional experience")

    skills_raw = candidate.get("skills") or []
    if isinstance(skills_raw, str):
        try:
            skills_raw = json.loads(skills_raw)
        except Exception:
            skills_raw = []
    skill_names = [s["name"] if isinstance(s, dict) else str(s) for s in skills_raw if s]
    if skill_names:
        parts.append(f"Skills:\n{', '.join(skill_names)}")

    work_exp = candidate.get("work_experience") or []
    if isinstance(work_exp, str):
        try:
            work_exp = json.loads(work_exp)
        except Exception:
            work_exp = []
    if work_exp:
        lines = []
        for w in work_exp:
            title = w.get("title", "")
            company = w.get("company", "")
            desc = w.get("description", "")
            entry = " at ".join(filter(None, [title, company]))
            if desc:
                entry = f"{entry}: {desc}"
            if entry.strip():
                lines.append(entry.strip())
        if lines:
            parts.append("Work Experience:\n" + "\n".join(lines))

    education = candidate.get("education") or []
    if isinstance(education, str):
        try:
            education = json.loads(education)
        except Exception:
            education = []
    if education:
        lines = []
        for e in education:
            degree = e.get("degree", "")
            institution = e.get("institution", "")
            entry = " at ".join(filter(None, [degree, institution]))
            if entry.strip():
                lines.append(entry.strip())
        if lines:
            parts.append("Education:\n" + "\n".join(lines))

    preferred_roles = raw_data.get("preferred_roles") or []
    if preferred_roles:
        parts.append(f"Target / Preferred Roles:\n{', '.join(preferred_roles)}")

    certifications = raw_data.get("certifications") or []
    if certifications:
        parts.append(f"Certifications:\n{', '.join(certifications)}")

    summary = (candidate.get("summary") or "").strip()
    if summary:
        parts.append(f"Summary:\n{summary}")

    additional = (raw_data.get("additional_information") or "").strip()
    if additional:
        parts.append(f"Additional Information:\n{additional}")

    # Use parsed_resume_text if available, else resume_text
    resume_text = (
        candidate.get("parsed_resume_text")
        or candidate.get("resume_text")
        or ""
    ).strip()
    if resume_text:
        parts.append(f"Resume:\n{resume_text[:2000]}")

    return "\n\n".join(parts)
