import os
import uuid
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct
from dotenv import load_dotenv

load_dotenv()

QDRANT_URL = os.getenv("QDRANT_URL")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY")
EMBEDDING_VERSION = os.getenv("EMBEDDING_VERSION", "1")

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


def upsert_job_embedding(job_id: str, embedding: list[float], job: dict) -> None:
    point_id = uuid.UUID(job_id).int % (2**63)
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
                },
            )
        ],
    )
