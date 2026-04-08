"""
Kore — Embedder (v3)
Lazy-loaded sentence embeddings using multilingual models.
Default: paraphrase-multilingual-MiniLM-L12-v2 (~120MB, 384 dim, 50+ languages)

Supports:
- sentence-transformers v5.x encode_query()/encode_document() for asymmetric search
- ONNX backend for faster inference (optional: pip install 'sentence-transformers[onnx]')
- Custom models via KORE_EMBED_MODEL env var
"""

from __future__ import annotations

import base64
import json
import logging
import struct
from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

from . import config

MODEL_NAME = config.EMBED_MODEL
MAX_EMBED_CHARS = config.MAX_EMBED_CHARS
ONNX_BACKEND = config.EMBED_BACKEND

logger = logging.getLogger(__name__)

# --- numpy availability (optional, installed with [semantic]) ---
try:
    import numpy as np

    _HAS_NUMPY = True
except ImportError:
    np = None  # type: ignore[assignment]
    _HAS_NUMPY = False


@lru_cache(maxsize=1)
def get_model() -> SentenceTransformer:
    """Load the model once and keep it cached."""
    from sentence_transformers import SentenceTransformer

    kwargs = {}
    if ONNX_BACKEND:
        kwargs["backend"] = "onnx"
        logger.info("Loading model %s with ONNX backend", MODEL_NAME)

    model = SentenceTransformer(MODEL_NAME, **kwargs)

    # Check if model supports asymmetric search (v5+ with prompts)
    has_prompts = hasattr(model, "prompts") and model.prompts
    logger.info(
        "Loaded model %s (dim=%d, asymmetric=%s)",
        MODEL_NAME,
        model.get_sentence_embedding_dimension(),
        bool(has_prompts),
    )
    return model


def _truncate(text: str, max_chars: int = MAX_EMBED_CHARS) -> str:
    """Truncate text to max chars to prevent OOM."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars]


def _has_asymmetric_support(model: SentenceTransformer) -> bool:
    """Check if model supports encode_query/encode_document (v5+ with prompts)."""
    return hasattr(model, "encode_query") and hasattr(model, "prompts") and bool(model.prompts)


def embed(text: str) -> list[float]:
    """Return the embedding vector for a single text (document mode)."""
    model = get_model()
    truncated = _truncate(text)

    if _has_asymmetric_support(model):
        vector = model.encode_document(truncated, normalize_embeddings=True)
    else:
        vector = model.encode(truncated, normalize_embeddings=True)

    return vector.tolist()


def embed_query(text: str) -> list[float]:
    """Return the embedding vector optimized for search queries."""
    model = get_model()
    truncated = _truncate(text)

    if _has_asymmetric_support(model):
        vector = model.encode_query(truncated, normalize_embeddings=True)
    else:
        vector = model.encode(truncated, normalize_embeddings=True)

    return vector.tolist()


def embed_batch(texts: list[str]) -> list[list[float]]:
    """Return embedding vectors for a list of texts (document mode)."""
    model = get_model()
    truncated = [_truncate(t) for t in texts]

    if _has_asymmetric_support(model):
        vectors = model.encode_document(truncated, normalize_embeddings=True, batch_size=32)
    else:
        vectors = model.encode(truncated, normalize_embeddings=True, batch_size=32)

    return [v.tolist() for v in vectors]


def get_dimensions() -> int:
    """Return the embedding dimension of the current model."""
    model = get_model()
    return model.get_sentence_embedding_dimension()


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Dot product of two normalized vectors = cosine similarity."""
    if _HAS_NUMPY:
        return float(np.dot(a, b))
    return sum(x * y for x, y in zip(a, b))


# --- Serialization: base64-encoded struct.pack (~50% smaller than JSON) ---


def serialize(vector: list[float]) -> str:
    """Serialize a float vector to a compact base64 string."""
    binary = struct.pack(f"{len(vector)}f", *vector)
    return base64.b64encode(binary).decode("ascii")


def deserialize(blob: str) -> list[float]:
    """
    Deserialize a vector from either base64 binary or legacy JSON format.
    Auto-detects format: if the string starts with '[' it's JSON, otherwise base64.
    """
    if blob.startswith("["):  # Legacy JSON format
        return json.loads(blob)
    binary = base64.b64decode(blob)
    count = len(binary) // 4
    return list(struct.unpack(f"{count}f", binary))
