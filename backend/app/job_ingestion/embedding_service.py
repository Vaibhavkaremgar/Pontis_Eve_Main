import os
from typing import Any
import numpy as np
from sentence_transformers import SentenceTransformer
from dotenv import load_dotenv

load_dotenv()

_MODEL_NAME = os.getenv("EMBEDDING_MODEL_NAME", "all-MiniLM-L6-v2")
_model = SentenceTransformer(_MODEL_NAME)


def build_job_text(job: dict[str, Any]) -> str:
    parts = [
        f"Title: {job.get('title', '')}",
        f"Company: {job.get('company_name', '')}",
        f"Department: {job.get('department', '')}",
        f"Location: {job.get('location', '')}",
        f"Employment Type: {job.get('employment_type', '')}",
        f"Salary Range: {job.get('salary_range', '')}",
        f"Experience Level: {job.get('experience_level', '')}",
        f"Skills Required: {job.get('skills_required', '')}",
        f"Description: {job.get('description', '')}",
    ]
    return "\n".join(p for p in parts if not p.endswith(": ") and not p.endswith("None"))


def generate_job_embedding(job: dict[str, Any]) -> list[float]:
    text = build_job_text(job)
    embedding = _model.encode(text, normalize_embeddings=True)
    return embedding.tolist()
