import json
from typing import Any, Dict

import re


_CERTIFICATION_BOILERPLATE_WORDS = {
    "cert",
    "certificate",
    "certificates",
    "certification",
    "certifications",
    "certified",
    "course",
    "courses",
    "credential",
    "credentials",
    "training",
}


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def _normalize_key(value: Any) -> str:
    return _normalize_text(value).lower()


def _relaxed_cert_key(value: Any) -> str:
    stripped = re.sub(r"[^\w\s]+", " ", _normalize_key(value))
    tokens = [token for token in stripped.split() if token not in _CERTIFICATION_BOILERPLATE_WORDS]
    return " ".join(tokens) or stripped


def _normalize_certifications(certifications: Any) -> list[str]:
    if not isinstance(certifications, list):
        return []
    normalized: list[str] = []
    seen_strict: set[str] = set()
    seen_relaxed: set[str] = set()
    for cert in certifications:
        cleaned = _normalize_text(cert)
        if not cleaned:
            continue
        strict_key = _normalize_key(cleaned)
        relaxed_key = _relaxed_cert_key(cleaned)
        if strict_key in seen_strict or relaxed_key in seen_relaxed:
            continue
        seen_strict.add(strict_key)
        seen_relaxed.add(relaxed_key)
        normalized.append(cleaned)
    return normalized


def _normalize_skills(skills: Any, certifications: Any = None) -> list[str]:
    if not isinstance(skills, list):
        return []

    normalized_certs = _normalize_certifications(certifications or [])
    normalized: list[str] = []
    seen: set[str] = set()

    for skill in skills:
        cleaned = _normalize_text(skill.get("name") if isinstance(skill, dict) else skill)
        if not cleaned:
            continue
        key = _normalize_key(cleaned)
        if key in seen:
            continue
        if any(_normalize_key(cert) == key for cert in normalized_certs) and (
            re.search(r"\b(?:cert|certificate|certificates|certification|certifications|certified|course|courses|credential|credentials|training|license|licence)\b", cleaned, re.I)
            or any(
                re.search(r"\b(?:cert|certificate|certificates|certification|certifications|certified|course|courses|credential|credentials|training|license|licence)\b", cert, re.I)
                for cert in normalized_certs
            )
        ):
            continue
        seen.add(key)
        normalized.append(cleaned)

    return normalized


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
    certifications = _normalize_certifications(raw_data.get("certifications") or [])
    skill_names = _normalize_skills(skills_raw, certifications=certifications)
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
