import logging
import re
import uuid
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
    semantic_score: float,
) -> Tuple[float, Dict[str, float]]:
    """
    Compute a weighted hybrid score for one job.
    Returns (final_score, component_scores).
    """
    job_text = f"{job_title} {job_description}"

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

    # 2. Fetch job details for all candidates
    async with SessionLocal() as db:
        placeholders = ", ".join(f":jid_{i}" for i in range(len(candidate_job_ids)))
        params = {f"jid_{i}": jid for i, jid in enumerate(candidate_job_ids)}
        rows = await db.execute(
            text(f"""
                SELECT id, title, description
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
        job_details = {str(r[0]): {"title": r[1] or "", "description": r[2] or ""}
                       for r in rows.fetchall()}

    if not job_details:
        logger.info("[matching] No valid active jobs found for candidate %s", candidate_id)
        return

    # 3. Extract candidate signals once
    signals = _build_candidate_signals(candidate)

    # 4. Hybrid re-ranking
    scored: List[Tuple[str, float, Dict]] = []
    for job_id, job_data in job_details.items():
        sem = semantic_map.get(job_id, 0.0)
        final, components = _hybrid_score(
            signals,
            job_data["title"],
            job_data["description"],
            sem,
        )
        scored.append((job_id, final, components))
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

    logger.info(
        "[matching] Upserted %d job recommendations for candidate %s",
        len(ranked_jobs), candidate_id,
    )
