"""
Kore — Repository: Search operations.
FTS5, semantic search, tag search, timeline.
"""

from __future__ import annotations

from datetime import UTC, datetime

from ..database import get_connection
from ..decay import effective_score, should_forget
from ..models import MemoryRecord
from .memory import _embeddings_available


def search_memories(
    query: str,
    limit: int = 5,
    category: str | None = None,
    semantic: bool = True,
    agent_id: str = "default",
    cursor: tuple[float, int] | None = None,
) -> tuple[list[MemoryRecord], tuple[float, int] | None, int]:
    """
    Search memories with cursor-based pagination.

    Returns: (results, next_cursor, total_count)
    - results: list of MemoryRecord
    - next_cursor: (decay_score, id) for next page, or None if no more results
    - total_count: total matching memories in DB (not just page size)

    Uses semantic (embedding) search when available,
    falls back to FTS5 full-text search, then LIKE.
    Filters out fully-decayed memories. Reinforces access count on results.
    """
    # Fetch extra results to ensure we have enough after filtering
    fetch_limit = limit * 3

    if semantic and _embeddings_available():
        results = _semantic_search(query, fetch_limit, category, agent_id, cursor)
    else:
        results = _fts_search(query, fetch_limit, category, agent_id, cursor)

    # Filter forgotten memories, re-rank by combined score:
    # similarity (semantic) × decay × importance_weight
    alive = [r for r in results if not should_forget(r.decay_score or 1.0)]
    alive.sort(
        key=lambda r: (
            (r.score if r.score and r.score > 0 else 1.0) * effective_score(r.decay_score or 1.0, r.importance)
        ),
        reverse=True,
    )

    # Get total count of matching active memories
    total_count = _count_active_memories(query, category, agent_id)

    # Take requested page + 1 to check if there are more results
    page = alive[: limit + 1]
    has_more = len(page) > limit
    top = page[:limit]

    # Generate next cursor if there are more results
    next_cursor = None
    if has_more and top:
        last = top[-1]
        next_cursor = (last.decay_score or 1.0, last.id)

    # Reinforce access for retrieved memories
    if top:
        _reinforce([r.id for r in top])

    return top, next_cursor, total_count


def get_timeline(
    subject: str,
    limit: int = 20,
    agent_id: str = "default",
    cursor: tuple[float, int] | None = None,
) -> tuple[list[MemoryRecord], tuple[float, int] | None, int]:
    """Return memories about a subject ordered by creation time with cursor pagination."""
    fetch_limit = limit * 2  # Fetch extra for sorting

    if _embeddings_available():
        results = _semantic_search(subject, fetch_limit, category=None, agent_id=agent_id, cursor=cursor)
    else:
        results = _fts_search(subject, fetch_limit, category=None, agent_id=agent_id, cursor=cursor)

    # Get total count
    total_count = _count_active_memories(subject, None, agent_id)

    # Sort by creation time (oldest first)
    sorted_results = sorted(results, key=lambda r: r.created_at)

    # Paginate
    page = sorted_results[: limit + 1]
    has_more = len(page) > limit
    top = page[:limit]

    next_cursor = None
    if has_more and top:
        last = top[-1]
        next_cursor = (last.decay_score or 1.0, last.id)

    return top, next_cursor, total_count


def search_by_tag(tag: str, agent_id: str = "default", limit: int = 20) -> list[MemoryRecord]:
    """Search memories by tag."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT m.id, m.content, m.category, m.importance,
                   m.decay_score, m.access_count, m.last_accessed,
                   m.created_at, m.updated_at, NULL AS score
            FROM memories m
            JOIN memory_tags t ON m.id = t.memory_id
            WHERE t.tag = ? AND m.agent_id = ? AND m.compressed_into IS NULL
              AND m.archived_at IS NULL
              AND (m.expires_at IS NULL OR m.expires_at > datetime('now'))
            ORDER BY m.importance DESC, m.created_at DESC
            LIMIT ?
            """,
            (tag.strip().lower(), agent_id, limit),
        ).fetchall()
    return [_row_to_record(r) for r in rows]


# ── Private helpers ──────────────────────────────────────────────────────────


def _count_active_memories(query: str, category: str | None, agent_id: str) -> int:
    """Count total active memories matching query (for pagination total)."""
    with get_connection() as conn:
        safe_query = _sanitize_fts_query(query)
        if safe_query:
            sql = """
                SELECT COUNT(*) FROM memories_fts
                JOIN memories m ON memories_fts.rowid = m.id
                WHERE memories_fts MATCH :query
                  AND m.agent_id = :agent_id
                  AND m.compressed_into IS NULL
                  AND m.archived_at IS NULL
                  AND m.decay_score >= 0.05
                  AND (m.expires_at IS NULL OR m.expires_at > datetime('now'))
            """
            params: dict = {"query": safe_query, "agent_id": agent_id}
        else:
            escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            sql = """
                SELECT COUNT(*) FROM memories
                WHERE content LIKE :query ESCAPE '\\'
                  AND agent_id = :agent_id
                  AND compressed_into IS NULL
                  AND archived_at IS NULL
                  AND decay_score >= 0.05
                  AND (expires_at IS NULL OR expires_at > datetime('now'))
            """
            params = {"query": f"%{escaped}%", "agent_id": agent_id}

        if category:
            # Prefix m. for FTS JOIN, no prefix for direct LIKE query
            col_prefix = "m." if safe_query else ""
            sql = sql.rstrip() + f" AND {col_prefix}category = :category"
            params["category"] = category

        return conn.execute(sql, params).fetchone()[0]


