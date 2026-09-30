# Profile Strength Service - Part 1
"""
New Profile Strength and Recommendation Readiness scoring system.

Architecture:
  Candidate Data
    -> Evidence Extraction
    -> Candidate Knowledge Model
    -> Role-Relevant Requirements
    -> Evidence Quality / Coverage / Consistency / Freshness
    -> Profile Strength (0-100)
    -> Recommendation Confidence
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Evidence quality levels (Phase 6)
# ---------------------------------------------------------------------------
EVIDENCE_UNKNOWN = 0       # No useful information
EVIDENCE_CLAIMED = 1       # Resume claim or candidate statement
EVIDENCE_CORROBORATED = 2  # Multiple independent sources agree
EVIDENCE_DEMONSTRATED = 3  # Project / work / explicit candidate usage
EVIDENCE_VERIFIED = 4      # External/documentary verification

# ---------------------------------------------------------------------------
# Role categories for role-aware scoring (Phase 3)
# ---------------------------------------------------------------------------
_TECH_KEYWORDS = {
    "engineer", "developer", "programmer", "devops", "sre", "data scientist",
    "ml engineer", "backend", "frontend", "fullstack", "full stack",
    "software", "cloud", "platform", "infrastructure", "security",
}
_SALES_KEYWORDS = {
    "sales", "account executive", "business development", "account manager",
    "revenue", "bdr", "sdr", "customer success",
}
_CREATIVE_KEYWORDS = {
    "designer", "ux", "ui", "graphic", "creative", "brand", "content",
    "copywriter", "marketing",
}
_MANAGEMENT_KEYWORDS = {
    "manager", "director", "vp", "head of", "chief", "cto", "ceo", "coo",
    "product manager", "program manager", "project manager",
}


def _role_category(target_roles: list[str], current_role: str) -> str:
    combined = " ".join(target_roles + [current_role]).lower()
    if any(k in combined for k in _TECH_KEYWORDS):
        return "technical"
    if any(k in combined for k in _SALES_KEYWORDS):
        return "sales"
    if any(k in combined for k in _CREATIVE_KEYWORDS):
        return "creative"
    if any(k in combined for k in _MANAGEMENT_KEYWORDS):
        return "management"
    return "general"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clean(v: Any) -> str:
    return " ".join(str(v).split()) if v is not None else ""


def _has_text(v: Any) -> bool:
    return bool(_clean(v))


def _has_list(v: Any) -> bool:
    return isinstance(v, list) and len(v) > 0


def _as_list(value: Any) -> list:
    """Return meaningful profile items without treating a string as characters."""
    if isinstance(value, list):
        return [item for item in value if item not in (None, "")]
    if _has_text(value):
        return [value]
    return []


def _project_key(item: dict) -> str:
    """Return a stable identity for a persisted project record."""
    return _clean(item.get("title") or item.get("name") or item.get("project_name")).casefold()


def _is_self_project_experience(item: dict) -> bool:
    """Return whether Eve's normal experience record is a Self-Project."""
    marker = re.sub(r"[^a-z0-9]+", " ", _clean(item.get("company")).casefold()).strip()
    return marker in {"self project", "self-project"}


def _read_project_evidence(candidate: dict, raw: dict, parsed_resume: dict) -> list[dict]:
    """Read genuine project records across the persisted profile shapes.

    Self-Projects are persisted by some profile paths as project fields on an
    experience record rather than in the top-level projects collection.  Only
    records with an explicit project identity are promoted; ordinary work
    descriptions are intentionally left as work experience.
    """
    sources = [raw.get("projects"), candidate.get("projects"), parsed_resume.get("projects")]
    records: list[dict] = []
    for source in sources:
        for item in _as_list(source):
            if isinstance(item, dict):
                records.append(item)
            elif _has_text(item):
                # Legacy profile storage allowed a candidate-supplied project
                # title as a string; retain that genuine project evidence.
                title = _clean(item)
                records.append({"title": title, "description": title})

    # Self-Projects have historically lived in the work-experience payload,
    # including parsed_resume_json/raw_data copies when the canonical column
    # was not updated. Read all persisted copies; the final identity merge
    # below makes this idempotent and prevents score inflation.
    experience_sources = [
        candidate.get("work_experience"),
        parsed_resume.get("work_experience"),
        raw.get("work_experience"),
    ]
    for experiences in experience_sources:
        for experience in _as_list(experiences):
            if not isinstance(experience, dict):
                continue
            nested = experience.get("projects")
            if isinstance(nested, list):
                records.extend(item for item in nested if isinstance(item, dict))
            title = experience.get("project_title") or experience.get("projectTitle") \
                or experience.get("project_name") or experience.get("projectName")
            description = experience.get("project_description") or experience.get("projectDescription")
            if _has_text(title) and _has_text(description):
                records.append({"title": title, "description": description})
            elif _is_self_project_experience(experience):
                # Eve's Chat/Voice profile update persists a Self-Project using
                # the ordinary work-experience shape: company="Self-Project",
                # title=<project title>, description=<project description>.
                # The explicit marker is required so normal jobs are not
                # reclassified as projects.
                title = experience.get("title") or experience.get("name")
                description = experience.get("description") or experience.get("summary")
                if _has_text(title) and _has_text(description):
                    records.append({"title": title, "description": description})

    unique: list[dict] = []
    by_key: dict[str, dict] = {}
    for item in records:
        title = _clean(item.get("title") or item.get("name") or item.get("project_name"))
        description = _clean(item.get("description") or item.get("summary"))
        # A title alone is not enough for a genuine project evidence record.
        if not title or not description:
            continue
        key = title.casefold()
        existing = by_key.get(key)
        if existing is None:
            normalized = dict(item)
            normalized["title"] = title
            normalized["description"] = description
            by_key[key] = normalized
            unique.append(normalized)
        elif len(description) > len(_clean(existing.get("description"))):
            existing["description"] = description
    return unique


def _experience_has_dates(item: Any) -> bool:
    """Recognise both structured dates and the display date range persisted by intake."""
    if not isinstance(item, dict):
        return False
    return any(_has_text(item.get(key)) for key in (
        "start_date", "startDate", "end_date", "endDate", "dates", "duration",
    ))


def _parse_raw(v: Any) -> dict:
    import json
    if isinstance(v, dict):
        return dict(v)
    if isinstance(v, str):
        try:
            r = json.loads(v)
            return r if isinstance(r, dict) else {}
        except Exception:
            return {}
    return {}


def _years_ago(ts: Optional[float], now_ts: float) -> Optional[float]:
    if ts is None:
        return None
    return (now_ts - ts) / (365.25 * 86400)


def _freshness_factor(years_old: Optional[float], decay_after: float = 3.0, floor: float = 0.5) -> float:
    """Return 1.0 for recent, decaying toward floor for old. None = neutral (1.0)."""
    if years_old is None:
        return 1.0  # unknown freshness: neutral, do not penalise
    if years_old <= decay_after:
        return 1.0
    excess = years_old - decay_after
    return max(floor, 1.0 - (excess / (decay_after * 2)) * (1.0 - floor))


# ---------------------------------------------------------------------------
# Phase 1 — Canonical voice intake state helper
# ---------------------------------------------------------------------------

def get_voice_intake_state(candidate: dict) -> dict:
    """
    Single canonical read path for voice intake state.

    Priority: raw_data.voice_intake (persisted structured state)
    Falls back to empty state. Never reads candidate_voice_intakes or
    candidate_voice_sessions directly — those are historical records.

    Returns a dict with keys:
      status, completed_turns, known_topics, missing_topics,
      transcript (reconstructed), has_meaningful_content,
      completion_status, turn_count
    """
    raw = _parse_raw(candidate.get("raw_data"))
    vi = _parse_raw(raw.get("voice_intake"))

    status = _clean(vi.get("status")).lower() or "not_started"
    completed_turns = vi.get("completed_turns") or []
    known_topics = vi.get("known_topics") or []
    missing_topics = vi.get("missing_topics") or []

    # Reconstruct a lightweight transcript from completed turns
    transcript_parts = []
    for turn in completed_turns:
        q = _clean(turn.get("question"))
        a = _clean(turn.get("answer"))
        if q and a:
            transcript_parts.append(f"Q: {q}\nA: {a}")

    has_meaningful = (
        len(completed_turns) >= 1
        and any(_clean(t.get("answer")) for t in completed_turns)
    )

    return {
        "status": status,
        "completed_turns": completed_turns,
        "known_topics": known_topics,
        "missing_topics": missing_topics,
        "transcript": "\n\n".join(transcript_parts),
        "has_meaningful_content": has_meaningful,
        "completion_status": status,
        "turn_count": len(completed_turns),
    }


# ---------------------------------------------------------------------------
# Phase 1 — Canonical preferences reader
# ---------------------------------------------------------------------------

