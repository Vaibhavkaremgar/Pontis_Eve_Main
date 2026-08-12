import os
import logging
from typing import List

logger = logging.getLogger(__name__)

EMBEDDING_MODEL_NAME = os.environ.get("EMBEDDING_MODEL_NAME", "all-MiniLM-L6-v2")
EMBEDDING_VERSION = os.environ.get("EMBEDDING_VERSION", "v2_structured")

_model = None


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        logger.info("Loading embedding model: %s", EMBEDDING_MODEL_NAME)
        _model = SentenceTransformer(EMBEDDING_MODEL_NAME)
    return _model


def generate_embedding(text: str) -> List[float]:
    """Generate a 384-dimensional embedding vector for the given text."""
    model = _get_model()
    vector = model.encode(text, normalize_embeddings=True)
    return vector.tolist()
