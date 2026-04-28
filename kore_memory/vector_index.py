"""
Kore — Vector index with sqlite-vec native search.

Strategy:
  - Uses sqlite-vec virtual table for native KNN search in SQLite
  - Vectors stored directly in vec0 table (not loaded in RAM)
  - Partition key by agent_id for efficient per-agent queries
  - Falls back to numpy in-memory approach if sqlite-vec is unavailable
  - distance_metric=cosine for normalized embeddings
"""

from __future__ import annotations

import logging
import struct
import threading
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# --- sqlite-vec availability ---
try:
    import sqlite_vec

    _HAS_SQLITE_VEC = True
except ImportError:
    _HAS_SQLITE_VEC = False

# --- numpy availability (optional, installed with [semantic]) ---
try:
    import numpy as np

    _HAS_NUMPY = True
except ImportError:
    np = None  # type: ignore[assignment]
    _HAS_NUMPY = False


def has_sqlite_vec() -> bool:
    """Check if sqlite-vec extension is available."""
    return _HAS_SQLITE_VEC


# ── sqlite-vec native index ─────────────────────────────────────────────────


class SqliteVecIndex:
    """Native vector search via sqlite-vec virtual table."""

    def __init__(self, dimensions: int = 384) -> None:
        self._dimensions = dimensions
        self._initialized_dbs: set[str] = set()
        self._lock = threading.Lock()

    def _ensure_table(self, conn) -> None:
        """Create vec_memories table if it doesn't exist."""
        db_path = str(conn.execute("PRAGMA database_list").fetchone()[2])
        if db_path in self._initialized_dbs:
            return

        with self._lock:
            if db_path in self._initialized_dbs:
                return

            try:
                conn.execute("SELECT 1 FROM vec_memories LIMIT 0")
            except Exception:
                conn.execute(f"""
                    CREATE VIRTUAL TABLE IF NOT EXISTS vec_memories USING vec0(
                        agent_id TEXT partition key,
                        embedding float[{self._dimensions}] distance_metric=cosine
                    )
                """)
                conn.commit()

            self._initialized_dbs.add(db_path)

    def upsert(self, conn, memory_id: int, agent_id: str, embedding: list[float]) -> None:
        """Insert or replace a vector in the index."""
        self._ensure_table(conn)
        vec_blob = _serialize_f32(embedding)
        # Delete existing entry if any, then insert
        conn.execute("DELETE FROM vec_memories WHERE rowid = ?", (memory_id,))
        conn.execute(
            "INSERT INTO vec_memories(rowid, agent_id, embedding) VALUES (?, ?, ?)",
            (memory_id, agent_id, vec_blob),
        )

    def remove(self, conn, memory_id: int) -> None:
        """Remove a vector from the index."""
        self._ensure_table(conn)
        conn.execute("DELETE FROM vec_memories WHERE rowid = ?", (memory_id,))

    def search(
        self,
        query_vec: list[float],
        agent_id: str,
        category: str | None = None,
        limit: int = 10,
        min_similarity: float = 0.1,
    ) -> list[tuple[int, float]]:
        """
        KNN search via sqlite-vec. Returns [(memory_id, similarity_score), ...].
        distance_metric=cosine returns cosine distance (1 - similarity),
        so we convert to similarity score.
        """
        from .database import get_connection

        vec_blob = _serialize_f32(query_vec)

        with get_connection() as conn:
            self._ensure_table(conn)

            # Load sqlite-vec extension on this connection
            _load_vec_extension(conn)

            rows = conn.execute(
                """
                SELECT rowid, distance
                FROM vec_memories
                WHERE embedding MATCH ?
                  AND agent_id = ?
                  AND k = ?
                """,
                (vec_blob, agent_id, limit * 2),
            ).fetchall()

        # Convert cosine distance to similarity score (1 - distance)
        results: list[tuple[int, float]] = []
        for row in rows:
            similarity = 1.0 - row[1]
            if similarity >= min_similarity:
                results.append((row[0], round(similarity, 4)))

        results.sort(key=lambda x: x[1], reverse=True)
        return results[:limit]

    def invalidate(self, agent_id: str) -> None:
        """No-op: sqlite-vec doesn't need cache invalidation."""
        pass

    def invalidate_all(self) -> None:
        """No-op: sqlite-vec doesn't need cache invalidation."""
        pass

    def sync_from_memories(self, conn) -> None:
        """Sync vec_memories table from memories table (migration/rebuild)."""
        self._ensure_table(conn)
        from .embedder import deserialize

        # Load extension for this connection
        _load_vec_extension(conn)

        # Clear and rebuild
        conn.execute("DELETE FROM vec_memories")

        rows = conn.execute(
            """
            SELECT id, agent_id, embedding FROM memories
            WHERE embedding IS NOT NULL
              AND compressed_into IS NULL
              AND archived_at IS NULL
            """
        ).fetchall()

        for row in rows:
            try:
                vec = deserialize(row["embedding"])
                vec_blob = _serialize_f32(vec)
                conn.execute(
                    "INSERT INTO vec_memories(rowid, agent_id, embedding) VALUES (?, ?, ?)",
                    (row["id"], row["agent_id"], vec_blob),
                )
            except Exception:
                continue  # skip corrupted embeddings

        conn.commit()
        logger.info("Synced %d vectors to sqlite-vec index", len(rows))


