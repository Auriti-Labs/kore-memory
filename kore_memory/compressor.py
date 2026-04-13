"""
Kore — Memory Compressor
Finds clusters of similar memories and merges them into a single richer record.

Strategy:
  1. Load all memories without a compressed_into reference
  2. For each pair, compute cosine similarity
  3. Cluster memories with similarity > threshold
  4. Merge each cluster into one record (union of content, max importance)
  5. Mark originals as compressed_into the new record
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from . import config
from .database import get_connection
from .embedder import cosine_similarity, deserialize
from .events import MEMORY_COMPRESSED, emit
from .models import MemorySaveRequest
from .repository import _compress_lock, save_memory

SIMILARITY_THRESHOLD = config.SIMILARITY_THRESHOLD
MAX_COMPRESSION_DEPTH = 3  # Limite massimo catena di compressione

# --- numpy availability (optional, installed with [semantic]) ---
try:
    import numpy as np

    _HAS_NUMPY = True
except ImportError:
    np = None  # type: ignore[assignment]
    _HAS_NUMPY = False


@dataclass
class CompressionResult:
    clusters_found: int
    memories_merged: int
    new_records_created: int


def run_compression(agent_id: str = "default") -> CompressionResult:
    """
    Full compression: finds similar memories and merges them.
    Thread-safe: only one run at a time.
    """
    if not _compress_lock.acquire(blocking=False):
        return CompressionResult(0, 0, 0)  # run already in progress

    try:
        return _run_compression_inner(agent_id)
    finally:
        _compress_lock.release()


def _run_compression_inner(agent_id: str = "default") -> CompressionResult:
    memories = _load_compressible_memories(agent_id)
    if len(memories) < 2:
        return CompressionResult(0, 0, 0)

    clusters = _find_clusters(memories)
    if not clusters:
        return CompressionResult(0, 0, 0)

    merged = 0
    created = 0
    for cluster in clusters:
        new_id = _merge_cluster(cluster, agent_id=agent_id)
        if new_id:
            merged += len(cluster)
            created += 1
            emit(
                MEMORY_COMPRESSED,
                {
                    "id": new_id,
                    "agent_id": agent_id,
                    "merged_ids": [m["id"] for m in cluster],
                    "cluster_size": len(cluster),
                },
            )

    return CompressionResult(
        clusters_found=len(clusters),
        memories_merged=merged,
        new_records_created=created,
    )


def _load_compressible_memories(agent_id: str = "default") -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, content, category, importance, embedding
            FROM memories
            WHERE compressed_into IS NULL AND archived_at IS NULL AND embedding IS NOT NULL AND agent_id = ?
            """,
            (agent_id,),
        ).fetchall()

    # Filtra memorie che sono già risultato di troppi livelli di compressione
    result = []
    for r in rows:
        result.append(dict(r))

    if not result:
        return result

    # Escludi memorie che hanno già raggiunto la profondità massima di compressione
    ids = [m["id"] for m in result]
    with get_connection() as conn:
        depth_map = _get_compression_depths(conn, ids)

    return [m for m in result if depth_map.get(m["id"], 0) < MAX_COMPRESSION_DEPTH]


def _get_compression_depths(conn, memory_ids: list[int]) -> dict[int, int]:
    """Calcola la profondità di compressione per ogni memoria.
    Depth 0 = memoria originale, 1 = risultato di una compressione, ecc."""
    if not memory_ids:
        return {}

    # Conta quante memorie puntano a ciascun id (quanti livelli di merge)
    placeholders = ",".join("?" for _ in memory_ids)
    rows = conn.execute(
        f"""
        WITH RECURSIVE chain(id, depth) AS (
            SELECT id, 0 FROM memories WHERE id IN ({placeholders})
            UNION ALL
            SELECT m.id, c.depth + 1
            FROM memories m
            JOIN chain c ON m.compressed_into = c.id
        )
        SELECT chain.id, MAX(chain.depth) AS max_depth
        FROM chain
        GROUP BY chain.id
        """,
        memory_ids,
    ).fetchall()

    return {row["id"]: row["max_depth"] for row in rows}


def _find_clusters(memories: list[dict]) -> list[list[dict]]:
    """
    Greedy clustering: finds groups of memories with similarity > threshold.

    Uses numpy matrix multiplication when available for O(n²) batch similarity
    computation (much faster than O(n²) pure Python pairwise comparisons).
    Falls back to pure Python if numpy is not installed.
    """
    # Pre-deserialize all vectors once
    vectors: dict[int, list[float]] = {}
    for mem in memories:
        try:
            vectors[mem["id"]] = deserialize(mem["embedding"])
        except Exception:
            continue

    # Filter to only memories with valid vectors
    valid_memories = [m for m in memories if m["id"] in vectors]

    if len(valid_memories) < 2:
        return []

    if _HAS_NUMPY:
        return _find_clusters_numpy(valid_memories, vectors)
    else:
        return _find_clusters_python(valid_memories, vectors)


_CHUNK_SIZE = 2000  # Max vectors per chunk to avoid OOM on large datasets


def _find_clusters_numpy(
    memories: list[dict],
    vectors: dict[int, list[float]],
) -> list[list[dict]]:
    """
    Numpy-accelerated clustering with chunked processing.

    For datasets up to CHUNK_SIZE: full n×n similarity matrix (fast, O(n²) memory).
    For larger datasets: chunked processing computes similarity in blocks,
    keeping memory usage bounded at O(CHUNK_SIZE × n) per iteration.
    """
    n = len(memories)
    mem_ids = [m["id"] for m in memories]
    matrix = np.array([vectors[mid] for mid in mem_ids], dtype=np.float32)

    if n <= _CHUNK_SIZE:
        # Small dataset: full similarity matrix in one shot
        return _cluster_full_matrix(memories, matrix, n)

    # Large dataset: chunked row-by-row similarity computation
    return _cluster_chunked(memories, matrix, n)


