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
EVIDENCE_DEMONSTRATED = 3  # Project / assessment / interview
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
                return []
        return []

    preferred_roles = _jlist(p.get("preferred_roles")) or _jlist(raw.get("preferred_roles"))
    preferred_locations = _jlist(p.get("preferred_locations")) or _jlist(raw.get("location_preferences"))
    preferred_industries = _jlist(p.get("preferred_industries")) or _jlist(raw.get("target_industries"))
    employment_types = _jlist(p.get("employment_types")) or _jlist(raw.get("employment_types"))
    remote_preference = _clean(p.get("remote_preference")) or _clean(raw.get("work_type_preference"))
    notice_period = _clean(p.get("notice_period")) or _clean(raw.get("notice_period")) or _clean(raw.get("availability"))
    expected_salary = _clean(p.get("expected_salary")) or _clean(raw.get("salary_expectation"))
    willing_to_relocate = p.get("willing_to_relocate")
    open_to_opportunities = p.get("open_to_opportunities")

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


# ---------------------------------------------------------------------------
# Phase 1 — Item-level provenance / evidence model
# ---------------------------------------------------------------------------

def build_attribute_evidence(candidate: dict, prefs_row: Optional[dict] = None) -> dict:
    """
    Build a lightweight evidence map for key candidate attributes.

    Returns a dict keyed by attribute name, each value being:
      {source, evidence_level, timestamp, confidence}

    Sources: claimed_from_resume | provided_by_candidate | extracted_from_voice
             | demonstrated_in_assessment | verified_by_document | system_inferred
    """
    raw = _parse_raw(candidate.get("raw_data"))
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
        if _has_list(parsed_resume.get("certifications")) or _has_list(raw.get("certifications")):
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

    # Uploaded certificates = verified_by_document
    certs = candidate.get("candidate_certificates") or []
    if isinstance(certs, list) and len(certs) > 0:
        _add("certifications", "verified_by_document", EVIDENCE_VERIFIED, 0.9)

    # Interview scores = demonstrated
    tech_score = candidate.get("interview_technical_score")
    comm_score = candidate.get("interview_communication_score")
    if tech_score is not None:
        _add("skills", "demonstrated_in_assessment", EVIDENCE_DEMONSTRATED, min(float(tech_score) / 10.0, 1.0))
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
        dated = sum(1 for w in work_exp if isinstance(w, dict) and (w.get("start_date") or w.get("end_date")))
        if dated > 0:
            score += 10
            signals.append("dated_history")

    edu = candidate.get("education") or []
    if isinstance(edu, list) and len(edu) > 0:
        score += 5
        signals.append("education")

    return {"score": min(score, 100.0), "signals": signals}


def _score_skills_capability(candidate: dict, evidence: dict, role_category: str) -> dict:
    """Dimension 2: Skills & Capability — with freshness applied to evidence confidence."""
    score = 0.0
    signals = []
    now_ts = datetime.now(timezone.utc).timestamp()

    skills = candidate.get("skills") or []
    if not isinstance(skills, list):
        skills = []

    if len(skills) >= 1:
        score += 20
        signals.append("has_skills")
    if len(skills) >= 3:
        score += 15
        signals.append("multiple_skills")
    if len(skills) >= 6:
        score += 10
        signals.append("broad_skills")

    # Evidence quality bonus — apply freshness to skill evidence
    skill_ev = evidence.get("skills", {})
    ev_level = skill_ev.get("evidence_level", EVIDENCE_UNKNOWN)
    ev_ts = skill_ev.get("timestamp")
    years_old = _years_ago(ev_ts, now_ts) if ev_ts else None
    # Skills decay after 3 years of no evidence update
    freshness = _freshness_factor(years_old, decay_after=3.0, floor=0.6)

    if ev_level >= EVIDENCE_DEMONSTRATED:
        score += 25 * freshness
        signals.append("skills_demonstrated")
    elif ev_level >= EVIDENCE_CORROBORATED:
        score += 20 * freshness
        signals.append("skills_corroborated")
    elif ev_level >= EVIDENCE_CLAIMED:
        score += 10 * freshness
        signals.append("skills_claimed")

    # For technical roles: work experience descriptions mentioning tech
    if role_category == "technical":
        work_exp = candidate.get("work_experience") or []
        tech_descriptions = sum(
            1 for w in work_exp
            if isinstance(w, dict) and _has_text(w.get("description"))
        )
        if tech_descriptions > 0:
            score += 10
            signals.append("technical_descriptions")

    return {"score": min(score, 100.0), "signals": signals}