def _serialize_f32(vector: list[float]) -> bytes:
    """Serialize float list to raw float32 bytes for sqlite-vec."""
    return struct.pack(f"{len(vector)}f", *vector)


def _load_vec_extension(conn) -> None:
    """Load sqlite-vec extension on a connection (idempotent)."""
    if not _HAS_SQLITE_VEC:
        return
    try:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
    except Exception:
        pass  # already loaded or not available


# ── Legacy numpy in-memory index (fallback) ─────────────────────────────────


@dataclass
class _AgentCache:
    """Per-agent vector cache for legacy in-memory index."""

    vectors: dict[int, list[float]] = field(default_factory=dict)
    dirty: bool = True  # force reload on first access


class VectorIndex:
    """Legacy in-memory vector index with per-agent invalidation."""

    _MAX_AGENTS = 20  # Max cached agents; LRU eviction when exceeded

    def __init__(self) -> None:
        self._caches: dict[str, _AgentCache] = {}
        self._lock = threading.Lock()
        self._access_order: list[str] = []  # LRU tracking

    def get_cache(self, agent_id: str) -> _AgentCache:
        with self._lock:
            if agent_id not in self._caches:
                # Evict LRU if at capacity
                while len(self._caches) >= self._MAX_AGENTS and self._access_order:
                    evict = self._access_order.pop(0)
                    self._caches.pop(evict, None)
                self._caches[agent_id] = _AgentCache()
            # Update LRU order
            if agent_id in self._access_order:
                self._access_order.remove(agent_id)
            self._access_order.append(agent_id)
            return self._caches[agent_id]

    def invalidate(self, agent_id: str) -> None:
        """Invalidate cache for an agent (after save/delete/compress)."""
        with self._lock:
            if agent_id in self._caches:
                self._caches[agent_id].dirty = True

    def invalidate_all(self) -> None:
        """Invalidate all caches (after global decay pass)."""
        with self._lock:
            for cache in self._caches.values():
                cache.dirty = True

    def load_vectors(self, agent_id: str, category: str | None = None) -> dict[int, list[float]]:
        """
        Load/return vectors for an agent.
        If cache is dirty, reload from DB.
        Thread-safe: uses lock to avoid concurrent reloads.
        """
        with self._lock:
            if agent_id not in self._caches:
                self._caches[agent_id] = _AgentCache()
            cache = self._caches[agent_id]

            if cache.dirty:
                self._reload_from_db(agent_id, cache)

            return cache.vectors

    def search(
        self,
        query_vec: list[float],
        agent_id: str,
        category: str | None = None,
        limit: int = 10,
        min_similarity: float = 0.1,
    ) -> list[tuple[int, float]]:
        """
        Batch vector search: compute cosine similarity on all vectors
        and return top-k results as [(memory_id, score), ...].

        Uses numpy batch dot product when available for ~10-50x speedup.
        Falls back to pure Python if numpy is not installed.
        """
        vectors = self.load_vectors(agent_id, category)
        if not vectors:
            return []

        mem_ids = list(vectors.keys())

        if _HAS_NUMPY and mem_ids:
            # Batch computation: build matrix and compute all dot products at once
            matrix = np.array([vectors[mid] for mid in mem_ids], dtype=np.float32)
            query_arr = np.array(query_vec, dtype=np.float32)
            similarities = matrix @ query_arr  # shape: (n,)

            scored: list[tuple[int, float]] = [
                (mem_ids[i], float(similarities[i])) for i in range(len(mem_ids)) if similarities[i] >= min_similarity
            ]
        else:
            # Pure Python fallback
            scored = []
            for mem_id, vec in vectors.items():
                sim = sum(a * b for a, b in zip(query_vec, vec))
                if sim >= min_similarity:
                    scored.append((mem_id, sim))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:limit]

    def _reload_from_db(self, agent_id: str, cache: _AgentCache) -> None:
        """Reload all embeddings from DB for the agent."""
        from .database import get_connection
        from .embedder import deserialize

        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT id, embedding FROM memories
                WHERE embedding IS NOT NULL
                  AND compressed_into IS NULL
                  AND archived_at IS NULL
                  AND agent_id = ?
                """,
                (agent_id,),
            ).fetchall()

        cache.vectors = {}
        for row in rows:
            try:
                cache.vectors[row["id"]] = deserialize(row["embedding"])
            except Exception:
                continue  # corrupted embedding — skip

        cache.dirty = False


# ── Singleton instances ─────────────────────────────────────────────────────

_legacy_index = VectorIndex()
_vec_index = SqliteVecIndex() if _HAS_SQLITE_VEC else None


def get_index() -> VectorIndex | SqliteVecIndex:
    """Return the best available vector index (sqlite-vec preferred)."""
    if _vec_index is not None:
        return _vec_index
    return _legacy_index
