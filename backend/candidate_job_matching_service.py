import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy import text

from candidate_text import build_candidate_text
from embedding_service import generate_embedding
from qdrant_service import search_job_chunks

logger = logging.getLogger(__name__)

QDRANT_TOP_K = 150
MAX_RECOMMENDATIONS = 50

# Scoring weights (must sum to 1.0)
W_TARGET_ROLE = 0.35
W_SKILLS = 0.30
W_EXPERIENCE = 0.15
W_SEMANTIC = 0.20

# Evidence level weights for skill scoring (Phase 7/8)
_EVIDENCE_WEIGHT = {
    0: 0.3,   # Unknown
    1: 0.6,   # Claimed
    2: 0.8,   # Corroborated
    3: 1.0,   # Demonstrated
    4: 1.0,   # Verified
}


def _get_candidate_intelligence(candidate: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Compute candidate intelligence from profile_strength_service.
    Cached in candidate dict under '_intelligence' to avoid recomputation.
    """
    if "_intelligence" in candidate:
        return candidate["_intelligence"]
    try:
        from profile_strength_service import calculate_profile_strength_v2
        result = calculate_profile_strength_v2(candidate)
        candidate["_intelligence"] = result
        return result
    except Exception as exc:
        logger.warning("[matching] Could not compute candidate intelligence: %s", exc)
        return None


def _evidence_weighted_skills_score(
    candidate_skills: List[str],
    job_text: str,
    intelligence: Optional[Dict[str, Any]],
) -> float:
    """
    Score skills with evidence quality weighting.
    Demonstrated/verified skills count fully; claimed skills count at 0.6.
    """
    if not candidate_skills:
        return 0.0
    text_tokens = _phrase_set(job_text)
    evidence = (intelligence or {}).get("evidence") or {}
    skill_ev = evidence.get("skills", {})
    ev_level = skill_ev.get("evidence_level", 1)  # default: claimed
    base_weight = _EVIDENCE_WEIGHT.get(ev_level, 0.6)
    hits = sum(
        base_weight for s in candidate_skills
        if s and _term_in_text(_normalize(s), job_text, text_tokens)
    )
    return min(hits / len(candidate_skills), 1.0)


def _check_hard_constraints(
    constraint_profile: Dict[str, Any],
    job_text: str,
) -> Tuple[float, List[str]]:
    """
    Check candidate hard constraints against job text.
    Returns (penalty_multiplier, incompatibilities).
    1.0 = no penalty, 0.1 = near-disqualifying.
    """
    if not constraint_profile:
        return 1.0, []
    penalty = 1.0
    incompatibilities: List[str] = []
    job_lower = job_text.lower()

    work_mode = constraint_profile.get("work_mode_constraint", "unknown")
    if work_mode == "hard_remote_only":
        onsite_signals = [
            "on-site", "onsite", "on site", "in-office", "in office",
            "office based", "office-based", "must be present",
        ]
        if any(s in job_lower for s in onsite_signals):
            penalty *= 0.1
            incompatibilities.append("candidate_remote_only_job_requires_onsite")

    salary_min = constraint_profile.get("salary_min")
    if salary_min and salary_min > 0:
        nums = re.findall(r"[\d]+(?:\.\d+)?", job_lower.replace(",", ""))
        job_nums = [float(n) for n in nums if n]
        if job_nums:
            job_max = max(job_nums)
            if job_max < salary_min * 0.7:
                penalty *= 0.5
                incompatibilities.append("salary_below_candidate_minimum")

    return penalty, incompatibilities
EXPERIENCE_EPSILON_YEARS = 1e-6
_EXPERIENCE_MONTHS = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}


def _normalize(term: str) -> str:
    """Lowercase, collapse whitespace, normalize punctuation for safe whole-term comparison."""
    term = term.lower().strip()
    # Collapse internal whitespace
    term = re.sub(r"\s+", " ", term)
    return term


def _phrase_set(text: str) -> set:
    """
    Split text into a set of normalized single-word tokens AND keep the full
    normalized phrase so multi-word terms (e.g. 'react native') can be matched.
    Each token is the full word including punctuation chars like #, +, .
    so that 'c#', 'c++', '.net' are distinct from 'c'.
    """
    normalized = _normalize(text)
    # Word tokens that preserve tech punctuation: c#, c++, .net, node.js, etc.
    tokens = set(re.findall(r"[a-z0-9][a-z0-9#.+]*", normalized))
    return tokens


def _term_in_text(term: str, text: str, text_tokens: set) -> bool:
    """
    Return True only when the normalized term matches as a complete token (single-word)
    or as an exact phrase (multi-word) in the normalized text.
    Prevents 'java' matching 'javascript', 'c' matching 'c++', etc.
    """
    norm = _normalize(term)
    words = norm.split()
    if not words:
        return False
    if len(words) == 1:
        return words[0] in text_tokens
    # Multi-word: require the exact phrase to appear verbatim in the normalized text
    return norm in _normalize(text)


# Words that must not create a match by themselves — seniority and generic job-title words
_GENERIC_ROLE_WORDS = {
    # seniority / level
    "junior", "senior", "sr", "jr", "lead", "principal", "staff", "associate",
    "mid", "entry", "level", "i", "ii", "iii", "iv",
    # generic job-title nouns
    "developer", "engineer", "programmer", "specialist", "consultant",
    "architect", "manager", "analyst", "designer", "administrator",
    "director", "officer", "head", "expert", "professional",
}


def _role_tokens(role: str) -> set:
    """Return meaningful (non-generic) whole tokens from a role string."""
    return {t for t in _phrase_set(role) if t not in _GENERIC_ROLE_WORDS}


def _target_role_score(target_roles: List[str], job_title: str, job_text: str) -> float:
    """
    Token-overlap target-role scoring.
    All meaningful tokens from the candidate's target role must appear as whole
    tokens in the job title/text — preserving exact tech boundaries (java ≠ javascript).
    Score = (matched meaningful tokens / total meaningful tokens) weighted by
    title (0.8) vs full text (0.2).
    """
    if not target_roles:
        return 0.0
    title_tokens = _phrase_set(job_title)
    text_tokens = _phrase_set(job_text)
    best = 0.0
    for role in target_roles:
        role_toks = _role_tokens(role)
        if not role_toks:
            continue
        title_hit = len(role_toks & title_tokens) / len(role_toks)
        text_hit = len(role_toks & text_tokens) / len(role_toks)
        score = title_hit * 0.8 + text_hit * 0.2
        best = max(best, score)
    return min(best, 1.0)


def _skills_score(candidate_skills: List[str], job_text: str) -> float:
    """Fraction of candidate skills that appear as whole normalized terms in the job text."""
    if not candidate_skills:
        return 0.0
    text_tokens = _phrase_set(job_text)
    hits = sum(1 for s in candidate_skills if s and _term_in_text(_normalize(s), job_text, text_tokens))
    return min(hits / len(candidate_skills), 1.0)


def _experience_score(candidate_roles: List[str], job_title: str, job_text: str) -> float:
    """How well the candidate's past roles match the job title/description."""
    if not candidate_roles:
        return 0.0
    combined = job_title + " " + job_text
    combined_tokens = _phrase_set(combined)
    best = 0.0
    for role in candidate_roles:
        norm_role = _normalize(role)
        if not norm_role:
            continue
        score = 1.0 if _term_in_text(norm_role, combined, combined_tokens) else 0.0
        best = max(best, score)
    return min(best, 1.0)


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def _split_experience_range(text: str) -> list[str] | None:
    if not text:
        return None
    parts = [part.strip() for part in re.split(r"\s+(?:[\u2013\u2014-]|â€”)\s+", text, maxsplit=1)]
    if len(parts) == 2:
        return parts
    return None


def _is_open_ended_experience_value(value: Any) -> bool:
    normalized = _normalize_text(value).lower()
    return bool(normalized and re.search(r"\b(?:present|current|ongoing|now)\b", normalized))


def _parse_experience_date(value: Any, role: str = "end") -> datetime | None:
    text = _normalize_text(value)
    if not text or _is_open_ended_experience_value(text):
        return None
    cleaned = text.replace(".", "")

    if m := re.match(r"^(\d{4})$", cleaned):
        year = int(m.group(1))
        if role == "end":
            return datetime(year + 1, 1, 1, tzinfo=timezone.utc)
        return datetime(year, 1, 1, tzinfo=timezone.utc)

    if m := re.match(r"^(\d{4})[./](\d{1,2})$", cleaned):
        year = int(m.group(1))
        month = int(m.group(2))
        if not 1 <= month <= 12:
            return None
        if role == "end":
            if month == 12:
                return datetime(year + 1, 1, 1, tzinfo=timezone.utc)
            return datetime(year, month + 1, 1, tzinfo=timezone.utc)
        return datetime(year, month, 1, tzinfo=timezone.utc)

    if m := re.match(r"^([A-Za-z]{3,9})\s+(\d{4})$", cleaned):
        month = _EXPERIENCE_MONTHS.get(m.group(1).lower())
        if month is None:
            return None
        year = int(m.group(2))
        if role == "end":
            if month == 12:
                return datetime(year + 1, 1, 1, tzinfo=timezone.utc)
            return datetime(year, month + 1, 1, tzinfo=timezone.utc)
        return datetime(year, month, 1, tzinfo=timezone.utc)

    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%d/%m/%Y", "%m-%d-%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue

    if m := re.match(r"^(\d{4})[./](\d{1,2})[./](\d{1,2})$", cleaned):
        year, month, day = map(int, m.groups())
        return datetime(year, month, day, tzinfo=timezone.utc)

    return None


def _parse_experience_window(item: Any) -> tuple[datetime | None, datetime | None]:
    if not isinstance(item, dict):
        return None, None

    start = _parse_experience_date(item.get("start_date") or item.get("startDate"), "start")
    end = _parse_experience_date(item.get("end_date") or item.get("endDate"), "end")

    dates_text = _normalize_text(item.get("dates") or item.get("duration") or "")
    if dates_text:
        parts = _split_experience_range(dates_text)
        if parts:
            left, right = parts
            if start is None:
                start = _parse_experience_date(left, "start")
            if _is_open_ended_experience_value(right):
                end = None
            elif end is None:
                end = _parse_experience_date(right, "end")
        else:
            if start is None:
                start = _parse_experience_date(dates_text, "start")

    return start, end


def _candidate_total_experience_years(candidate: Dict[str, Any]) -> float:
    work_exp = candidate.get("work_experience") or []
    if isinstance(work_exp, str):
        try:
            work_exp = json.loads(work_exp)
        except Exception:
            work_exp = []

    intervals: list[tuple[datetime, datetime]] = []
    now = datetime.now(timezone.utc)

    for item in work_exp if isinstance(work_exp, list) else []:
        start, end = _parse_experience_window(item)
        if start is None:
            continue
        effective_end = end or now
        if effective_end < start:
            continue
        intervals.append((start, effective_end))

    if not intervals:
        return 0.0

    intervals.sort(key=lambda item: item[0])
    merged_days = 0.0
    current_start, current_end = intervals[0]

    for start, end in intervals[1:]:
        if start <= current_end:
            current_end = max(current_end, end)
        else:
            merged_days += (current_end - current_start).total_seconds() / 86400.0
            current_start, current_end = start, end

    merged_days += (current_end - current_start).total_seconds() / 86400.0
    return max(0.0, merged_days / 365.25)


def _job_text(job_title: str, job_description: str, job_requirements: Any = "", job_skills: Any = None) -> str:
    parts = [job_title or "", job_description or "", _normalize_text(job_requirements)]
    if isinstance(job_skills, list):
        skill_parts: list[str] = []
        for skill in job_skills:
            if isinstance(skill, dict):
                skill_parts.append(_normalize_text(skill.get("name") or skill.get("title") or skill.get("skill")))
            else:
                skill_parts.append(_normalize_text(skill))
        parts.append(", ".join(part for part in skill_parts if part))
    elif job_skills:
        parts.append(_normalize_text(job_skills))
    return " ".join(part for part in parts if part)


def _job_experience_bounds(job_text: str) -> tuple[float | None, float | None]:
    normalized = _normalize_text(job_text).lower()
    if not normalized:
        return None, None

    min_years: float | None = None
    max_years: float | None = None

    for pattern in (
        r"(?P<min>\d+(?:\.\d+)?)\s*[-–—]\s*(?P<max>\d+(?:\.\d+)?)\s*\+?\s*(?:years?|yrs?)",
        r"(?P<min>\d+(?:\.\d+)?)\s+(?:to)\s+(?P<max>\d+(?:\.\d+)?)\s*\+?\s*(?:years?|yrs?)",
    ):
        for match in re.finditer(pattern, normalized):
            low = float(match.group("min"))
            high = float(match.group("max"))
            min_years = low if min_years is None else max(min_years, low)
            max_years = high if max_years is None else min(max_years, high)

    for match in re.finditer(r"(?:minimum|min\.?|at least|requires?|requiring)?\s*(?P<years>\d+(?:\.\d+)?)\s*\+\s*(?:years?|yrs?)", normalized):
        years = float(match.group("years"))
        min_years = years if min_years is None else max(min_years, years)

    for match in re.finditer(r"(?:minimum|min\.?|at least|requires?|requiring)?\s*(?P<years>\d+(?:\.\d+)?)\s*(?:years?|yrs?)", normalized):
        years = float(match.group("years"))
        min_years = years if min_years is None else max(min_years, years)
        max_years = years if max_years is None else max_years

    if min_years is not None and max_years is not None and max_years < min_years:
        max_years = None

    return min_years, max_years


def _count_skill_matches(candidate_skills: List[str], job_text: str) -> int:
    if not candidate_skills:
        return 0
    text_tokens = _phrase_set(job_text)
    return sum(1 for skill in candidate_skills if skill and _term_in_text(_normalize(skill), job_text, text_tokens))


def _job_is_eligible(signals: Dict[str, Any], candidate_years: float, job_title: str, job_description: str, job_requirements: Any = "", job_skills: Any = None) -> bool:
    job_text = _job_text(job_title, job_description, job_requirements, job_skills)
    min_years, max_years = _job_experience_bounds(job_text)
    if min_years is not None and candidate_years + EXPERIENCE_EPSILON_YEARS < min_years:
        return False
    if max_years is not None and candidate_years - EXPERIENCE_EPSILON_YEARS > max_years:
        return False

    skill_hits = _count_skill_matches(signals["skills"], job_text)
    role_score = _target_role_score(signals["target_roles"], job_title, job_text)
    return skill_hits > 0 or role_score > 0.0


def _build_candidate_signals(candidate: Dict[str, Any]) -> Dict[str, Any]:
    """Extract matching signals from the candidate profile."""
    import json

    raw_data = candidate.get("raw_data") or {}
    if isinstance(raw_data, str):
        try:
            raw_data = json.loads(raw_data)
        except Exception:
            raw_data = {}

    # Target roles: preferred_roles (voice-stated desired roles) take priority, then current_role
    preferred_roles = raw_data.get("preferred_roles") or []
    current_role = (candidate.get("current_role") or "").strip()
    target_roles = list({r for r in preferred_roles if r})
    if current_role:
        target_roles.append(current_role)

    # Skills: merge DB skills with any voice-extracted skills stored in raw_data
    skills_raw = candidate.get("skills") or []
    if isinstance(skills_raw, str):
        try:
            skills_raw = json.loads(skills_raw)
        except Exception:
            skills_raw = []
    skills = [s["name"] if isinstance(s, dict) else str(s) for s in skills_raw if s]

    # Also include voice-extracted skills from raw_data (may not yet be merged into skills column)
    voice_skills = raw_data.get("skills") or []
    if isinstance(voice_skills, list):
        seen = {s.lower() for s in skills}
        for vs in voice_skills:
            name = vs["name"] if isinstance(vs, dict) else str(vs)
            if name and name.lower() not in seen:
                skills.append(name)
                seen.add(name.lower())

    # Past roles from work experience
    work_exp = candidate.get("work_experience") or []
    if isinstance(work_exp, str):
        try:
            work_exp = json.loads(work_exp)
        except Exception:
            work_exp = []
    past_roles = [w.get("title", "") for w in work_exp if w.get("title")]

    return {
        "target_roles": target_roles,
        "skills": skills,
        "past_roles": past_roles,
    }


def _extract_job_required_skills(job_text: str) -> List[str]:
    """
    Heuristically extract required skills from job text.
    Looks for skills listed after 'required:', 'requirements:', 'must have:',
    or in parenthetical skill lists. Falls back to all tech tokens.
    """
    lower = job_text.lower()
    # Look for explicit required section
    for marker in ("required:", "requirements:", "must have:", "you must have:",
                   "requires ", "requiring "):
        idx = lower.find(marker)
        if idx != -1:
            snippet = job_text[idx:idx + 300]
            # Extract comma/newline separated items
            items = re.split(r"[,\n;]", snippet)
            skills = []
            for item in items[1:8]:  # skip the marker itself
                item = item.strip().strip(".-•*")
                if 2 <= len(item) <= 40 and not item.lower().startswith(("and ", "or ", "the ")):
                    skills.append(item)
            if skills:
                return skills
    return []


def _skill_evidence_breakdown(
    candidate_skills: List[str],
    job_text: str,
    intelligence: Optional[Dict[str, Any]],
) -> Dict[str, List[str]]:
    """
    Classify each candidate skill that appears in the job text by evidence level.
    Returns {strong, partial, missing_required} lists for explainability.
    """
    text_tokens = _phrase_set(job_text)
    evidence = (intelligence or {}).get("evidence") or {}
    skill_ev = evidence.get("skills", {})
    ev_level = skill_ev.get("evidence_level", 1)

    strong, partial = [], []
    for s in candidate_skills:
        if not s:
            continue
        if _term_in_text(_normalize(s), job_text, text_tokens):
            if ev_level >= 3:  # DEMONSTRATED or VERIFIED
                strong.append(f"{s} — demonstrated")
            elif ev_level >= 2:  # CORROBORATED
                strong.append(f"{s} — corroborated")
            else:
                partial.append(f"{s} — claimed only")
    return {"strong": strong, "partial": partial}


def _compute_job_specific_confidence(
    signals: Dict[str, Any],
    job_title: str,
    job_text: str,
    hybrid_score: float,
    components: Dict[str, Any],
    intelligence: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Job-Specific Recommendation Confidence.

    Formula (avoids double-counting hybrid_score components):

      base = hybrid_score * 100          # already encodes target_role + skills + experience + semantic
      readiness_factor                   # scales base by candidate readiness (0.5 – 1.0)
      constraint_gate                    # hard override: near-disqualify on hard incompatibility
      evidence_gate                      # cap when required skills are only claimed / missing
      contradiction_penalty              # reduce for high-severity contradictions relevant to job

      raw_confidence = base * readiness_factor
      raw_confidence = min(raw_confidence, evidence_cap)
      raw_confidence -= contradiction_penalty
      if constraint_gate: raw_confidence = min(raw_confidence, 25)

    Keeps Profile Strength, Recommendation Readiness, and Job-Specific Confidence separate.
    """
    rec_readiness = (intelligence or {}).get("recommendation_readiness") or {}
    readiness_level = rec_readiness.get("level", "low")
    readiness_confidence = float(rec_readiness.get("confidence") or 0.0)

    # Readiness factor: scales base score — does NOT replace it
    # High readiness → full weight; Low readiness → 0.6 floor (job-critical info may still be known)
    if readiness_level == "high":
        readiness_factor = 1.0
    elif readiness_level == "medium":
        readiness_factor = 0.85
    else:
        # Low readiness: still allow high confidence if job-critical signals are strong
        # Use the hybrid score itself as the primary signal; readiness only damps slightly
        readiness_factor = 0.70

    base = hybrid_score * 100.0
    raw = base * readiness_factor

    # Evidence cap: if skills are only claimed (not corroborated/demonstrated), cap at 65
    evidence = (intelligence or {}).get("evidence") or {}
    skill_ev_level = evidence.get("skills", {}).get("evidence_level", 1)
    if skill_ev_level <= 1:  # claimed only
        evidence_cap = 65.0
    elif skill_ev_level == 2:  # corroborated
        evidence_cap = 85.0
    else:  # demonstrated / verified
        evidence_cap = 100.0
    raw = min(raw, evidence_cap)

    # Required-skill coverage cap: if job has explicit required skills and candidate
    # is missing some, cap confidence proportionally.
    # This prevents high confidence when semantic similarity is high but required skills absent.
    required_skills = _extract_job_required_skills(job_text)
    missing_required: List[str] = []
    if required_skills:
        candidate_skill_set = signals["skills"]
        for req in required_skills:
            req_norm = _normalize(req)
            if not any(
                req_norm in _normalize(s) or _normalize(s) in req_norm
                for s in candidate_skill_set if s
            ):
                missing_required.append(req)
        if missing_required:
            coverage = 1.0 - (len(missing_required) / len(required_skills))
            # 0 coverage → max 40; full coverage → no cap
            required_cap = 40.0 + coverage * 55.0
            raw = min(raw, required_cap)

    # Hard constraint gate: near-disqualify
    incompatibilities = components.get("incompatibilities") or []
    has_hard_constraint = bool(incompatibilities)
    if has_hard_constraint:
        raw = min(raw, 25.0)

    # Contradiction penalty: high-severity contradictions relevant to job reduce confidence
    inconsistencies = (intelligence or {}).get("inconsistencies") or []
    high_sev = sum(1 for i in inconsistencies if i.get("severity") == "high")
    medium_sev = sum(1 for i in inconsistencies if i.get("severity") == "medium")
    raw -= high_sev * 12 + medium_sev * 4
    raw = max(raw, 0.0)

    score = int(round(min(raw, 100.0)))

    if has_hard_constraint or score < 30:
        level = "low"
        tier = "near_disqualified" if has_hard_constraint else "limited"
    elif score >= 70:
        level = "high"
        tier = "strong_personalized"
    elif score >= 45:
        level = "medium"
        tier = "broader_matching"
    else:
        level = "low"
        tier = "limited"

    # Build match explanation
    skill_breakdown = _skill_evidence_breakdown(signals["skills"], job_text, intelligence)
    strong_reasons = list(skill_breakdown["strong"])
    partial_reasons = list(skill_breakdown["partial"])

    if components.get("target_role_score", 0) >= 0.5:
        strong_reasons.insert(0, "Target role matches")
    if components.get("experience_score", 0) >= 0.5:
        strong_reasons.append("Experience requirement satisfied")

    constraint_notes = []
    if has_hard_constraint:
        for inc in incompatibilities:
            if inc == "candidate_remote_only_job_requires_onsite":
                constraint_notes.append("Remote-only candidate — job requires onsite")
            elif inc == "salary_below_candidate_minimum":
                constraint_notes.append("Job salary below candidate minimum")
            else:
                constraint_notes.append(inc.replace("_", " "))
    elif components.get("constraint_penalty", 1.0) >= 1.0:
        if (intelligence or {}).get("constraint_profile", {}).get("work_mode_constraint") not in (None, "unknown"):
            constraint_notes.append("Work mode compatible")

    concerns = []
    for i in inconsistencies:
        if i.get("severity") in ("high", "medium"):
            concerns.append(i.get("description", ""))

    return {
        "recommendation_confidence": {
            "score": score,
            "level": level,
            "tier": tier,
        },
        "match_explanation": {
            "strong": strong_reasons,
            "partial": partial_reasons,
            "missing": [],  # populated by callers with job-required skills not in candidate
            "constraints": constraint_notes,
            "concerns": concerns,
        },
    }


def _hybrid_score(
    signals: Dict[str, Any],
    job_title: str,
    job_description: str,
    job_requirements: Any,
    job_skills: Any,
    semantic_score: float,
    intelligence: Optional[Dict[str, Any]] = None,
) -> Tuple[float, Dict[str, Any]]:
    """
    Compute a weighted hybrid score for one job.
    Integrates candidate intelligence (evidence quality + hard constraints).
    Returns (final_score, component_scores).
    """
    job_text = _job_text(job_title, job_description, job_requirements, job_skills)

    tr_score = _target_role_score(signals["target_roles"], job_title, job_text)
    sk_score = _evidence_weighted_skills_score(signals["skills"], job_text, intelligence)
    ex_score = _experience_score(signals["past_roles"], job_title, job_text)
    sem_score = max(0.0, min(1.0, float(semantic_score)))

    final = (
        W_TARGET_ROLE * tr_score
        + W_SKILLS * sk_score
        + W_EXPERIENCE * ex_score
        + W_SEMANTIC * sem_score
    )

    constraint_profile = (intelligence or {}).get("constraint_profile") or {}
    constraint_penalty, incompatibilities = _check_hard_constraints(constraint_profile, job_text)
    final *= constraint_penalty

    components: Dict[str, Any] = {
        "target_role_score": round(tr_score, 4),
        "skills_score": round(sk_score, 4),
        "experience_score": round(ex_score, 4),
        "semantic_score": round(sem_score, 4),
        "constraint_penalty": round(constraint_penalty, 4),
        "final_score": round(final, 4),
    }
    if incompatibilities:
        components["incompatibilities"] = incompatibilities

    # Job-specific confidence layer (built on top of hybrid score, not replacing it)
    job_confidence = _compute_job_specific_confidence(
        signals, job_title, job_text, final, components, intelligence
    )
    components.update(job_confidence)

    return final, components


async def refresh_candidate_job_matches(
    candidate_id: str,
    candidate: Dict[str, Any],
    SessionLocal: async_sessionmaker,
) -> None:
    """
    Build candidate embedding, search Qdrant, re-rank with hybrid scoring,
    and upsert into candidate_job_recommendations.
    """
    candidate_text = build_candidate_text(candidate)
    if not candidate_text.strip():
        logger.info("[matching] Candidate %s has no profile text — skipping", candidate_id)
        return

    query_vector = generate_embedding(candidate_text)

    # 1. Semantic search
    job_scores = search_job_chunks(query_vector, limit=QDRANT_TOP_K)
    if not job_scores:
        logger.info("[matching] No Qdrant results for candidate %s", candidate_id)
        return

    semantic_map = {jid: score for jid, score in job_scores}
    candidate_job_ids = [jid for jid, _ in job_scores]
    candidate_years = _candidate_total_experience_years(candidate)

    # 2. Fetch job details for all candidates
    async with SessionLocal() as db:
        placeholders = ", ".join(f":jid_{i}" for i in range(len(candidate_job_ids)))
        params = {f"jid_{i}": jid for i, jid in enumerate(candidate_job_ids)}
        rows = await db.execute(
            text(f"""
                SELECT id, title, description, requirements, skills
                FROM job_descriptions
                WHERE id::text IN ({placeholders})
                  AND (
                    is_active IS TRUE
                    OR status IN ('active', 'open', 'published')
                    OR job_status IN ('active', 'open', 'published')
                  )
            """),
            params,
        )
        job_details = {str(r[0]): {
            "title": r[1] or "",
            "description": r[2] or "",
            "requirements": r[3] or "",
            "skills": r[4] or [],
        }
                       for r in rows.fetchall()}

    if not job_details:
        logger.info("[matching] No valid active jobs found for candidate %s", candidate_id)
        return

    # 3. Extract candidate signals once
    signals = _build_candidate_signals(candidate)

    # Compute candidate intelligence once (evidence quality + constraints)
    intelligence = _get_candidate_intelligence(candidate)

    # 4. Hybrid re-ranking
    scored: List[Tuple[str, float, Dict]] = []
    eligible_job_ids: list[str] = []
    for job_id, job_data in job_details.items():
        if not _job_is_eligible(
            signals,
            candidate_years,
            job_data["title"],
            job_data["description"],
            job_data["requirements"],
            job_data["skills"],
        ):
            logger.info(
                "[job-match] job=%r filtered out by experience/skills-role eligibility",
                job_data["title"],
            )
            continue
        sem = semantic_map.get(job_id, 0.0)
        final, components = _hybrid_score(
            signals,
            job_data["title"],
            job_data["description"],
            job_data["requirements"],
            job_data["skills"],
            sem,
            intelligence=intelligence,
        )
        scored.append((job_id, final, components))
        eligible_job_ids.append(job_id)
        logger.info(
            "[job-match] job=%r target_role_score=%.4f skills_score=%.4f "
            "experience_score=%.4f semantic_score=%.4f final_score=%.4f",
            job_data["title"],
            components["target_role_score"],
            components["skills_score"],
            components["experience_score"],
            components["semantic_score"],
            components["final_score"],
        )

    scored.sort(key=lambda x: x[1], reverse=True)
    ranked_jobs = scored[:MAX_RECOMMENDATIONS]

    # 5. Load existing recommendations to preserve tracked_at / hidden_at
    async with SessionLocal() as db:
        existing_rows = await db.execute(
            text("""
                SELECT job_id, id, tracked_at, hidden_at
                FROM candidate_job_recommendations
                WHERE candidate_id = :cid
            """),
            {"cid": candidate_id},
        )
        existing = {str(r[0]): {"id": str(r[1]), "tracked_at": r[2], "hidden_at": r[3]}
                    for r in existing_rows.fetchall()}

    # 6. Upsert recommendations
    import json as _json
    async with SessionLocal() as db:
        for rank, (job_id, score, components) in enumerate(ranked_jobs, start=1):
            ex = existing.get(job_id)
            match_reason = _json.dumps({"type": "hybrid_match", **components})
            if ex:
                await db.execute(
                    text("""
                        UPDATE candidate_job_recommendations
                        SET match_score = :score,
                            recommendation_rank = :rank,
                            match_reason = CAST(:match_reason AS jsonb),
                            generated_at = now()
                        WHERE id = :rid
                    """),
                    {"score": score, "rank": rank, "rid": ex["id"],
                     "match_reason": match_reason},
                )
            else:
                await db.execute(
                    text("""
                        INSERT INTO candidate_job_recommendations
                            (id, candidate_id, job_id, match_score, recommendation_rank,
                             match_reason, created_at, generated_at)
                        VALUES
                            (:id, :cid, :jid, :score, :rank,
                             CAST(:match_reason AS jsonb), now(), now())
                    """),
                    {
                        "id": str(uuid.uuid4()),
                        "cid": candidate_id,
                        "jid": job_id,
                        "score": score,
                        "rank": rank,
                        "match_reason": match_reason,
                    },
                )
        await db.commit()

    stale_job_ids = [job_id for job_id in existing.keys() if job_id not in set(eligible_job_ids)]
    if stale_job_ids:
        async with SessionLocal() as db:
            await db.execute(
                text("""
                    UPDATE candidate_job_recommendations
                    SET hidden_at = COALESCE(hidden_at, now())
                    WHERE candidate_id = :cid
                      AND job_id::text = ANY(CAST(:job_ids AS text[]))
                """),
                {"cid": candidate_id, "job_ids": stale_job_ids},
            )
            await db.commit()

    logger.info(
        "[matching] Upserted %d job recommendations for candidate %s",
        len(ranked_jobs), candidate_id,
    )