def get_canonical_preferences(candidate: dict, prefs_row: Optional[dict] = None) -> dict:
    """
    Return the canonical preference state for a candidate.

    candidate_preferences table is authoritative.
    raw_data is used as fallback for fields not yet in the table.
    """
    raw = _parse_raw(candidate.get("raw_data"))
    parsed_resume = _parse_raw(candidate.get("parsed_resume_json"))
    p = prefs_row or {}

    def _jlist(v: Any) -> list:
        if isinstance(v, list):
            return v
        if isinstance(v, str):
            import json
            try:
                r = json.loads(v)
                return r if isinstance(r, list) else []
            except Exception:
                return [v] if _has_text(v) else []
        return []

    preferred_roles = (_jlist(p.get("preferred_roles")) or _jlist(raw.get("preferred_roles"))
                       or _jlist(raw.get("target_roles")) or _jlist(parsed_resume.get("preferred_roles"))
                       or _jlist(parsed_resume.get("target_roles")))
    # Profile updates use the canonical names below, while older resume/voice
    # imports used the aliases at the right. Both are persisted candidate
    # input and must produce the same score.
    preferred_locations = (
        _jlist(p.get("preferred_locations"))
        or _jlist(raw.get("preferred_locations"))
        or _jlist(raw.get("location_preferences"))
        or _jlist(parsed_resume.get("preferred_locations"))
        or _jlist(parsed_resume.get("location_preferences"))
    )
    preferred_industries = (
        _jlist(p.get("preferred_industries"))
        or _jlist(raw.get("preferred_industries"))
        or _jlist(raw.get("target_industries"))
        or _jlist(parsed_resume.get("preferred_industries"))
        or _jlist(parsed_resume.get("target_industries"))
    )
    employment_types = (_jlist(p.get("employment_types")) or _jlist(raw.get("employment_types"))
                        or _jlist(raw.get("employment_type")) or _jlist(parsed_resume.get("employment_types"))
                        or _jlist(parsed_resume.get("employment_type")))
    remote_preference = (_clean(p.get("remote_preference")) or _clean(raw.get("remote_preference"))
                         or _clean(raw.get("work_mode_preference")) or _clean(raw.get("work_type_preference"))
                         or _clean(parsed_resume.get("remote_preference")) or _clean(parsed_resume.get("work_mode_preference")))
    notice_period = _clean(p.get("notice_period")) or _clean(raw.get("notice_period")) or _clean(raw.get("availability")) or _clean(parsed_resume.get("notice_period"))
    expected_salary = (_clean(p.get("expected_salary")) or _clean(raw.get("expected_salary"))
                       or _clean(raw.get("salary_expectation")) or _clean(parsed_resume.get("expected_salary"))
                       or _clean(parsed_resume.get("salary_expectation")))
    willing_to_relocate = (p.get("willing_to_relocate") if p.get("willing_to_relocate") is not None
                           else raw.get("willing_to_relocate", parsed_resume.get("willing_to_relocate")))
    open_to_opportunities = p.get("open_to_opportunities") if p.get("open_to_opportunities") is not None else raw.get("open_to_opportunities")

    return {
        "preferred_roles": preferred_roles,
        "preferred_locations": preferred_locations,
        "preferred_industries": preferred_industries,
        "employment_types": employment_types,
        "remote_preference": remote_preference,
        "notice_period": notice_period,
        "expected_salary": expected_salary,
        "willing_to_relocate": willing_to_relocate,
        "open_to_opportunities": open_to_opportunities,
    }


def _completed_assessment_scores(candidate: dict) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """Read evidence only from completed assessments, across persisted shapes.

    Legacy top-level interview scores are results produced after an interview is
    completed. Newer assessment payloads must explicitly say completed.
    """
    raw = _parse_raw(candidate.get("raw_data"))
    parsed = _parse_raw(candidate.get("parsed_resume_json"))
    technical = candidate.get("interview_technical_score")
    communication = candidate.get("interview_communication_score")
    culture = candidate.get("interview_culture_fit_score")

    def score(value: Any) -> Optional[float]:
        try:
            value = float(value)
            return value if 0 <= value <= 10 else None
        except (TypeError, ValueError):
            return None

    for source in (raw, parsed):
        records = source.get("assessments") or source.get("assessment_results") or []
        if isinstance(records, dict):
            records = [records]
        for record in records if isinstance(records, list) else []:
            if not isinstance(record, dict) or _clean(record.get("status")).lower() not in ("completed", "complete"):
                continue
            kind = _clean(record.get("type") or record.get("assessment_type")).lower()
            technical = technical if technical is not None else record.get("technical_score")
            communication = communication if communication is not None else record.get("communication_score")
            culture = culture if culture is not None else record.get("culture_fit_score")
            # A completed behavioural/culture assessment can use a general score.
            if "technical" in kind and technical is None:
                technical = record.get("score")
            if any(word in kind for word in ("communication", "behavior", "behaviour", "culture")):
                communication = communication if communication is not None else record.get("score")
                culture = culture if culture is not None else record.get("score")
    return score(technical), score(communication), score(culture)


# ---------------------------------------------------------------------------
# Phase 1 — Item-level provenance / evidence model
# ---------------------------------------------------------------------------

_HANDS_ON_SKILLS = (("REST APIs", r"\bREST\s+APIs?\b"), ("Python", r"\bPython\b"), ("FastAPI", r"\bFastAPI\b"), ("Redis", r"\bRedis\b"), ("PostgreSQL", r"\bPostgreSQL\b"), ("MySQL", r"\bMySQL\b"), ("Embeddings", r"\bembeddings?\b"), ("Qdrant", r"\bQdrant\b"), ("Semantic Search", r"\bsemantic\s+(?:job\s+)?matching\b"), ("Asterisk", r"\bAsterisk\b"), ("PHP", r"\bPHP\b"), ("AGI", r"\bAGI\b"), ("CRM", r"\bCRM\b"))


def _backfill_demonstrated_skill_evidence(candidate: dict, raw: dict) -> dict:
    """Idempotently derive evidence records from existing hands-on text."""
    result = dict(raw or {})
    records = [r for r in (result.get("demonstrated_skill_evidence") or []) if isinstance(r, dict)]
    skills = {str(s).strip().casefold(): str(s).strip() for s in (candidate.get("skills") or []) if str(s).strip()}
    texts = []
    for item in candidate.get("work_experience") or []:
        if isinstance(item, dict):
            texts.extend(str(item.get(k) or "") for k in ("description", "responsibilities", "summary"))
    for item in result.get("projects") or []:
        if isinstance(item, dict):
            texts.extend(str(item.get(k) or "") for k in ("description", "responsibilities", "summary"))
    text_value = " ".join(texts)
    found = [skills[name.casefold()] for name, pattern in _HANDS_ON_SKILLS if name.casefold() in skills and re.search(pattern, text_value, re.I)]
    if found and not any(r.get("source") == "resume_work_experience" and r.get("skills") == found for r in records):
        records.append({"source": "resume_work_experience", "statement": text_value[:4000], "skills": found})
    result["demonstrated_skill_evidence"] = records
    return result


def build_attribute_evidence(candidate: dict, prefs_row: Optional[dict] = None) -> dict:
    """
    Build a lightweight evidence map for key candidate attributes.

    Returns a dict keyed by attribute name, each value being:
      {source, evidence_level, timestamp, confidence}

    Sources: claimed_from_resume | provided_by_candidate | extracted_from_voice
             | demonstrated_in_assessment | verified_by_document | system_inferred
    """
    raw = _backfill_demonstrated_skill_evidence(candidate, _parse_raw(candidate.get("raw_data")))
    vi_state = get_voice_intake_state(candidate)
    now_ts = datetime.now(timezone.utc).timestamp()

    evidence: dict[str, dict] = {}

    def _add(attr: str, source: str, level: int, confidence: float, ts: Optional[float] = None):
        existing = evidence.get(attr)
        if existing is None or level > existing["evidence_level"]:
            evidence[attr] = {
                "source": source,
                "evidence_level": level,
                "confidence": round(confidence, 3),
                "timestamp": ts or now_ts,
            }

    # Resume-derived attributes
    parsed_resume = _parse_raw(candidate.get("parsed_resume_json"))
    has_resume = bool(
        candidate.get("resume_file_path")
        or candidate.get("parsed_resume_text")
        or candidate.get("skills")
        or candidate.get("work_experience")
    )

    if has_resume:
        resume_ts = None
        rra = candidate.get("resume_received_at")
        if rra:
            try:
                if isinstance(rra, datetime):
                    resume_ts = rra.timestamp()
                else:
                    resume_ts = datetime.fromisoformat(str(rra).replace("Z", "+00:00")).timestamp()
            except Exception:
                resume_ts = None

        if _has_text(candidate.get("name")):
            _add("name", "claimed_from_resume", EVIDENCE_CLAIMED, 0.9, resume_ts)
        if _has_list(candidate.get("skills")):
            _add("skills", "claimed_from_resume", EVIDENCE_CLAIMED, 0.6, resume_ts)
        if _has_list(candidate.get("work_experience")):
            _add("work_experience", "claimed_from_resume", EVIDENCE_CLAIMED, 0.7, resume_ts)
        if _has_list(candidate.get("education")):
            _add("education", "claimed_from_resume", EVIDENCE_CLAIMED, 0.8, resume_ts)
        if _as_list(parsed_resume.get("certifications")) or _as_list(raw.get("certifications")):
            _add("certifications", "claimed_from_resume", EVIDENCE_CLAIMED, 0.6, resume_ts)

    # Voice-derived evidence (corroborates resume claims)
    if vi_state["has_meaningful_content"]:
        voice_topics = set(vi_state["known_topics"])
        if "skills_technologies" in voice_topics or "background_experience" in voice_topics:
            # Corroborated only when resume already claimed the skill
            if evidence.get("skills") and evidence["skills"].get("source") == "claimed_from_resume":
                _add("skills", "extracted_from_voice", EVIDENCE_CORROBORATED, 0.75)
            else:
                _add("skills", "extracted_from_voice", EVIDENCE_CLAIMED, 0.55)
        if "background_experience" in voice_topics:
            if evidence.get("work_experience"):
                _add("work_experience", "extracted_from_voice", EVIDENCE_CORROBORATED, 0.8)
        if "target_role" in voice_topics:
            _add("target_role", "extracted_from_voice", EVIDENCE_CLAIMED, 0.8)
        if "responsibilities_projects" in voice_topics:
            _add("projects", "extracted_from_voice", EVIDENCE_DEMONSTRATED, 0.7)
        if "availability_location" in voice_topics:
            _add("preferences", "extracted_from_voice", EVIDENCE_CLAIMED, 0.75)

    # Direct candidate statements from Chat or Voice may demonstrate an
    # *existing* skill.  These records are deliberately written by the intake
    # and chat persistence paths only after matching an explicit usage
    # statement against the candidate's pre-existing skills.  A resume skill,
    # a project technology list, or a newly extracted skill can never create
    # one of these records by itself.
    demonstrated_skill_evidence = raw.get("demonstrated_skill_evidence") or []
    if isinstance(demonstrated_skill_evidence, list):
        existing_skill_keys = {
            _clean(skill).lower() for skill in (candidate.get("skills") or [])
            if _clean(skill)
        }
        for record in demonstrated_skill_evidence:
            if not isinstance(record, dict):
                continue
            supported = record.get("skills") or []
            if not isinstance(supported, list):
                continue
            if any(_clean(skill).lower() in existing_skill_keys for skill in supported):
                _add("skills", "demonstrated_by_candidate_usage", EVIDENCE_DEMONSTRATED, 0.8)
                break

    # Uploaded certificates = verified_by_document
    certs = candidate.get("candidate_certificates") or []
    if isinstance(certs, list) and len(certs) > 0:
        _add("certifications", "verified_by_document", EVIDENCE_VERIFIED, 0.9)

    # Interview scores = demonstrated
    # Eve has no technical-assessment workflow. Technical capability is
    # demonstrated by work, projects, and explicit candidate usage evidence.
    _, comm_score, _ = _completed_assessment_scores(candidate)
    if comm_score is not None:
        _add("communication", "demonstrated_in_assessment", EVIDENCE_DEMONSTRATED, min(float(comm_score) / 10.0, 1.0))

    # Preferences from candidate_preferences table
    prefs = get_canonical_preferences(candidate, prefs_row)
    pref_count = sum(1 for v in prefs.values() if v)
    if pref_count >= 2:
        _add("preferences", "provided_by_candidate", EVIDENCE_CLAIMED, min(0.5 + pref_count * 0.05, 0.9))

    return evidence