def _score_evidence(candidate: dict, evidence: dict, raw: dict, role_category: str) -> dict:
    """Dimension 3: Evidence"""
    score = 0.0
    signals = []

    # Projects
    projects = raw.get("projects") or candidate.get("projects") or []
    vi_state = get_voice_intake_state(candidate)
    has_projects = (
        (isinstance(projects, list) and len(projects) > 0)
        or _has_text(raw.get("project_summary"))
        or "responsibilities_projects" in vi_state.get("known_topics", [])
    )
    if has_projects:
        score += 25
        signals.append("projects")

    # Uploaded certificates
    certs = candidate.get("candidate_certificates") or []
    if isinstance(certs, list) and len(certs) > 0:
        score += 20
        signals.append("uploaded_certificates")

    # Interview scores
    tech_score = candidate.get("interview_technical_score")
    if tech_score is not None:
        score += 30
        signals.append("technical_assessment")

    # Voice demonstrated capability
    if "responsibilities_projects" in vi_state.get("known_topics", []):
        score += 15
        signals.append("voice_projects")

    # Work experience with descriptions (evidence of doing, not just claiming)
    work_exp = candidate.get("work_experience") or []
    described = sum(
        1 for w in work_exp
        if isinstance(w, dict) and _has_text(w.get("description"))
    )
    if described >= 1:
        score += 10
        signals.append("described_experience")

    # Non-technical roles: communication evidence counts here too
    if role_category in ("sales", "management"):
        comm_ev = evidence.get("communication", {})
        if comm_ev.get("evidence_level", 0) >= EVIDENCE_DEMONSTRATED:
            score += 20
            signals.append("communication_assessed")

    return {"score": min(score, 100.0), "signals": signals}


