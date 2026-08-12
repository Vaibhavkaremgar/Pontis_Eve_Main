import logging
import uuid
from typing import Any, Dict, List

from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy import text

from candidate_text import build_candidate_text
from embedding_service import generate_embedding
from qdrant_service import search_job_chunks

logger = logging.getLogger(__name__)

# Top-K chunks to retrieve from Qdrant (may map to fewer unique jobs)
QDRANT_TOP_K = 100
# Maximum recommendations to upsert
MAX_RECOMMENDATIONS = 50


async def refresh_candidate_job_matches(
    candidate_id: str,
    candidate: Dict[str, Any],
    SessionLocal: async_sessionmaker,
) -> None:
    """
    Build candidate embedding, search Qdrant job_chunks, fetch matching jobs
    from job_descriptions, and upsert into candidate_job_recommendations.

    Preserves existing tracked_at and hidden_at values.
    Does NOT touch rows for jobs not returned by Qdrant (keeps dismissed/tracked history).
    """
    # 1. Build candidate text and embed
    candidate_text = build_candidate_text(candidate)
    if not candidate_text.strip():
        logger.info("[matching] Candidate %s has no profile text — skipping", candidate_id)
        return

    query_vector = generate_embedding(candidate_text)

    # 2. Search Qdrant
    job_scores = search_job_chunks(query_vector, limit=QDRANT_TOP_K)
    if not job_scores:
        logger.info("[matching] No Qdrant results for candidate %s", candidate_id)
        return

    job_ids = [jid for jid, _ in job_scores[:MAX_RECOMMENDATIONS]]
    score_map = {jid: score for jid, score in job_scores[:MAX_RECOMMENDATIONS]}

    # 3. Fetch matching active jobs from job_descriptions in one query
    async with SessionLocal() as db:
        placeholders = ", ".join(f":jid_{i}" for i in range(len(job_ids)))
        params = {f"jid_{i}": jid for i, jid in enumerate(job_ids)}
        rows = await db.execute(
            text(f"""
                SELECT id FROM job_descriptions
                WHERE id::text IN ({placeholders})
                  AND (
                    is_active IS TRUE
                    OR status IN ('active', 'open', 'published')
                    OR job_status IN ('active', 'open', 'published')
                  )
            """),
            params,
        )
        valid_ids = {str(r[0]) for r in rows.fetchall()}

    if not valid_ids:
        logger.info("[matching] No valid active jobs found for candidate %s", candidate_id)
        return

    # Preserve Qdrant ranking order, only include valid jobs
    ranked_jobs = [(jid, score_map[jid]) for jid in job_ids if jid in valid_ids]

    # 4. Load existing recommendations to preserve tracked_at / hidden_at
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

    # 5. Upsert recommendations
    async with SessionLocal() as db:
        for rank, (job_id, score) in enumerate(ranked_jobs, start=1):
            ex = existing.get(job_id)
            if ex:
                # Update score and rank, preserve tracked_at and hidden_at
                await db.execute(
                    text("""
                        UPDATE candidate_job_recommendations
                        SET match_score = :score,
                            recommendation_rank = :rank,
                            generated_at = now()
                        WHERE id = :rid
                    """),
                    {"score": score, "rank": rank, "rid": ex["id"]},
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
                        "match_reason": '{"type": "semantic_match"}',
                    },
                )
        await db.commit()

    logger.info(
        "[matching] Upserted %d job recommendations for candidate %s",
        len(ranked_jobs), candidate_id,
    )