# ---------------------------------------------------------------------------
# Phase 2-9 — Eight dimension scorers
# ---------------------------------------------------------------------------

def _score_identity_background(candidate: dict, evidence: dict) -> dict:
    """Dimension 1: Identity & Background"""
    score = 0.0
    signals = []

    if _has_text(candidate.get("name")):
        score += 15
        signals.append("name")
    if _has_text(candidate.get("email")):
        score += 10
        signals.append("email")
    if _has_text(candidate.get("location")):
        score += 10
        signals.append("location")
    if _has_text(candidate.get("current_role") or candidate.get("headline")):
        score += 15
        signals.append("current_role")
    if _has_text(candidate.get("current_company")):
        score += 5
        signals.append("current_company")

    exp_years = candidate.get("experience_years") or candidate.get("total_experience_years")
    if exp_years is not None:
        score += 10
        signals.append("experience_years")

    work_exp = candidate.get("work_experience") or []
    if isinstance(work_exp, list) and len(work_exp) > 0:
        score += 20
        signals.append("work_history")
        # Bonus for timeline consistency (has dates)
        dated = sum(1 for w in work_exp if _experience_has_dates(w))
        if dated > 0:
            score += 10
            signals.append("dated_history")

    edu = candidate.get("education") or []
    if isinstance(edu, list) and len(edu) > 0:
        score += 5
        signals.append("education")
        complete_education = sum(
            1 for item in edu
            if isinstance(item, dict)
            and _has_text(item.get("degree") or item.get("field_of_study"))
            and _has_text(item.get("institution") or item.get("school"))
        )
        if complete_education:
            score += 5
            signals.append("education_details")

    return {"score": min(score, 100.0), "signals": signals}


def _score_skills_capability(candidate: dict, evidence: dict, role_category: str) -> dict:
    """Dimension 2: skills coverage plus supported capability (20 points)."""
    signals = []
    now_ts = datetime.now(timezone.utc).timestamp()

    skills = candidate.get("skills") or []
    if not isinstance(skills, list):
        skills = []

    components = []
    relevant = len(skills) >= 1
    if relevant:
        components.append(("relevant_skills_listed", 6))
        signals.append("has_skills")
    if len(skills) >= 3:
        components.append(("role_relevant_breadth", 3))
        signals.append("multiple_skills")

    # Evidence quality bonus — apply freshness to skill evidence
    skill_ev = evidence.get("skills", {})
    ev_level = skill_ev.get("evidence_level", EVIDENCE_UNKNOWN)
    ev_ts = skill_ev.get("timestamp")
    years_old = _years_ago(ev_ts, now_ts) if ev_ts else None
    # Skills decay after 3 years of no evidence update
    freshness = _freshness_factor(years_old, decay_after=3.0, floor=0.6)

    if ev_level >= EVIDENCE_DEMONSTRATED:
        components.append(("skills_connected_to_work", 6))
        signals.append("skills_demonstrated")
    elif ev_level >= EVIDENCE_CORROBORATED:
        components.append(("skills_connected_to_work", 4))
        signals.append("skills_corroborated")
    elif ev_level >= EVIDENCE_CLAIMED:
        components.append(("skills_connected_to_work", 2))
        signals.append("skills_claimed")

    # For technical roles: work experience descriptions mentioning tech
    if role_category == "technical":
        work_exp = candidate.get("work_experience") or []
        tech_descriptions = sum(
            1 for w in work_exp
            if isinstance(w, dict) and _has_text(w.get("description"))
        )
        if tech_descriptions > 0:
            components.append(("technical_functional_depth", 2))
            signals.append("technical_descriptions")
    corroborated = evidence.get("skills", {}).get("evidence_level", 0) >= EVIDENCE_CORROBORATED
    if corroborated:
        components.append(("cross_source_corroboration", 3))
    earned = sum(points for _, points in components)
    return {"score": min(earned / 20.0 * 100.0, 100.0), "earned_points": earned,
            "signals": signals,
            "components": [{"name": n, "points": p, "earned": True} for n, p in components],
            "maximum": 20}


def _score_evidence(candidate: dict, evidence: dict, raw: dict, role_category: str) -> dict:
    """Dimension 3: quality and depth of distinct evidence (30 points)."""
    signals = []
    components = []

    # Projects
    parsed_resume = _parse_raw(candidate.get("parsed_resume_json"))
    # Projects may arrive from resume parsing, chat/voice raw_data, or a
    # canonical profile column.  All are candidate-provided evidence; the
    # source affects provenance, not whether the section exists.
    projects = _read_project_evidence(candidate, raw, parsed_resume)
    vi_state = get_voice_intake_state(candidate)
    has_projects = (
        bool(projects)
        or _has_text(raw.get("project_summary"))
        or "responsibilities_projects" in vi_state.get("known_topics", [])
    )
    if has_projects:
        components.append(("genuine_project_or_substantive_work", 4))
        signals.append("projects")

    distinct_projects = len(projects)
    if distinct_projects >= 2:
        components.append(("multiple_distinct_projects", 4))
        signals.append("multiple_distinct_projects")

    project_text = " ".join(_clean(p.get("description")) for p in projects)
    work_text = " ".join(_clean(w.get("description") or w.get("summary") or w.get("responsibilities"))
                          for w in (candidate.get("work_experience") or []) if isinstance(w, dict))
    all_text = f"{project_text} {work_text}".lower()
    if re.search(r"\b(built|developed|designed|implemented|solved|automated|created|improved|managed)\b", all_text):
        components.append(("purpose_problem_solution", 5))
    if re.search(r"\b(python|java|javascript|typescript|fastapi|react|sql|postgres|docker|aws|redis|api|framework|database)\b", all_text):
        components.append(("technologies_tools_methods", 4))
    if re.search(r"\b(i|we)\s+(built|developed|designed|implemented|led|owned|managed)|responsibil", all_text):
        components.append(("candidate_responsibilities", 4))
    if re.search(r"\b(increased|reduced|improved|achieved|delivered|scale|%|users|revenue|performance|latency)\b", all_text):
        components.append(("outcomes_impact", 3))

    # A populated certification section is useful profile evidence even when
    # its documents have not been uploaded.  Uploaded documents receive the
    # larger verified-evidence credit below, so this does not equate a claim
    # with verification.
    claimed_certs = _as_list(
        candidate.get("certifications")
        or raw.get("certifications")
        or parsed_resume.get("certifications")
    )
    if claimed_certs:
        components.append(("certifications", 1))
        signals.append("certifications_claimed")

    # Voice demonstrated capability
    if "responsibilities_projects" in vi_state.get("known_topics", []):
        signals.append("voice_projects")

    # Work experience with descriptions (evidence of doing, not just claiming)
    work_exp = candidate.get("work_experience") or []
    described = sum(1 for w in work_exp if isinstance(w, dict) and _has_text(w.get("description") or w.get("summary")))
    if described >= 1:
        signals.append("described_experience")

    # Credit complete, substantive employment records rather than merely a
    # list of job titles.  This is capped and requires real role, employer,
    # timeline, and responsibility information.
    complete_roles = sum(
        1 for w in work_exp
        if isinstance(w, dict)
        and _has_text(w.get("title"))
        and _has_text(w.get("company"))
        and _experience_has_dates(w)
        and _has_text(w.get("description") or w.get("summary"))
    )
    if complete_roles:
        components.append(("work_history_depth", 3))
        signals.append("complete_experience_records")

    # Non-technical roles: communication evidence counts here too
    if role_category in ("sales", "management"):
        comm_ev = evidence.get("communication", {})
        if comm_ev.get("evidence_level", 0) >= EVIDENCE_DEMONSTRATED:
            signals.append("communication_assessed")
    if ("projects" in signals and ("voice_projects" in signals or evidence.get("projects", {}).get("source"))):
        components.append(("resume_profile_chat_voice_corroboration", 2))
    earned = min(sum(points for _, points in components), 30)
    return {"score": earned / 30.0 * 100.0, "earned_points": earned, "signals": signals,
            "components": [{"name": n, "points": p, "earned": True} for n, p in components],
            "maximum": 30, "projects_detected": distinct_projects}