def _score_career_intent(candidate: dict, prefs: dict, raw: dict, vi_state: dict) -> dict:
    """Dimension 4: Career Intent"""
    score = 0.0
    signals = []
    ambiguity_flags = []

    preferred_roles = prefs.get("preferred_roles") or raw.get("preferred_roles") or []
    if not isinstance(preferred_roles, list):
        preferred_roles = []

    if len(preferred_roles) >= 1:
        score += 30
        signals.append("target_role_stated")
        # Penalise vague "anything" intent
        vague = any(
            r.lower().strip() in ("anything", "any role", "open to anything", "flexible")
            for r in preferred_roles
        )
        if vague:
            score -= 15
            ambiguity_flags.append("vague_role_preference")
    else:
        ambiguity_flags.append("no_target_role")

    if _has_text(candidate.get("current_role") or candidate.get("headline")):
        score += 15
        signals.append("current_role_known")

    # Career goals / summary
    if _has_text(candidate.get("summary")):
        score += 15
        signals.append("career_summary")

    # Voice-confirmed intent
    if "target_role" in vi_state.get("known_topics", []):
        score += 20
        signals.append("voice_confirmed_intent")

    if "career_preferences" in vi_state.get("known_topics", []):
        score += 10
        signals.append("career_preferences_known")

    preferred_industries = prefs.get("preferred_industries") or []
    if isinstance(preferred_industries, list) and len(preferred_industries) > 0:
        score += 10
        signals.append("target_industries")

    return {
        "score": min(max(score, 0.0), 100.0),
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
        has = (isinstance(val, list) and len(val) > 0) or _has_text(val) or val is True
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

    _check("preferred_roles", "preferred_roles", 20)
    _check("preferred_locations", "location_preferences", 15)
    _check("remote_preference", "remote_preference", 15, freshness_decay=1.0)  # time-sensitive
    _check("notice_period", "availability", 15, freshness_decay=0.5)           # highly time-sensitive
    _check("expected_salary", "salary_expectation", 10)
    _check("employment_types", "employment_types", 10)
    _check("preferred_industries", "target_industries", 10)

    willing = prefs.get("willing_to_relocate")
    if willing is not None:
        score_ref.append(5)
        signals.append("relocation_stated")
        known.append("relocation")
    else:
        unknown.append("relocation")

    score = float(sum(score_ref))

    return {
        "score": min(score, 100.0),
        "signals": signals,
        "known": known,
        "unknown": unknown,
    }


def _score_behaviour_communication(candidate: dict, vi_state: dict) -> dict:
    """Dimension 6: Behaviour & Communication"""
    score = 0.0
    signals = []

    # Interview communication score
    comm_score = candidate.get("interview_communication_score")
    if comm_score is not None:
        try:
            normalized = float(comm_score) / 10.0
            score += normalized * 60
            signals.append("interview_communication_score")
        except (TypeError, ValueError):
            pass

    # Voice intake: meaningful multi-turn conversation = communication evidence
    turn_count = vi_state.get("turn_count", 0)
    if turn_count >= 3:
        score += 30
        signals.append("voice_multi_turn")
    elif turn_count >= 1:
        score += 15
        signals.append("voice_single_turn")

    # Culture fit score
    culture_score = candidate.get("interview_culture_fit_score")
    if culture_score is not None:
        try:
            score += (float(culture_score) / 10.0) * 10
            signals.append("culture_fit_score")
        except (TypeError, ValueError):
            pass

    # If no behavioural evidence at all, return incomplete (not fabricated)
    if not signals:
        return {"score": None, "signals": [], "incomplete": True}

    return {"score": min(score, 100.0), "signals": signals, "incomplete": False}


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

    # Weighted composite — role-aware
    if role_category == "technical":
        weights = {"identity": 0.15, "skills": 0.35, "evidence": 0.35, "intent": 0.15}
    elif role_category == "sales":
        weights = {"identity": 0.20, "skills": 0.20, "evidence": 0.30, "intent": 0.30}
    else:
        weights = {"identity": 0.25, "skills": 0.25, "evidence": 0.25, "intent": 0.25}

    score = (
        identity * weights["identity"]
        + skills * weights["skills"]
        + evidence * weights["evidence"]
        + intent * weights["intent"]
    )

    signals = []
    missing_critical = []

    if intent < 30:
        missing_critical.append("career_intent_unclear")
    if skills < 20:
        missing_critical.append("skills_insufficient")
    if evidence < 20:
        missing_critical.append("evidence_weak")

    if vi_state.get("has_meaningful_content"):
        signals.append("voice_intake_completed_turns")

    return {
        "score": min(score, 100.0),
        "signals": signals,
        "missing_critical": missing_critical,
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
            "identity_background": 0.12,
            "skills_capability": 0.25,
            "evidence": 0.25,
            "career_intent": 0.15,
            "preferences_constraints": 0.10,
            "behaviour_communication": 0.05,
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
    return {
        "identity_background": 0.15,
        "skills_capability": 0.20,
        "evidence": 0.20,
        "career_intent": 0.18,
        "preferences_constraints": 0.12,
        "behaviour_communication": 0.08,
        "career_readiness": 0.07,
    }


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
        next_actions.append("Add projects or assessments that demonstrate your skills")
    else:
        missing.append("Skills")
        next_actions.append("Add your key skills to your profile")

    if _dim_score("evidence") >= 60:
        strong.append("Evidence of capability")
    elif _dim_score("evidence") < 30:
        needs_evidence.append("Projects or demonstrated work")
        next_actions.append("Add projects, assessments, or portfolio links")

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

    # Build enriched view (canonical DB columns win over parsed_resume_json)
    enriched = dict(candidate)
    enriched["raw_data"] = raw
    for field, fb in [
        ("name", parsed_resume.get("name")),
        ("current_role", parsed_resume.get("current_role") or parsed_resume.get("headline")),
        ("skills", parsed_resume.get("skills")),
        ("work_experience", parsed_resume.get("work_experience")),
        ("education", parsed_resume.get("education")),
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

    if total_weight > 0:
        raw_percent = weighted_sum / total_weight
    else:
        raw_percent = 0.0

    # Fresher adjustment: boost education/skills weight if no work history
    if is_fresher:
        edu = enriched.get("education") or []
        if isinstance(edu, list) and len(edu) > 0:
            raw_percent = min(raw_percent + 5, 100.0)

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

    # Structured dimension output for API
    def _dim_out(d: dict, key: str) -> dict:
        out: dict = {"score": d.get("score"), "signals": d.get("signals", [])}
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
        "role_category": role_category,
        "is_fresher": is_fresher,
        "inconsistencies": inconsistencies,
        "constraint_profile": constraint_profile,
        "evidence": evidence,
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
