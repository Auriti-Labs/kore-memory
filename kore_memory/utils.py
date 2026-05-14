"""
Kore — Utility functions.
Shared helpers used across modules without circular dependencies.
"""

# ── Embedding availability check ─────────────────────────────────────────────

_EMBEDDINGS_AVAILABLE: bool | None = None


def _embeddings_available() -> bool:
    """
    Check if sentence-transformers is available for semantic search.
    Lazy import to avoid heavy dependency at startup.
    """
    global _EMBEDDINGS_AVAILABLE
    if _EMBEDDINGS_AVAILABLE is None:
        try:
            import sentence_transformers  # noqa: F401

            _EMBEDDINGS_AVAILABLE = True
        except ImportError:
            _EMBEDDINGS_AVAILABLE = False
    return _EMBEDDINGS_AVAILABLE