def _score_career_intent(candidate: dict, prefs: dict, raw: dict, vi_state: dict) -> dict:
    """Dimension 4: Career Intent"""
    score = 0.0
    signals = []
    ambiguity_flags = []

    preferred_roles = prefs.get("preferred_roles") or raw.get("preferred_roles") or []
    if not isinstance(preferred_roles, list):
        preferred_roles = []

    if len(preferred_roles) >= 1:
        score += 5
        signals.append("target_role_stated")
        # Penalise vague "anything" intent
        vague = any(
            r.lower().strip() in ("anything", "any role", "open to anything", "flexible")
            for r in preferred_roles
        )
        if vague:
            score -= 3
            ambiguity_flags.append("vague_role_preference")
    else:
        ambiguity_flags.append("no_target_role")

    if _has_text(candidate.get("current_role") or candidate.get("headline")):
        score += 2
        signals.append("current_role_known")

    # Career goals / summary
    if _has_text(candidate.get("summary")):
        score += 4
        signals.append("career_summary")

    # Voice-confirmed intent
    if "target_role" in vi_state.get("known_topics", []):
        score += 3
        signals.append("voice_confirmed_intent")

    if "career_preferences" in vi_state.get("known_topics", []):
        score += 1
        signals.append("career_preferences_known")

    preferred_industries = prefs.get("preferred_industries") or []
    if isinstance(preferred_industries, list) and len(preferred_industries) > 0:
        score += 1
        signals.append("target_industries")

    return {
        "score": min(max(score, 0.0), 15.0) / 15.0 * 100.0,
        "earned_points": min(max(score, 0.0), 15.0),
        "signals": signals,
        "ambiguity_flags": ambiguity_flags,
    }


def _score_preferences_constraints(prefs: dict, raw: dict, vi_state: dict) -> dict:
    """Dimension 5: Preferences & Constraints — availability/remote are time-sensitive."""
    score = 0.0
    signals = []
    known = []
    unknown = []
    now_ts = datetime.now(timezone.utc).timestamp()

    def _check(key: str, label: str, points: int, freshness_decay: Optional[float] = None):
        val = prefs.get(key)
        if val is None:
            val = raw.get(key)
        # Do not pass collections through _has_text: ``str([])`` is ``"[]"``
        # and previously made empty preference lists look complete. This also
        # meant a real answer could be persisted without increasing the score.
        if isinstance(val, (list, tuple, set, dict)):
            has = len(val) > 0
        elif isinstance(val, bool):
            has = val
        else:
            has = _has_text(val)
        if has:
            pts = points
            if freshness_decay is not None:
                # Time-sensitive fields: use voice intake recency as proxy
                # If voice intake has been done recently, treat as fresh
                vi_turns = vi_state.get("turn_count", 0)
                # Without a real timestamp, use neutral freshness if voice done, else slight decay
                freshness = 1.0 if vi_turns > 0 else _freshness_factor(None)
                pts = int(points * freshness)
            score_ref.append(pts)
            signals.append(label)
            known.append(label)
        else:
            unknown.append(label)

    score_ref: list[int] = []

    _check("preferred_locations", "location_preferences", 2)
    _check("remote_preference", "remote_preference", 2, freshness_decay=1.0)
    _check("notice_period", "availability", 2, freshness_decay=0.5)
    _check("expected_salary", "salary_expectation", 1)
    _check("employment_types", "employment_types", 1)
    _check("preferred_industries", "target_industries", 1)

    willing = prefs.get("willing_to_relocate")
    if willing is not None:
        score_ref.append(1)
        signals.append("relocation_stated")
        known.append("relocation")
    else:
        unknown.append("relocation")

    score = float(sum(score_ref))

    return {
        "score": min(score, 10.0) / 10.0 * 100.0,
        "earned_points": min(score, 10.0),
        "signals": signals,
        "known": known,
        "unknown": unknown,
    }


def _score_behaviour_communication(candidate: dict, vi_state: dict) -> dict:
    """Dimension 6: substantive communication evidence (7 points)."""
    signals = []
    components = []

    # Interview communication score
    _, comm_score, culture_score = _completed_assessment_scores(candidate)
    # Assessment scores are intentionally excluded from this model.

    turn_count = vi_state.get("turn_count", 0)
    turns = vi_state.get("completed_turns", [])
    substantive = [t for t in turns if len(_clean(t.get("answer"))) >= 25]
    if substantive:
        components.append(("meaningful_participation", 2))
        signals.append("meaningful_interaction")
    text = " ".join(_clean(t.get("answer")) for t in substantive).lower()
    if re.search(r"\b(built|developed|designed|implemented|led|managed|responsib|project|experience)\b", text):
        components.append(("experience_responsibilities_explained", 2))
        signals.append("experience_explained")
    if turns and not any(i.get("severity") in ("high", "medium") for i in _detect_inconsistencies(candidate, vi_state)):
        components.append(("coherent_information", 2))
        signals.append("coherent_information")
    if len(substantive) >= 2:
        components.append(("useful_follow_up_detail", 1))
        signals.append("follow_up_detail")

    # Culture fit score
    # No assessment or culture score is used here.

    # If no behavioural evidence at all, return incomplete (not fabricated)
    if not components:
        return {"score": None, "signals": [], "incomplete": True}

    earned = min(sum(p for _, p in components), 7)
    return {"score": earned / 7.0 * 100.0, "earned_points": earned, "signals": signals,
            "components": [{"name": n, "points": p, "earned": True} for n, p in components],
            "maximum": 7, "incomplete": False}


def _score_career_readiness(
    dim_scores: dict,
    role_category: str,
    prefs: dict,
    vi_state: dict,
) -> dict:
    """Dimension 7: Career Readiness"""
    identity = dim_scores.get("identity_background", {}).get("score", 0) or 0
    skills = dim_scores.get("skills_capability", {}).get("score", 0) or 0
    evidence = dim_scores.get("evidence", {}).get("score", 0) or 0
    intent = dim_scores.get("career_intent", {}).get("score", 0) or 0

    signals = []
    missing_critical = []
    components = []
    if identity >= 70:
        components.append(("identity_sufficient", 1))
    if intent >= 70:
        components.append(("target_role_clear", 2))
    if skills >= 60:
        components.append(("skills_relevant_supported", 2))
    if evidence >= 60:
        components.append(("evidence_substantive", 2))
    if prefs and sum(bool(v) for v in prefs.values()) >= 3:
        components.append(("key_constraints_known", 1))
    if intent < 70:
        missing_critical.append("career_intent_unclear")
    if skills < 20:
        missing_critical.append("skills_insufficient")
    if evidence < 20:
        missing_critical.append("evidence_weak")

    if vi_state.get("has_meaningful_content"):
        signals.append("voice_intake_completed_turns")

    return {
        "score": sum(p for _, p in components) / 8.0 * 100.0,
        "earned_points": sum(p for _, p in components),
        "signals": signals,
        "missing_critical": missing_critical,
        "components": [{"name": n, "points": p, "earned": True} for n, p in components],
        "maximum": 8,
    }


# ---------------------------------------------------------------------------
# Phase 6 — Expanded consistency / contradiction checker
# ---------------------------------------------------------------------------

