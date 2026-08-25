import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

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


def _hybrid_score(
    signals: Dict[str, Any],
    job_title: str,
    job_description: str,
    job_requirements: Any,
    job_skills: Any,
    semantic_score: float,
) -> Tuple[float, Dict[str, float]]:
    """
    Compute a weighted hybrid score for one job.
    Returns (final_score, component_scores).
    """
    job_text = _job_text(job_title, job_description, job_requirements, job_skills)

    tr_score = _target_role_score(signals["target_roles"], job_title, job_text)
    sk_score = _skills_score(signals["skills"], job_text)
    ex_score = _experience_score(signals["past_roles"], job_title, job_text)
    sem_score = max(0.0, min(1.0, float(semantic_score)))

    final = (
        W_TARGET_ROLE * tr_score
        + W_SKILLS * sk_score
        + W_EXPERIENCE * ex_score
        + W_SEMANTIC * sem_score
    )

    components = {
        "target_role_score": round(tr_score, 4),
        "skills_score": round(sk_score, 4),
        "experience_score": round(ex_score, 4),
        "semantic_score": round(sem_score, 4),
        "final_score": round(final, 4),
    }
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
