import os
import logging
from typing import List, Tuple

logger = logging.getLogger(__name__)

QDRANT_URL = os.environ.get("QDRANT_URL", "")
QDRANT_API_KEY = os.environ.get("QDRANT_API_KEY", "")
JOB_COLLECTION = "job_chunks"

_client = None


def _get_client():
    global _client
    if _client is None:
        from qdrant_client import QdrantClient
        if not QDRANT_URL:
            raise RuntimeError("QDRANT_URL environment variable is not set")
        kwargs = {"url": QDRANT_URL}
        if QDRANT_API_KEY:
            kwargs["api_key"] = QDRANT_API_KEY
        logger.info("Connecting to Qdrant at %s", QDRANT_URL)
        _client = QdrantClient(**kwargs)
    return _client


def search_job_chunks(query_vector: List[float], limit: int = 50) -> List[Tuple[str, float]]:
    """
    Search the job_chunks Qdrant collection using COSINE similarity.
    Returns a list of (jobId, score) tuples, deduplicated by jobId (best score kept).
    """
    client = _get_client()
    results = client.search(
        collection_name=JOB_COLLECTION,
        query_vector=query_vector,
        limit=limit,
        with_payload=True,
    )

    seen: dict[str, float] = {}
    for hit in results:
        payload = hit.payload or {}
        job_id = payload.get("job_id")
        if not job_id or not isinstance(job_id, str):
            continue
        score = float(hit.score)
        if job_id not in seen or score > seen[job_id]:
            seen[job_id] = score

    # Return sorted by score descending
    return sorted(seen.items(), key=lambda x: x[1], reverse=True)