def _detect_inconsistencies(candidate: dict, vi_state: dict) -> list[dict]:
    """
    Detect meaningful contradictions between candidate data sources.

    Distinguishes:
      hard_contradiction  — clear factual conflict
      ambiguity           — conflicting signals, unclear which is correct
      career_transition   — apparent change that is NOT a contradiction
    """
    issues = []
    raw = _parse_raw(candidate.get("raw_data"))

    # 1. Experience years: resume vs voice
    resume_years = candidate.get("experience_years")
    voice_years_raw = raw.get("voice_experience_years")
    if resume_years is not None and voice_years_raw is not None:
        try:
            ry = float(resume_years)
            vy = float(voice_years_raw)
            if abs(ry - vy) > 2.0:
                issues.append({
                    "field": "experience_years",
                    "type": "hard_contradiction",
                    "resume_value": ry,
                    "voice_value": vy,
                    "severity": "medium",
                    "description": f"Resume claims {ry:.0f} years but voice suggests {vy:.0f} years",
                })
        except (TypeError, ValueError):
            pass

    # 2. Employment timeline overlaps (impossible concurrent roles at different companies)
    work_exp = candidate.get("work_experience") or []
    if isinstance(work_exp, list) and len(work_exp) >= 2:
        from candidate_job_matching_service import _parse_experience_window
        windows = []
        for item in work_exp:
            if not isinstance(item, dict):
                continue
            start, end = _parse_experience_window(item)
            company = _clean(item.get("company"))
            if start:
                windows.append((start, end, company))
        windows.sort(key=lambda x: x[0])
        for i in range(len(windows) - 1):
            s1, e1, c1 = windows[i]
            s2, e2, c2 = windows[i + 1]
            if e1 is None:
                continue  # open-ended: not a contradiction
            if s2 < e1 and c1 and c2 and c1.lower() != c2.lower():
                overlap_days = (e1 - s2).days
                if overlap_days > 30:  # ignore minor date rounding
                    issues.append({
                        "field": "employment_timeline",
                        "type": "hard_contradiction",
                        "resume_value": f"{c1} ends {e1.date()}",
                        "voice_value": f"{c2} starts {s2.date()}",
                        "severity": "medium",
                        "description": f"Employment timeline overlap: {c1} and {c2} overlap by {overlap_days} days",
                    })

    # 3. Preference contradiction: remote-only vs willing to relocate onsite
    remote_pref = _clean(raw.get("work_type_preference") or raw.get("remote_preference")).lower()
    willing_relocate = raw.get("willing_to_relocate")
    if "remote" in remote_pref and "only" in remote_pref and willing_relocate is True:
        issues.append({
            "field": "work_mode_preference",
            "type": "ambiguity",
            "resume_value": remote_pref,
            "voice_value": "willing_to_relocate=True",
            "severity": "low",
            "description": "Candidate states remote-only but also willing to relocate — preference may be flexible",
        })

    # 4. Skill level contradiction: resume claims skill, voice explicitly states beginner/just started
    for turn in vi_state.get("completed_turns", []):
        answer = _clean(turn.get("answer")).lower()
        skills = candidate.get("skills") or []
        for skill in (skills if isinstance(skills, list) else []):
            skill_lower = _clean(skill).lower()
            if not skill_lower:
                continue
            beginner_patterns = [
                f"started learning {skill_lower}",
                f"just started {skill_lower}",
                f"beginner in {skill_lower}",
                f"new to {skill_lower}",
                f"learning {skill_lower} recently",
            ]
            if any(p in answer for p in beginner_patterns):
                issues.append({
                    "field": f"skill:{skill}",
                    "type": "hard_contradiction",
                    "resume_value": "claimed_experienced",
                    "voice_value": "recently_started",
                    "severity": "low",
                    "description": f"Resume lists {skill} but voice suggests recently started",
                })

    # 5. Role transition detection (NOT a contradiction — mark as career_transition)
    current_role = _clean(candidate.get("current_role") or candidate.get("headline")).lower()
    preferred_roles = raw.get("preferred_roles") or []
    if isinstance(preferred_roles, list) and preferred_roles and current_role:
        current_cat = _role_category([], current_role)
        target_cat = _role_category(preferred_roles, "")
        if current_cat != target_cat and current_cat != "general" and target_cat != "general":
            issues.append({
                "field": "career_direction",
                "type": "career_transition",
                "resume_value": current_role,
                "voice_value": ", ".join(preferred_roles[:2]),
                "severity": "info",
                "description": f"Candidate appears to be transitioning from {current_cat} to {target_cat} roles",
            })

    return issues


# ---------------------------------------------------------------------------
# Phase 11 — Recommendation confidence (Dimension 8)
# ---------------------------------------------------------------------------

def _score_recommendation_confidence(
    dim_scores: dict,
    role_category: str,
    prefs: dict,
    inconsistencies: list,
    vi_state: dict,
) -> dict:
    """
    Dimension 8 / Phase 11: Recommendation Confidence.

    NOT a simple average. Asks: does Eve know enough about this candidate
    to confidently recommend jobs for their TARGET ROLE?
    """
    intent_score = dim_scores.get("career_intent", {}).get("score", 0) or 0
    skills_score = dim_scores.get("skills_capability", {}).get("score", 0) or 0
    prefs_score = dim_scores.get("preferences_constraints", {}).get("score", 0) or 0
    readiness_score = dim_scores.get("career_readiness", {}).get("score", 0) or 0

    preferred_roles = prefs.get("preferred_roles") or []
    has_clear_target = (
        isinstance(preferred_roles, list)
        and len(preferred_roles) >= 1
        and not any(
            r.lower().strip() in ("anything", "any role", "open to anything")
            for r in preferred_roles
        )
    )

    # Gate: without a clear target role, confidence is capped
    if not has_clear_target:
        confidence = min(intent_score * 0.4 + skills_score * 0.3, 50.0)
        level = "low"
        reason = "target_role_unclear"
        return {
            "score": confidence,
            "level": level,
            "confidence": round(confidence / 100.0, 3),
            "gating_reason": reason,
            "recommendation_tier": "limited",
        }

    # Weighted confidence for known target role
    confidence = (
        intent_score * 0.35
        + skills_score * 0.30
        + prefs_score * 0.15
        + readiness_score * 0.20
    )

    # Consistency penalty
    high_severity = sum(1 for i in inconsistencies if i.get("severity") == "high")
    medium_severity = sum(1 for i in inconsistencies if i.get("severity") == "medium")
    confidence -= high_severity * 15 + medium_severity * 5
    confidence = max(confidence, 0.0)

    if confidence >= 70:
        level = "high"
        tier = "strong_personalized"
    elif confidence >= 45:
        level = "medium"
        tier = "broader_matching"
    else:
        level = "low"
        tier = "limited"

    return {
        "score": min(confidence, 100.0),
        "level": level,
        "confidence": round(min(confidence, 100.0) / 100.0, 3),
        "gating_reason": None,
        "recommendation_tier": tier,
    }


# ---------------------------------------------------------------------------
# Phase 3 — Role-aware requirement weights
# ---------------------------------------------------------------------------

def _role_aware_profile_weight(role_category: str) -> dict[str, float]:
    """
    Return dimension weights for the final profile strength score.
    Weights sum to 1.0.
    """
    if role_category == "technical":
        return {
            "identity_background": 0.10,
            "skills_capability": 0.20,
            "evidence": 0.30,
            "career_intent": 0.15,
            "preferences_constraints": 0.10,
            "behaviour_communication": 0.07,
            "career_readiness": 0.08,
        }
    if role_category == "sales":
        return {
            "identity_background": 0.15,
            "skills_capability": 0.15,
            "evidence": 0.20,
            "career_intent": 0.20,
            "preferences_constraints": 0.15,
            "behaviour_communication": 0.10,
            "career_readiness": 0.05,
        }
    # general / management / creative
    return {"identity_background": 0.10, "skills_capability": 0.20, "evidence": 0.30,
            "career_intent": 0.15, "preferences_constraints": 0.10,
            "behaviour_communication": 0.07, "career_readiness": 0.08}


# ---------------------------------------------------------------------------
# Phase 4 — 100% definition: role-relevant completeness gate
# ---------------------------------------------------------------------------

def _is_role_complete(
    dim_scores: dict,
    role_category: str,
    prefs: dict,
    inconsistencies: Optional[list] = None,
    rec_conf: Optional[dict] = None,
    evidence: Optional[dict] = None,
) -> bool:
    """
    100% means Eve has sufficient, relevant, recent, consistent, evidence-backed
    information to confidently understand the candidate and make high-quality
    recommendations for their intended career direction.

    Universal gates (all must pass):
    - identity/background sufficiently known
    - clear target role (non-vague)
    - career intent sufficiently clear
    - relevant capabilities sufficiently understood
    - sufficient evidence for role-critical capabilities (not just claimed)
    - relevant preferences/constraints sufficiently known
    - no unresolved high-severity contradictions
    - recommendation confidence sufficiently high
    """
    identity = dim_scores.get("identity_background", {}).get("score", 0) or 0
    skills = dim_scores.get("skills_capability", {}).get("score", 0) or 0
    intent = dim_scores.get("career_intent", {}).get("score", 0) or 0
    prefs_score = dim_scores.get("preferences_constraints", {}).get("score", 0) or 0
    evidence_score = dim_scores.get("evidence", {}).get("score", 0) or 0

    preferred_roles = prefs.get("preferred_roles") or []
    has_clear_target = (
        isinstance(preferred_roles, list)
        and len(preferred_roles) >= 1
        and not any(
            r.lower().strip() in ("anything", "any role", "open to anything", "flexible")
            for r in preferred_roles
        )
    )

    # Universal gate: no unresolved high-severity contradictions
    if inconsistencies:
        high_severity = sum(1 for i in inconsistencies if i.get("severity") == "high")
        if high_severity > 0:
            return False

    # Universal gate: recommendation confidence must be sufficiently high
    if rec_conf:
        if rec_conf.get("level") == "low":
            return False

    # Universal gate: evidence must be above claimed-only level for role-critical capabilities
    # (prevents 50-skill resume with no demonstrated evidence from reaching 100)
    if evidence:
        skill_ev_level = evidence.get("skills", {}).get("evidence_level", EVIDENCE_UNKNOWN)
        if skill_ev_level < EVIDENCE_CORROBORATED:
            return False

    if role_category == "technical":
        return (
            has_clear_target
            and identity >= 70
            and skills >= 60
            and intent >= 60
            and prefs_score >= 40
            and evidence_score >= 30
        )
    return (
        has_clear_target
        and identity >= 65
        and skills >= 50
        and intent >= 55
        and prefs_score >= 35
    )