def _reinforce(memory_ids: list[int]) -> None:
    """Increment access_count and update last_accessed for retrieved memories."""
    now = datetime.now(UTC).isoformat()
    with get_connection() as conn:
        conn.executemany(
            """
            UPDATE memories
            SET access_count = access_count + 1,
                last_accessed = ?,
                decay_score   = MIN(1.0, decay_score + 0.05),
                updated_at    = ?
            WHERE id = ?
            """,
            [(now, now, mid) for mid in memory_ids],
        )


def _fts_search(
    query: str,
    limit: int,
    category: str | None,
    agent_id: str = "default",
    cursor: tuple[float, int] | None = None,
) -> list[MemoryRecord]:
    """Full-text search via SQLite FTS5 with prefix wildcards, scoped to agent."""
    with get_connection() as conn:
        safe_query = _sanitize_fts_query(query)

        cursor_filter = ""
        if cursor:
            decay_score, last_id = cursor
            cursor_filter = (
                "AND ((m.decay_score, m.id) < (:cursor_score, :cursor_id))"
                if safe_query
                else "AND ((decay_score, id) < (:cursor_score, :cursor_id))"
            )

        if safe_query:
            sql = """
                SELECT m.id, m.content, m.category, m.importance,
                       m.decay_score, m.access_count, m.last_accessed,
                       m.created_at, m.updated_at, rank AS score
                FROM memories_fts
                JOIN memories m ON memories_fts.rowid = m.id
                WHERE memories_fts MATCH :query
                  AND m.agent_id = :agent_id
                  AND m.compressed_into IS NULL
                  AND m.archived_at IS NULL
                  AND (m.expires_at IS NULL OR m.expires_at > datetime('now'))
                  {category_filter}
                  {cursor_filter}
                ORDER BY m.decay_score DESC, m.id DESC
                LIMIT :limit
            """
            params: dict = {"query": safe_query, "limit": limit, "agent_id": agent_id}
        else:
            sql = """
                SELECT id, content, category, importance,
                       decay_score, access_count, last_accessed,
                       created_at, updated_at, NULL AS score
                FROM memories
                WHERE content LIKE :query ESCAPE '\\'
                  AND agent_id = :agent_id
                  AND compressed_into IS NULL
                  AND archived_at IS NULL
                  AND (expires_at IS NULL OR expires_at > datetime('now'))
                  {category_filter}
                  {cursor_filter}
                ORDER BY decay_score DESC, id DESC
                LIMIT :limit
            """
            # q=* → list all memories (global wildcard)
            if query.strip() == "*":
                escaped_query = ""
            else:
                escaped_query = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            params = {"query": f"%{escaped_query}%", "limit": limit, "agent_id": agent_id}

        if cursor:
            params["cursor_score"] = cursor[0]
            params["cursor_id"] = cursor[1]

        category_filter = (
            "AND m.category = :category" if safe_query and category else "AND category = :category" if category else ""
        )
        if category:
            params["category"] = category

        rows = conn.execute(sql.format(category_filter=category_filter, cursor_filter=cursor_filter), params).fetchall()

    return [_row_to_record(r) for r in rows]


def _semantic_search(
    query: str,
    limit: int,
    category: str | None,
    agent_id: str = "default",
    cursor: tuple[float, int] | None = None,
) -> list[MemoryRecord]:
    """Semantic search with vector index, scoped to agent."""
    from ..embedder import embed_query
    from ..vector_index import get_index

    query_vec = embed_query(query)
    index = get_index()

    # Batch vector search via in-memory index
    top_ids = index.search(query_vec, agent_id, category=category, limit=limit)
    if not top_ids:
        return []

    # Load full records from DB with cursor filter
    id_score_map = {mem_id: score for mem_id, score in top_ids}
    placeholders = ",".join("?" for _ in top_ids)

    cursor_filter = ""
    params = [id for id, _ in top_ids]

    with get_connection() as conn:
        # Build query — parameter order: IN ids, category, cursor
        category_clause = "AND category = ?" if category else ""
        if category:
            params.append(category)

        if cursor:
            decay_score, last_id = cursor
            cursor_filter = "AND ((decay_score, id) < (?, ?))"
            params.extend([decay_score, last_id])

        sql = f"""
            SELECT id, content, category, importance,
                   decay_score, access_count, last_accessed,
                   created_at, updated_at
            FROM memories
            WHERE id IN ({placeholders})
              AND archived_at IS NULL
              AND (expires_at IS NULL OR expires_at > datetime('now'))
              {category_clause}
              {cursor_filter}
            ORDER BY decay_score DESC, id DESC
        """
        rows = conn.execute(sql, params).fetchall()

    results = []
    for row in rows:
        sim = id_score_map.get(row["id"], 0.0)
        results.append(
            MemoryRecord(
                id=row["id"],
                content=row["content"],
                category=row["category"],
                importance=row["importance"],
                decay_score=row["decay_score"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                score=round(sim, 4),
            )
        )

    # Sort by descending score
    results.sort(key=lambda r: r.score or 0.0, reverse=True)
    return results


def _sanitize_fts_query(query: str) -> str:
    """Sanitize FTS5 query: remove special operators, limit token count."""
    special = set('"^():-*+<>&|')
    cleaned = "".join(c if c not in special else " " for c in query).strip()
    if not cleaned:
        return ""
    # Max 10 tokens, min 2 characters each — prevents DoS
    tokens = [t for t in cleaned.split() if len(t) >= 2][:10]
    if not tokens:
        return ""
    # Quote for exact match, wildcard suffix for flexibility
    return " OR ".join(f'"{t}"*' for t in tokens)


def _row_to_record(row) -> MemoryRecord:
    return MemoryRecord(
        id=row["id"],
        content=row["content"],
        category=row["category"],
        importance=row["importance"],
        decay_score=row["decay_score"] if "decay_score" in row.keys() else 1.0,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        score=row["score"],
    )