def _cluster_full_matrix(
    memories: list[dict],
    matrix: np.ndarray,
    n: int,
) -> list[list[dict]]:
    """Full n×n similarity matrix clustering (for small datasets)."""
    sim_matrix = matrix @ matrix.T  # shape: (n, n)

    used: set[int] = set()
    clusters: list[list[dict]] = []

    for i in range(n):
        if i in used:
            continue

        # Vectorized: find all j > i with similarity >= threshold
        sims = sim_matrix[i, i + 1 :]
        similar_mask = sims >= SIMILARITY_THRESHOLD
        similar_indices = np.where(similar_mask)[0] + (i + 1)

        # Filter out already used indices
        cluster_indices = [i]
        for j in similar_indices:
            if j not in used:
                cluster_indices.append(int(j))

        if len(cluster_indices) > 1:
            for idx in cluster_indices:
                used.add(idx)
            clusters.append([memories[idx] for idx in cluster_indices])

    return clusters


def _cluster_chunked(
    memories: list[dict],
    matrix: np.ndarray,
    n: int,
) -> list[list[dict]]:
    """Chunked similarity computation for large datasets (>CHUNK_SIZE vectors)."""
    used: set[int] = set()
    clusters: list[list[dict]] = []

    for chunk_start in range(0, n, _CHUNK_SIZE):
        chunk_end = min(chunk_start + _CHUNK_SIZE, n)

        # Compute similarity between chunk rows and ALL columns
        chunk_matrix = matrix[chunk_start:chunk_end]  # shape: (chunk, dim)
        sim_block = chunk_matrix @ matrix.T  # shape: (chunk, n)

        for local_i in range(chunk_end - chunk_start):
            global_i = chunk_start + local_i
            if global_i in used:
                continue

            # Only look at j > global_i to avoid double-counting
            sims = sim_block[local_i, global_i + 1 :]
            similar_mask = sims >= SIMILARITY_THRESHOLD
            similar_indices = np.where(similar_mask)[0] + (global_i + 1)

            cluster_indices = [global_i]
            for j in similar_indices:
                if j not in used:
                    cluster_indices.append(int(j))

            if len(cluster_indices) > 1:
                for idx in cluster_indices:
                    used.add(idx)
                clusters.append([memories[idx] for idx in cluster_indices])

    return clusters


def _find_clusters_python(
    memories: list[dict],
    vectors: dict[int, list[float]],
) -> list[list[dict]]:
    """
    Pure Python fallback: O(n²/2) pairwise cosine similarity comparisons.
    """
    used: set[int] = set()
    clusters: list[list[dict]] = []

    for i, mem_a in enumerate(memories):
        if mem_a["id"] in used:
            continue

        vec_a = vectors[mem_a["id"]]
        cluster = [mem_a]

        # Compare only with subsequent memories (avoids double comparisons)
        for mem_b in memories[i + 1 :]:
            if mem_b["id"] in used:
                continue
            if cosine_similarity(vec_a, vectors[mem_b["id"]]) >= SIMILARITY_THRESHOLD:
                cluster.append(mem_b)

        if len(cluster) > 1:
            for m in cluster:
                used.add(m["id"])
            clusters.append(cluster)

    return clusters


def _merge_cluster(cluster: list[dict], agent_id: str = "default") -> int | None:
    """
    Merge a cluster of memories into a single new record.
    Returns the id of the new merged record, or None on failure.
    """
    if not cluster:
        return None

    # Build merged content: combine unique sentences
    # Split on sentence boundaries (. ! ? followed by space/end) instead of pipe
    combined_parts = []
    seen = set()
    for mem in cluster:
        sentences = re.split(r"(?<=[.!?])\s+", mem["content"].strip())
        for s in sentences:
            s = s.strip()
            if s and s not in seen:
                seen.add(s)
                combined_parts.append(s)

    merged_content = " ".join(combined_parts)
    if len(merged_content) > 4000:
        merged_content = merged_content[:3997] + "..."

    # Use the most common category and highest importance
    categories = [m["category"] for m in cluster]
    merged_category = max(set(categories), key=categories.count)
    merged_importance = max(m["importance"] for m in cluster)

    # Save the new merged record
    req = MemorySaveRequest(
        content=merged_content,
        category=merged_category,
        importance=merged_importance,
    )
    new_id, _, _conflicts = save_memory(req, agent_id=agent_id)

    # Migrate tags and relations from originals to the new record, then mark as compressed
    ids = [m["id"] for m in cluster]
    with get_connection() as conn:
        # Copy unique tags to the new record
        placeholders = ",".join("?" for _ in ids)
        conn.execute(
            f"""INSERT OR IGNORE INTO memory_tags (memory_id, tag)
                SELECT ?, tag FROM memory_tags WHERE memory_id IN ({placeholders})""",
            [new_id, *ids],
        )

        # Relink relations: source_id -> new_id
        conn.execute(
            f"""UPDATE memory_relations SET source_id = ?
                WHERE source_id IN ({placeholders})""",
            [new_id, *ids],
        )
        # Relink relations: target_id -> new_id
        conn.execute(
            f"""UPDATE memory_relations SET target_id = ?
                WHERE target_id IN ({placeholders})""",
            [new_id, *ids],
        )
        # Remove any self-relations created by the relink
        conn.execute(
            "DELETE FROM memory_relations WHERE source_id = target_id",
        )

        # Mark originals as compressed
        conn.executemany(
            "UPDATE memories SET compressed_into = ? WHERE id = ?",
            [(new_id, mid) for mid in ids],
        )

    return new_id