# ---------------------------------------------------------------------------
# Phase 10 — Explainability: missing info and next actions
# ---------------------------------------------------------------------------

def _build_explainability(dim_scores: dict, prefs: dict, evidence: dict, vi_state: dict) -> dict:
    """Build actionable gaps and next steps for the candidate."""
    strong = []
    needs_evidence = []
    missing = []
    next_actions = []

    def _dim_score(key: str) -> float:
        d = dim_scores.get(key, {})
        s = d.get("score")
        return float(s) if s is not None else 0.0

    if _dim_score("career_intent") >= 70:
        strong.append("Career intent")
    elif _dim_score("career_intent") >= 40:
        needs_evidence.append("Career direction (be more specific about target roles)")
    else:
        missing.append("Target role / career direction")
        next_actions.append("Tell Eve what kind of role you are targeting")

    if _dim_score("identity_background") >= 70:
        strong.append("Work history")
    elif _dim_score("identity_background") < 40:
        missing.append("Work history")
        next_actions.append("Upload your resume or add work experience")

    skill_ev = evidence.get("skills", {})
    if skill_ev.get("evidence_level", 0) >= EVIDENCE_DEMONSTRATED:
        strong.append("Skills (demonstrated)")
    elif skill_ev.get("evidence_level", 0) >= EVIDENCE_CORROBORATED:
        strong.append("Skills (corroborated)")
    elif _dim_score("skills_capability") >= 40:
        needs_evidence.append("Skills (claimed but not yet evidenced)")
        next_actions.append("Add projects or responsibilities that demonstrate your skills")
    else:
        missing.append("Skills")
        next_actions.append("Add your key skills to your profile")

    if _dim_score("evidence") >= 60:
        strong.append("Evidence of capability")
    elif _dim_score("evidence") < 30:
        needs_evidence.append("Projects or demonstrated work")
        next_actions.append("Add projects, responsibilities, or portfolio links")

    prefs_score = _dim_score("preferences_constraints")
    prefs_dim = dim_scores.get("preferences_constraints", {})
    unknown_prefs = prefs_dim.get("unknown", [])
    if prefs_score >= 60:
        strong.append("Preferences & availability")
    else:
        for up in unknown_prefs[:3]:
            missing.append(up.replace("_", " ").title())
        if "availability" in unknown_prefs:
            next_actions.append("Share your availability / notice period")
        if "remote_preference" in unknown_prefs:
            next_actions.append("Share your preferred work mode (remote/hybrid/on-site)")
        if "salary_expectation" in unknown_prefs:
            next_actions.append("Share your salary expectations")

    behaviour = dim_scores.get("behaviour_communication", {})
    if behaviour.get("incomplete"):
        pass  # Don't surface as missing — it's optional
    elif _dim_score("behaviour_communication") >= 50:
        strong.append("Communication evidence")

    return {
        "strong": strong,
        "needs_evidence": needs_evidence,
        "missing": missing,
        "next_actions": next_actions[:5],
    }


def _build_ninety_percent_guidance(percent: int, explain: dict, dimensions: Optional[dict] = None) -> dict:
    """Return UI-safe, actionable completion guidance without recalculating score."""
    action_sections = {
        "Tell Eve what kind of role you are targeting": ("Target role", "preferred-roles", "What job titles or roles are you targeting?"),
        "Upload your resume or add work experience": ("Work experience", "work-experience", "Tell me about your most recent role, company, dates, and main responsibilities."),
        "Add projects or responsibilities that demonstrate your skills": ("Skills evidence", "additional-information", "Describe a project or responsibility that demonstrates your strongest skills."),
        "Add your key skills to your profile": ("Key skills", "skills", "What are your strongest professional and technical skills?"),
        "Add projects, responsibilities, or portfolio links": ("Projects and portfolio", "additional-information", "Tell me about a relevant project, your contribution, and its outcome."),
        "Share your availability / notice period": ("Availability", "additional-information", "What is your notice period, and when can you start?"),
        "Share your preferred work mode (remote/hybrid/on-site)": ("Work preferences", "additional-information", "Do you prefer remote, hybrid, or on-site work?"),
        "Share your salary expectations": ("Salary expectations", "additional-information", "What salary range are you targeting?"),
    }
    items = []
    seen_actions = set()

    def add(action: str, title: str, section: str, question: str) -> None:
        if action in seen_actions or len(items) >= 5:
            return
        seen_actions.add(action)
        items.append({"title": title, "action": action, "section": section, "question": question})

    if percent < 90:
        for action in explain.get("next_actions", []):
            title, section, question = action_sections.get(
                action, (action, "additional-information", action)
            )
            add(action, title, section, question)

        # A strong-but-not-yet-90 profile can have no fully missing sections.
        # Surface partial gaps from the same scored dimensions so every item
        # is tied to points the canonical calculator can award.
        dims = dimensions or {}
        identity = dims.get("identity_background", {})
        identity_signals = set(identity.get("signals") or [])
        identity_gaps = [
            ("location", "Add your current location", "Location", "additional-information", "What city and country are you currently based in?"),
            ("experience_years", "Add your total years of experience", "Experience length", "work-experience", "How many total years of professional experience do you have?"),
            ("dated_history", "Add dates to your work history", "Work dates", "work-experience", "What were the start and end dates for your recent roles?"),
            ("education", "Add your education", "Education", "education", "What is your highest qualification, institution, and graduation year?"),
            ("education_details", "Complete your education details", "Education details", "education", "What degree did you earn and which institution awarded it?"),
        ]
        if float(identity.get("score") or 0) < 100:
            for signal, action, title, section, question in identity_gaps:
                if signal not in identity_signals:
                    add(action, title, section, question)

        skills = dims.get("skills_capability", {})
        skill_components = {c.get("name") for c in skills.get("components", []) if isinstance(c, dict)}
        if float(skills.get("score") or 0) < 100:
            if "role_relevant_breadth" not in skill_components:
                add("Add more role-relevant skills", "Role-relevant skills", "skills", "Which other skills do you regularly use in your work?")
            if "skills_connected_to_work" not in skill_components or "technical_functional_depth" not in skill_components:
                add("Connect your skills to real work", "Skills evidence", "work-experience", "Give one example of how you used your key skills in a project or job.")

        evidence = dims.get("evidence", {})
        evidence_components = {c.get("name") for c in evidence.get("components", []) if isinstance(c, dict)}
        if float(evidence.get("score") or 0) < 100:
            evidence_gaps = [
                ("genuine_project_or_substantive_work", "Add a relevant project", "Project evidence", "Tell me about a relevant project, what you built, and your contribution."),
                ("multiple_distinct_projects", "Add another relevant project", "More project evidence", "Tell me about another relevant project and what you contributed."),
                ("outcomes_impact", "Add measurable outcomes", "Measured impact", "What measurable result did your work achieve, such as time saved, growth, users, or performance improvement?"),
                ("work_history_depth", "Complete your work experience details", "Experience evidence", "For a recent role, share the company, title, dates, responsibilities, and outcomes."),
            ]
            for component, action, title, question in evidence_gaps:
                if component not in evidence_components:
                    add(action, title, "additional-information", question)

        intent = dims.get("career_intent", {})
        intent_signals = set(intent.get("signals") or [])
        if float(intent.get("score") or 0) < 100:
            if "target_industries" not in intent_signals:
                add("Add your preferred industries", "Preferred industries", "additional-information", "Which industries are you most interested in working in?")
            if "career_summary" not in intent_signals:
                add("Add your career summary", "Career summary", "additional-information", "In two or three sentences, what is your background and what do you want to do next?")

        prefs = dims.get("preferences_constraints", {})
        pref_questions = {
            "location_preferences": ("Add preferred locations", "Preferred locations", "Which locations are you open to working in?"),
            "employment_types": ("Add preferred employment types", "Employment type", "Are you looking for full-time, part-time, contract, or freelance work?"),
            "target_industries": ("Add your preferred industries", "Preferred industries", "Which industries are you most interested in working in?"),
            "relocation": ("Confirm relocation preference", "Relocation", "Are you willing to relocate for the right role?"),
        }
        for key in prefs.get("unknown", []) or []:
            if key in pref_questions:
                action, title, question = pref_questions[key]
                add(action, title, "additional-information", question)

    return {
        "current_percent": percent,
        "remaining_percent_to_90": max(0, 90 - percent),
        "items": items,
    }


# ---------------------------------------------------------------------------
# Fresher detection (preserved from original, used in scoring)
# ---------------------------------------------------------------------------

_FRESHER_HINTS = (
    "student", "intern", "fresher", "graduate", "new grad",
    "entry level", "junior", "trainee",
)


