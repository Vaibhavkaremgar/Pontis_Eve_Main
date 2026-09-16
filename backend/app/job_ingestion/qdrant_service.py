import os
import uuid
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct
from dotenv import load_dotenv

load_dotenv()

QDRANT_URL = os.getenv("QDRANT_URL")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY")
EMBEDDING_MODEL_NAME = os.getenv("EMBEDDING_MODEL_NAME", "all-MiniLM-L6-v2")
EMBEDDING_VERSION = os.getenv("EMBEDDING_VERSION", "v2_structured")

COLLECTION_NAME = "job_chunks"
VECTOR_SIZE = 384

_client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)


def ensure_collection() -> None:
    existing = {c.name for c in _client.get_collections().collections}
    if COLLECTION_NAME not in existing:
        _client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
        )


def _point_id(job_id: str) -> int:
    return uuid.UUID(job_id).int % (2**63)


def _existing_payload(job_id: str) -> dict | None:
    points = _client.retrieve(
        collection_name=COLLECTION_NAME,
        ids=[_point_id(job_id)],
        with_payload=True,
        with_vectors=False,
    )
    return dict(points[0].payload or {}) if points else None


def upsert_job_embedding(job_id: str, embedding: list[float], job: dict, *, reindex: bool = False) -> bool:
    """Upsert a stable job point, requiring explicit replacement of incompatible vectors."""
    if len(embedding) != VECTOR_SIZE:
        raise ValueError(f"Expected {VECTOR_SIZE}-dimensional job embedding, got {len(embedding)}")
    existing = _existing_payload(job_id)
    if existing:
        existing_version = existing.get("embedding_version")
        existing_model = existing.get("embedding_model")
        incompatible = (
            (existing_version and existing_version != EMBEDDING_VERSION)
            or (existing_model and existing_model != EMBEDDING_MODEL_NAME)
        )
        if incompatible and not reindex:
            raise ValueError(
                "Existing point has incompatible embedding metadata "
                f"(version={existing_version!r}, model={existing_model!r}); "
                "rerun with reindex=True to replace it explicitly"
            )

    point_id = _point_id(job_id)
    _client.upsert(
        collection_name=COLLECTION_NAME,
        points=[
            PointStruct(
                id=point_id,
                vector=embedding,
                payload={
                    "job_id": job_id,
                    "ats_job_id": job.get("ats_job_id"),
                    "title": job.get("title"),
                    "company_name": job.get("company_name"),
                    "ats_type": job.get("ats_type"),
                    "embedding_version": EMBEDDING_VERSION,
                    "embedding_model": EMBEDDING_MODEL_NAME,
                },
            )
        ],
    )
    return True