def _is_fresher(candidate: dict, raw: dict) -> bool:
    work_exp = candidate.get("work_experience") or []
    if isinstance(work_exp, list) and len(work_exp) > 0:
        return False
    years = candidate.get("experience_years")
    try:
        if years is not None and float(years) >= 2:
            return False
    except (TypeError, ValueError):
        pass
    role_text = " ".join(
        str(v).lower()
        for v in (candidate.get("current_role"), candidate.get("headline"), raw.get("current_role"))
        if _has_text(v)
    )
    if any(h in role_text for h in _FRESHER_HINTS):
        return True
    return not role_text or not any(
        t in role_text for t in ("senior", "lead", "principal", "manager", "director")
    )


# ---------------------------------------------------------------------------
# Temporary support diagnostic (read-only; does not participate in scoring)
# ---------------------------------------------------------------------------

def build_profile_strength_diagnostic(
    candidate: dict,
    raw_data: Optional[dict] = None,
    prefs_row: Optional[dict] = None,
) -> dict:
    """Return a point-level explanation of the existing score calculation.

    This helper is intentionally observational: it calls the production
    calculator and describes the inputs/components it used.  It must not be
    used by request handling or scoring paths.
    """
    result = calculate_profile_strength_v2(candidate, raw_data, prefs_row)
    raw = raw_data if isinstance(raw_data, dict) else _parse_raw(candidate.get("raw_data"))
    parsed_resume = _parse_raw(candidate.get("parsed_resume_json"))

    def prefer(primary: Any, fallback: Any) -> Any:
        return primary if primary is not None and primary != "" and primary != [] else fallback

    skills = prefer(candidate.get("skills"), parsed_resume.get("skills")) or []
    if not isinstance(skills, list):
        skills = []
    work_experience = prefer(candidate.get("work_experience"), parsed_resume.get("work_experience")) or []
    if not isinstance(work_experience, list):
        work_experience = []
    evidence = result["evidence"]
    skill_evidence = evidence.get("skills", {})
    evidence_level = skill_evidence.get("evidence_level", EVIDENCE_UNKNOWN)
    now_ts = datetime.now(timezone.utc).timestamp()
    evidence_timestamp = skill_evidence.get("timestamp")
    years_old = _years_ago(evidence_timestamp, now_ts) if evidence_timestamp else None
    freshness = _freshness_factor(years_old, decay_after=3.0, floor=0.6)
    role_category = result["role_category"]
    technical_descriptions = [
        {
            "title": item.get("title"),
            "company": item.get("company"),
            "description": item.get("description") or item.get("summary"),
        }
        for item in work_experience
        if isinstance(item, dict) and _has_text(item.get("description"))
    ]
    complete_records = [
        item for item in work_experience
        if isinstance(item, dict)
        and _has_text(item.get("title"))
        and _has_text(item.get("company"))
        and _experience_has_dates(item)
        and _has_text(item.get("description") or item.get("summary"))
    ]
    voice = get_voice_intake_state({**candidate, "raw_data": raw})
    dimensions = result["dimensions"]
    readiness_weights = (
        {"identity": 0.15, "skills": 0.35, "evidence": 0.35, "intent": 0.15}
        if role_category == "technical" else
        {"identity": 0.20, "skills": 0.20, "evidence": 0.30, "intent": 0.30}
        if role_category == "sales" else
        {"identity": 0.25, "skills": 0.25, "evidence": 0.25, "intent": 0.25}
    )
    readiness_inputs = {
        "identity": dimensions["identity_background"]["score"] or 0,
        "skills": dimensions["skills_capability"]["score"] or 0,
        "evidence": dimensions["evidence"]["score"] or 0,
        "intent": dimensions["career_intent"]["score"] or 0,
    }

    projects = _read_project_evidence(candidate, raw, parsed_resume)
    claimed_certs = _as_list(candidate.get("certifications") or raw.get("certifications") or parsed_resume.get("certifications"))
    uploaded_certs = candidate.get("candidate_certificates") or []
    skill_evidence_component = (
        {"name": "skills_demonstrated", "points": 25 * freshness}
        if evidence_level >= EVIDENCE_DEMONSTRATED else
        {"name": "skills_corroborated", "points": 20 * freshness}
        if evidence_level >= EVIDENCE_CORROBORATED else
        {"name": "skills_claimed", "points": 10 * freshness}
        if evidence_level >= EVIDENCE_CLAIMED else None
    )
    return {
        "candidate_id": candidate.get("id") or candidate.get("candidate_id"),
        "calculator_result": result,
        "skills_capability": {
            "score": dimensions["skills_capability"]["score"],
            "signals": dimensions["skills_capability"]["signals"],
            "components": [
                {"name": "has_skills", "present": len(skills) >= 1, "points": 20 if len(skills) >= 1 else 0},
                {"name": "multiple_skills", "present": len(skills) >= 3, "points": 15 if len(skills) >= 3 else 0},
                {"name": "broad_skills", "present": len(skills) >= 6, "points": 10 if len(skills) >= 6 else 0},
                skill_evidence_component,
                {"name": "technical_descriptions", "present": role_category == "technical" and bool(technical_descriptions), "points": 10 if role_category == "technical" and technical_descriptions else 0},
            ],
            "claimed_skills": skills if evidence_level >= EVIDENCE_CLAIMED else [],
            "corroborated_skills": skills if evidence_level >= EVIDENCE_CORROBORATED else [],
            "demonstrated_skills": skills if evidence_level >= EVIDENCE_DEMONSTRATED else [],
            "technical_descriptions": technical_descriptions,
            "freshness_evidence": {**skill_evidence, "years_old": years_old, "freshness_factor": freshness},
        },
        "evidence_dimension": {
            "score": dimensions["evidence"]["score"],
            "signals": dimensions["evidence"]["signals"],
            "projects_detected": projects,
            "project_summary_present": _has_text(raw.get("project_summary")),
            "certifications_detected": claimed_certs,
            "uploaded_verified_certifications": uploaded_certs,
            "responsibilities_projects_from_voice": "responsibilities_projects" in voice["known_topics"],
            "described_experience": technical_descriptions,
            "complete_experience_records": complete_records,
            "technical_assessment_score": None,
            "technical_assessment_applicable": False,
            "all_evidence_signals": evidence,
        },
        "career_readiness": {
            "score": dimensions["career_readiness"]["score"],
            "signals": dimensions["career_readiness"]["signals"],
            "missing_critical": dimensions["career_readiness"].get("missing_critical", []),
            "contributions": {
                key: {"score": score, "weight": readiness_weights[key], "contribution": score * readiness_weights[key]}
                for key, score in readiness_inputs.items()
            },
        },
        "final_calculation": result["calculation"],
        "missing_critical_information": result["missing_critical_information"],
        "recommended_next_actions": result["recommended_next_actions"],
        "explainability": result["explainability"],
        "inconsistencies": result["inconsistencies"],
        "evidence": evidence,
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def calculate_profile_strength_v2(
    candidate: dict,
    raw_data: Optional[dict] = None,
    prefs_row: Optional[dict] = None,
) -> dict:
    """
    Calculate the new layered Profile Strength and Recommendation Readiness.

    Returns a structured dict suitable for the API response (Phase 9).
    Also returns (percent, label) compatible fields for backward compatibility.

    Logging (Phase 16): logs dimension scores at INFO level using candidate id,
    never logs personal data.
    """
    raw = raw_data if isinstance(raw_data, dict) else _parse_raw(candidate.get("raw_data"))

    # Resolve parsed_resume fallback for fields not yet in DB columns
    parsed_resume = _parse_raw(candidate.get("parsed_resume_json"))

    def _prefer(primary: Any, fallback: Any) -> Any:
        if primary is not None and primary != "" and primary != []:
            return primary
        return fallback

    # Build enriched view (canonical DB columns win over parsed_resume_json).
    # A resume import is persisted in both the normalised columns and the
    # original parsed payload.  Older imports can legitimately have some of
    # the former empty, so do not discard that already-known profile data when
    # calculating completion.
    enriched = dict(candidate)
    enriched["raw_data"] = raw
    for field, fb in [
        ("name", parsed_resume.get("name")),
        ("email", parsed_resume.get("email")),
        ("phone", parsed_resume.get("phone")),
        ("location", parsed_resume.get("location")),
        ("current_role", parsed_resume.get("current_role") or parsed_resume.get("headline")),
        ("headline", parsed_resume.get("headline") or parsed_resume.get("current_role")),
        ("current_company", parsed_resume.get("current_company") or parsed_resume.get("company")),
        ("summary", parsed_resume.get("summary") or parsed_resume.get("bio")),
        ("experience_years", parsed_resume.get("experience_years") or parsed_resume.get("total_experience_years")),
        ("skills", parsed_resume.get("skills")),
        ("work_experience", parsed_resume.get("work_experience")),
        ("education", parsed_resume.get("education")),
        ("certifications", parsed_resume.get("certifications")),
    ]:
        enriched[field] = _prefer(enriched.get(field), fb)

    # Load canonical preferences
    prefs = get_canonical_preferences(enriched, prefs_row)

    # Build evidence map
    evidence = build_attribute_evidence(enriched, prefs_row)

    # Voice intake state
    vi_state = get_voice_intake_state(enriched)

    # Role category
    target_roles = prefs.get("preferred_roles") or raw.get("preferred_roles") or []
    if not isinstance(target_roles, list):
        target_roles = []
    role_category = _role_category(
        target_roles,
        _clean(enriched.get("current_role") or enriched.get("headline")),
    )

    # Fresher adjustment: for freshers, work_experience absence is not penalised
    is_fresher = _is_fresher(enriched, raw)

    # Score each dimension
    d1 = _score_identity_background(enriched, evidence)
    d2 = _score_skills_capability(enriched, evidence, role_category)
    d3 = _score_evidence(enriched, evidence, raw, role_category)
    d4 = _score_career_intent(enriched, prefs, raw, vi_state)
    d5 = _score_preferences_constraints(prefs, raw, vi_state)
    d6 = _score_behaviour_communication(enriched, vi_state)
    d7_input = {
        "identity_background": d1,
        "skills_capability": d2,
        "evidence": d3,
        "career_intent": d4,
        "preferences_constraints": d5,
    }
    d7 = _score_career_readiness(d7_input, role_category, prefs, vi_state)

    dim_scores = {
        "identity_background": d1,
        "skills_capability": d2,
        "evidence": d3,
        "career_intent": d4,
        "preferences_constraints": d5,
        "behaviour_communication": d6,
        "career_readiness": d7,
    }

    # Consistency check
    inconsistencies = _detect_inconsistencies(enriched, vi_state)

    # Recommendation confidence (Dimension 8)
    rec_conf = _score_recommendation_confidence(
        dim_scores, role_category, prefs, inconsistencies, vi_state
    )

    # Final profile strength: weighted average of 7 dimensions
    weights = _role_aware_profile_weight(role_category)
    total_weight = 0.0
    weighted_sum = 0.0
    for dim_key, w in weights.items():
        d = dim_scores.get(dim_key, {})
        s = d.get("score")
        if s is None:
            # Incomplete dimension (e.g. behaviour with no data): skip
            continue
        weighted_sum += float(s) * w
        total_weight += w

    weighted_contributions: dict[str, dict[str, float]] = {}
    for dim_key, weight in weights.items():
        dimension_score = dim_scores.get(dim_key, {}).get("score")
        if dimension_score is not None:
            weighted_contributions[dim_key] = {
                "score": round(float(dimension_score), 2),
                "weight": weight,
                "contribution": round(float(dimension_score) * weight, 2),
            }

    if total_weight > 0:
        raw_percent = weighted_sum / total_weight
    else:
        raw_percent = 0.0

    # Consistency penalty
    medium_issues = sum(1 for i in inconsistencies if i.get("severity") == "medium")
    raw_percent = max(raw_percent - medium_issues * 3, 0.0)

    # Phase 2 — 100% hard gate: weighted average alone cannot reach 100
    # A candidate reaches 100 only when role-critical requirements are ALL satisfied
    if raw_percent >= 99.5 and not _is_role_complete(
        dim_scores, role_category, prefs,
        inconsistencies=inconsistencies,
        rec_conf=rec_conf,
        evidence=evidence,
    ):
        raw_percent = min(raw_percent, 97.0)

    percent = int(round(min(raw_percent, 100.0)))

    if percent >= 80:
        label = "Strong"
    elif percent >= 55:
        label = "Developing"
    else:
        label = "Building"

    # Explainability
    explain = _build_explainability(dim_scores, prefs, evidence, vi_state)
    ninety_percent_guidance = _build_ninety_percent_guidance(percent, explain, dim_scores)

    # Structured dimension output for API
    def _dim_out(d: dict, key: str) -> dict:
        out: dict = {"score": d.get("score"), "signals": d.get("signals", [])}
        for field in ("components", "maximum", "earned_points"):
            if field in d:
                out[field] = d[field]
        if "ambiguity_flags" in d:
            out["ambiguity_flags"] = d["ambiguity_flags"]
        if "incomplete" in d:
            out["incomplete"] = d["incomplete"]
        if "known" in d:
            out["known"] = d["known"]
        if "unknown" in d:
            out["unknown"] = d["unknown"]
        if "missing_critical" in d:
            out["missing_critical"] = d["missing_critical"]
        return out

    # Build constraint profile for the matching engine (Phase 8/10)
    constraint_profile = _build_constraint_profile(prefs, raw)

    result = {
        "profile_strength": {
            "percent": percent,
            "label": label,
        },
        "recommendation_readiness": {
            "level": rec_conf["level"],
            "confidence": rec_conf["confidence"],
            "tier": rec_conf["recommendation_tier"],
            "gating_reason": rec_conf.get("gating_reason"),
        },
        "dimensions": {
            "identity_background": _dim_out(d1, "identity_background"),
            "skills_capability": _dim_out(d2, "skills_capability"),
            "evidence": _dim_out(d3, "evidence"),
            "career_intent": _dim_out(d4, "career_intent"),
            "preferences_constraints": _dim_out(d5, "preferences_constraints"),
            "behaviour_communication": _dim_out(d6, "behaviour_communication"),
            "career_readiness": _dim_out(d7, "career_readiness"),
            "recommendation_confidence": {
                "score": rec_conf["score"],
                "level": rec_conf["level"],
            },
        },
        "missing_critical_information": explain["missing"],
        "recommended_next_actions": explain["next_actions"],
        "explainability": explain,
        "ninety_percent_guidance": ninety_percent_guidance,
        "role_category": role_category,
        "is_fresher": is_fresher,
        "inconsistencies": inconsistencies,
        "constraint_profile": constraint_profile,
        "evidence": evidence,
        "calculation": {
            "weighted_categories": weighted_contributions,
            "included_weight": round(total_weight, 3),
            "weighted_percent_before_adjustments": round(weighted_sum / total_weight, 2) if total_weight else 0.0,
            "consistency_penalty": medium_issues * 3,
            "final_percent": percent,
        },
        # Backward-compatible fields
        "percent": percent,
        "label": label,
    }

    cid = candidate.get("id") or candidate.get("candidate_id") or "unknown"
    logger.info(
        "[profile_strength] candidate=%s percent=%d label=%s role_category=%s "
        "identity=%.0f skills=%.0f evidence=%.0f intent=%.0f prefs=%.0f "
        "behaviour=%s readiness=%.0f rec_confidence=%.0f tier=%s",
        cid, percent, label, role_category,
        d1.get("score") or 0,
        d2.get("score") or 0,
        d3.get("score") or 0,
        d4.get("score") or 0,
        d5.get("score") or 0,
        str(d6.get("score") or "n/a"),
        d7.get("score") or 0,
        rec_conf["score"],
        rec_conf["recommendation_tier"],
    )
    logger.info(
        "[profile_strength_breakdown] candidate=%s categories=%s final_percent=%d",
        cid,
        {key: value["contribution"] for key, value in weighted_contributions.items()},
        percent,
    )

    return result


# ---------------------------------------------------------------------------
# Phase 10 — Constraint profile for matching engine
# ---------------------------------------------------------------------------

def _build_constraint_profile(prefs: dict, raw: dict) -> dict:
    """
    Extract hard constraints and strong preferences for the matching engine.
    Returns a structured dict the matcher can use to filter/penalise incompatible jobs.
    """
    remote_pref = _clean(prefs.get("remote_preference") or raw.get("work_type_preference")).lower()
    willing_relocate = prefs.get("willing_to_relocate")

    # Classify work mode constraint
    if "remote" in remote_pref and "only" in remote_pref:
        work_mode_constraint = "hard_remote_only"
    elif "remote" in remote_pref:
        work_mode_constraint = "prefers_remote"
    elif "onsite" in remote_pref or "on-site" in remote_pref or "office" in remote_pref:
        work_mode_constraint = "prefers_onsite"
    elif "hybrid" in remote_pref:
        work_mode_constraint = "prefers_hybrid"
    else:
        work_mode_constraint = "unknown"

    # Salary minimum
    salary_raw = _clean(prefs.get("expected_salary") or raw.get("salary_expectation"))
    salary_min = None
    if salary_raw:
        # Extract first number found (handles "₹X LPA", "$X", "X per year", etc.)
        import re as _re
        nums = _re.findall(r"[\d,]+(?:\.\d+)?", salary_raw.replace(",", ""))
        if nums:
            try:
                salary_min = float(nums[0])
            except ValueError:
                pass

    # Availability / notice period
    notice_raw = _clean(prefs.get("notice_period") or raw.get("notice_period") or raw.get("availability")).lower()
    if "immediate" in notice_raw or "0" in notice_raw:
        availability_constraint = "immediate"
    elif notice_raw:
        availability_constraint = notice_raw
    else:
        availability_constraint = "unknown"

    return {
        "work_mode_constraint": work_mode_constraint,
        "willing_to_relocate": willing_relocate,
        "salary_min": salary_min,
        "salary_raw": salary_raw or None,
        "availability_constraint": availability_constraint,
        "preferred_locations": prefs.get("preferred_locations") or [],
    }


def calculate_profile_strength_compat(
    candidate: dict,
    raw_data: Optional[dict] = None,
    prefs_row: Optional[dict] = None,
) -> tuple[int, str]:
    """
    Backward-compatible wrapper returning (percent, label).
    Used by _calculate_profile_strength in server.py.
    """
    result = calculate_profile_strength_v2(candidate, raw_data, prefs_row)
    return result["percent"], result["label"]
