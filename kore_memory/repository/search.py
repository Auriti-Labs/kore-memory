"""
Kore — Repository: Search operations.
FTS5, semantic search, tag search, timeline.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta

from ..database import get_connection
from ..decay import should_forget
from ..models import MemoryRecord
from .memory import _embeddings_available

# ── Status e Conditions (issue #020) ─────────────────────────────────────────


def _compute_memory_status(row) -> str:
    """
    Deriva lo status strutturale della memoria dal suo stato nel DB.
    Mutually exclusive, priorità: superseded > archived > expired > compressed > active.
    """
    if _row_field(row, "invalidated_at"):
        return "superseded"
    if _row_field(row, "archived_at"):
        return "archived"
    valid_to = _row_field(row, "valid_to")
    if valid_to and valid_to < datetime.now(UTC).isoformat():
        return "expired"
    if _row_field(row, "compressed_into"):
        return "compressed"
    return "active"


def _compute_conditions(row, conflicted_ids: set[int] | None = None) -> list[str]:
    """
    Deriva le condizioni osservate della memoria (possono coesistere con qualsiasi status).

    - forgotten (decay < 0.05): la memoria rimane active ma è esclusa dal retrieval default
    - fading (0.10 < decay < 0.30): in declino ma ancora recuperabile
    - conflicted: ha conflitti irrisolti in memory_conflicts
    - low_confidence (confidence < 0.50)
    - stale: valid_to entro 7 giorni ma non ancora scaduta
    """
    conditions: list[str] = []
    decay = float(_row_field(row, "decay_score") or 1.0)
    confidence = float(_row_field(row, "confidence") or 1.0)

    if decay < 0.05:
        conditions.append("forgotten")
    elif 0.10 < decay < 0.30:
        conditions.append("fading")

    if conflicted_ids and _row_field(row, "id") in conflicted_ids:
        conditions.append("conflicted")

    if confidence < 0.50:
        conditions.append("low_confidence")

    valid_to = _row_field(row, "valid_to")
    if valid_to:
        now_str = datetime.now(UTC).isoformat()
        stale_threshold = (datetime.now(UTC) + timedelta(days=7)).isoformat()
        if now_str < valid_to <= stale_threshold:
            conditions.append("stale")

    return conditions


def _row_field(row, field: str):
    """Accesso sicuro a campo row — None se il campo non esiste."""
    try:
        return row[field]
    except (IndexError, KeyError):
        return None


def _load_conflicted_ids(memory_ids: list[int], agent_id: str) -> set[int]:
    """
    Carica in bulk gli ID delle memorie con conflitti irrisolti.
    Una sola query per tutto il result set — O(1) round-trip.
    """
    if not memory_ids:
        return set()
    placeholders = ",".join("?" for _ in memory_ids)
    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT DISTINCT memory_a_id, memory_b_id
            FROM memory_conflicts
            WHERE resolved_at IS NULL
              AND agent_id = ?
              AND (
                  memory_a_id IN ({placeholders})
                  OR memory_b_id IN ({placeholders})
              )
            """,
            [agent_id] + memory_ids + memory_ids,
        ).fetchall()
    conflicted: set[int] = set()
    ids_set = set(memory_ids)
    for row in rows:
        conflicted.add(row[0])
        conflicted.add(row[1])
    return conflicted & ids_set


def _load_embeddings(memory_ids: list[int]) -> dict[int, list[float]]:
    """
    Carica embeddings dalla tabella memories in bulk.
    Usato per il calcolo di task_relevance con cosine similarity.
    """
    if not memory_ids:
        return {}
    from ..embedder import deserialize

    placeholders = ",".join("?" for _ in memory_ids)
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT id, embedding FROM memories WHERE id IN ({placeholders}) AND embedding IS NOT NULL",
            memory_ids,
        ).fetchall()
    result: dict[int, list[float]] = {}
    for row in rows:
        try:
            result[row[0]] = deserialize(row[1])
        except Exception:
            pass
    return result


# ── M1: RRF Fusion + Graph Search ────────────────────────────────────────────

# Default RRF weights (configurable via env)
_RRF_W_FTS = float(os.getenv("KORE_RRF_W_FTS", "0.30"))
_RRF_W_VEC = float(os.getenv("KORE_RRF_W_VEC", "0.50"))
_RRF_W_GRAPH = float(os.getenv("KORE_RRF_W_GRAPH", "0.20"))


def _rrf_fusion(
    fts_stream: list[tuple[int, float]],
    vec_stream: list[tuple[int, float]],
    graph_stream: list[tuple[int, float]],
    k: int = 60,
) -> list[tuple[int, float]]:
    """Reciprocal Rank Fusion across 3 streams with weight renormalization."""
    streams = []
    weights = []
    if fts_stream:
        streams.append(fts_stream)
        weights.append(_RRF_W_FTS)
    if vec_stream:
        streams.append(vec_stream)
        weights.append(_RRF_W_VEC)
    if graph_stream:
        streams.append(graph_stream)
        weights.append(_RRF_W_GRAPH)

    if not streams:
        return []

    # Renormalize weights
    total_w = sum(weights)
    weights = [w / total_w for w in weights]

    scores: dict[int, float] = {}
    for stream, weight in zip(streams, weights):
        for rank, (mem_id, _) in enumerate(stream, start=1):
            scores[mem_id] = scores.get(mem_id, 0.0) + weight / (k + rank)

    return sorted(scores.items(), key=lambda x: x[1], reverse=True)


def _graph_search(
    query: str,
    agent_id: str,
    limit: int,
    include_historical: bool = False,
) -> list[tuple[int, float]]:
    """Graph search stream: extract entities from query → match → BFS 1-hop → memory IDs."""
    try:
        from ..integrations.entities import extract_graph_entities
        from .entity import find_entities_by_names
    except Exception:
        return []

    entities = extract_graph_entities(query)
    if not entities:
        return []

    names = [name for name, _ in entities[:10]]
    matched = find_entities_by_names(names, agent_id=agent_id)
    if not matched:
        return []

    entity_ids = [eid for eid, _, _ in matched]
    total_query_entities = len(entity_ids)
    placeholders = ",".join("?" for _ in entity_ids)

    validity_clause = (
        ""
        if include_historical
        else "AND m.invalidated_at IS NULL AND (m.valid_to IS NULL OR m.valid_to > datetime('now'))"
    )

    try:
        with get_connection() as conn:
            rows = conn.execute(
                f"""
                SELECT mel.memory_id,
                       COUNT(DISTINCT mel.entity_id) AS entity_hits,
                       SUM(mel.confidence) AS total_confidence
                FROM memory_entity_links mel
                JOIN memories m ON m.id = mel.memory_id
                WHERE mel.entity_id IN ({placeholders})
                  AND m.agent_id = ?
                  AND m.archived_at IS NULL
                  AND m.compressed_into IS NULL
                  AND (m.expires_at IS NULL OR m.expires_at > datetime('now'))
                  {validity_clause}
                GROUP BY mel.memory_id
                ORDER BY entity_hits DESC, total_confidence DESC
                LIMIT ?
                """,
                (*entity_ids, agent_id, limit),
            ).fetchall()
    except Exception:
        return []  # graceful degradation (table may not exist)

    return [(r[0], r[1] / total_query_entities) for r in rows]


def _fts_search_ids(
    query: str,
    limit: int,
    category: str | None,
    agent_id: str,
    include_historical: bool = False,
) -> list[tuple[int, float]]:
    """FTS5 search returning (id, score) tuples for RRF fusion."""
    validity = (
        ""
        if include_historical
        else "AND (m.valid_to IS NULL OR m.valid_to > datetime('now')) AND m.invalidated_at IS NULL"
    )
    safe_query = _sanitize_fts_query(query)
    if not safe_query:
        return []

    cat_filter = "AND m.category = :category" if category else ""
    params: dict = {"query": safe_query, "limit": limit, "agent_id": agent_id}
    if category:
        params["category"] = category

    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT m.id, rank AS score
            FROM memories_fts
            JOIN memories m ON memories_fts.rowid = m.id
            WHERE memories_fts MATCH :query
              AND m.agent_id = :agent_id
              AND m.compressed_into IS NULL
              AND m.archived_at IS NULL
              AND (m.expires_at IS NULL OR m.expires_at > datetime('now'))
              {validity}
              {cat_filter}
            ORDER BY rank
            LIMIT :limit
            """,
            params,
        ).fetchall()
    return [(r[0], float(r[1])) for r in rows]


def _vec_search_ids(
    query: str,
    limit: int,
    category: str | None,
    agent_id: str,
) -> list[tuple[int, float]]:
    """Vector search returning (id, score) tuples for RRF fusion."""
    if not _embeddings_available():
        return []
    try:
        from ..embedder import embed_query
        from ..vector_index import get_index

        query_vec = embed_query(query)
        index = get_index()
        return index.search(query_vec, agent_id, category=category, limit=limit)
    except Exception:
        return []


def _load_memories_by_ids(
    fused: list[tuple[int, float]],
    agent_id: str,
    include_historical: bool = False,
) -> list[MemoryRecord]:
    """Load MemoryRecords for fused IDs, preserving RRF score order."""
    if not fused:
        return []

    ids = [mid for mid, _ in fused]
    score_map = {mid: score for mid, score in fused}
    placeholders = ",".join("?" for _ in ids)

    validity = (
        ""
        if include_historical
        else "AND (valid_to IS NULL OR valid_to > datetime('now')) AND invalidated_at IS NULL"
    )

    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT id, content, category, importance,
                   decay_score, access_count, last_accessed,
                   created_at, updated_at, title,
                   valid_from, valid_to, invalidated_at, supersedes_id,
                   confidence, provenance, memory_type, archived_at,
                   compressed_into
            FROM memories
            WHERE id IN ({placeholders})
              AND archived_at IS NULL
              AND (expires_at IS NULL OR expires_at > datetime('now'))
              {validity}
            """,
            ids,
        ).fetchall()

    results = []
    for row in rows:
        record = _row_to_record(row)
        record.score = score_map.get(row["id"], 0.0)
        results.append(record)

    # Preserve RRF order
    id_order = {mid: i for i, mid in enumerate(ids)}
    results.sort(key=lambda r: id_order.get(r.id, 999999))
    return results


# ── Public search functions ──────────────────────────────────────────────────


def search_memories(
    query: str,
    limit: int = 5,
    category: str | None = None,
    semantic: bool = True,
    agent_id: str = "default",
    cursor: tuple[float, int] | None = None,
    include_historical: bool = False,
    include_forgotten: bool = False,
    task: str = "",
    ranking_profile: str = "default",
    explain: bool = False,
) -> tuple[list[MemoryRecord], tuple[float, int] | None, int, list[dict]]:
    """
    Search memories con cursor pagination, task_relevance e explain opzionale.

    Returns: (results, next_cursor, total_count, excluded)
    - results: MemoryRecord ordinati per score
    - next_cursor: (decay_score, id) per la pagina successiva, None se non ce ne sono
    - total_count: totale matching memorie nel DB
    - excluded: memorie filtrate (populate solo con explain=True)
    """
    fetch_limit = max(limit * 5, 30)

    # M1: 3-stream RRF fusion (FTS + Vector + Graph)
    fts_ids = _fts_search_ids(query, fetch_limit, category, agent_id, include_historical=include_historical)
    vec_ids = _vec_search_ids(query, fetch_limit, category, agent_id) if semantic else []
    graph_ids = _graph_search(query, agent_id, fetch_limit, include_historical=include_historical)

    fused = _rrf_fusion(fts_ids, vec_ids, graph_ids)
    results = _load_memories_by_ids(fused[:fetch_limit], agent_id, include_historical=include_historical)

    # Traccia memorie escluse (popola solo con explain=True)
    excluded: list[dict] = []
    if not include_forgotten:
        forgotten_out, results = _split_forgotten(results, explain)
        if explain:
            excluded.extend(forgotten_out)

    # Carica conflict IDs per le memorie nel result set
    result_ids = [r.id for r in results]
    conflicted_ids = _load_conflicted_ids(result_ids, agent_id) if result_ids else set()

    # Aggiorna conditions con conflicted
    for record in results:
        if record.id in conflicted_ids and "conflicted" not in record.conditions:
            record.conditions.append("conflicted")

    # Carica embeddings per task_relevance (solo se task fornito e embedder disponibile)
    embedding_map: dict[int, list[float]] = {}
    task_vec: list[float] | None = None
    if task and task.strip() and _embeddings_available():
        try:
            from ..embedder import embed_query

            task_vec = embed_query(task)
            embedding_map = _load_embeddings(result_ids)
        except Exception:
            pass

    # Re-rank con Ranking Engine v1.1
    from ..ranking import rank_results

    results = rank_results(
        results,
        conflict_ids=conflicted_ids,
        task=task,
        task_vec=task_vec,
        embedding_map=embedding_map,
        ranking_profile=ranking_profile,
        explain=explain,
    )

    total_count = _count_active_memories(query, category, agent_id, include_historical=include_historical)

    # Cursor-based pagination: skip results at or before cursor position
    if cursor:
        cursor_score, cursor_id = cursor
        skip = True
        filtered = []
        for r in results:
            if skip:
                if r.id == cursor_id:
                    skip = False
                    continue  # skip the cursor item itself
                # Also skip items with higher score (already seen)
                r_score = r.score or 0.0
                if r_score > cursor_score or (r_score == cursor_score and r.id > cursor_id):
                    continue
                # If we haven't found cursor_id but score is lower, stop skipping
                skip = False
            filtered.append(r)
        results = filtered

    page = results[: limit + 1]
    has_more = len(page) > limit
    top = page[:limit]

    next_cursor = None
    if has_more and top:
        last = top[-1]
        next_cursor = (last.score or last.decay_score or 1.0, last.id)

    if top:
        _reinforce([r.id for r in top])

    return top, next_cursor, total_count, excluded


def _split_forgotten(
    results: list[MemoryRecord],
    explain: bool,
) -> tuple[list[dict], list[MemoryRecord]]:
    """
    Separa le memorie forgotten dal result set.
    Ritorna (excluded_dicts, kept_records).
    """
    excluded: list[dict] = []
    kept: list[MemoryRecord] = []
    for r in results:
        if should_forget(r.decay_score or 1.0):
            if explain:
                excluded.append(
                    {
                        "id": r.id,
                        "reason": "decay_threshold",
                        "decay_score": r.decay_score,
                        "score_before_filter": r.score,
                    }
                )
            # scarta dalla lista kept
        else:
            kept.append(r)
    return excluded, kept


def get_timeline(
    subject: str,
    limit: int = 20,
    agent_id: str = "default",
    cursor: tuple[float, int] | None = None,
) -> tuple[list[MemoryRecord], tuple[float, int] | None, int]:
    """Return memories about a subject ordered by creation time with cursor pagination."""
    fetch_limit = limit * 2

    if _embeddings_available():
        results = _semantic_search(subject, fetch_limit, category=None, agent_id=agent_id, cursor=cursor)
    else:
        results = _fts_search(subject, fetch_limit, category=None, agent_id=agent_id, cursor=cursor)

    total_count = _count_active_memories(subject, None, agent_id)

    sorted_results = sorted(results, key=lambda r: r.created_at)

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
                   m.created_at, m.updated_at, NULL AS score,
                   m.provenance, m.memory_type, m.confidence,
                   m.valid_from, m.valid_to, m.supersedes_id, m.archived_at
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


def _count_active_memories(
    query: str,
    category: str | None,
    agent_id: str,
    include_historical: bool = False,
) -> int:
    """Count total active memories matching query (for pagination total)."""
    _vf = "AND (m.valid_to IS NULL OR m.valid_to > datetime('now')) AND m.invalidated_at IS NULL"
    validity_filter = "" if include_historical else _vf
    _vfd = "AND (valid_to IS NULL OR valid_to > datetime('now')) AND invalidated_at IS NULL"
    validity_filter_direct = "" if include_historical else _vfd

    with get_connection() as conn:
        safe_query = _sanitize_fts_query(query)
        if safe_query:
            sql = f"""
                SELECT COUNT(*) FROM memories_fts
                JOIN memories m ON memories_fts.rowid = m.id
                WHERE memories_fts MATCH :query
                  AND m.agent_id = :agent_id
                  AND m.compressed_into IS NULL
                  AND m.archived_at IS NULL
                  AND m.decay_score >= 0.05
                  AND (m.expires_at IS NULL OR m.expires_at > datetime('now'))
                  {validity_filter}
            """
            params: dict = {"query": safe_query, "agent_id": agent_id}
        else:
            # Caso speciale: q=* → conta tutte le memorie attive (wildcard globale)
            escaped = (
                "" if query.strip() == "*" else query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            )
            sql = f"""
                SELECT COUNT(*) FROM memories
                WHERE content LIKE :query ESCAPE '\\'
                  AND agent_id = :agent_id
                  AND compressed_into IS NULL
                  AND archived_at IS NULL
                  AND decay_score >= 0.05
                  AND (expires_at IS NULL OR expires_at > datetime('now'))
                  {validity_filter_direct}
            """
            params = {"query": f"%{escaped}%", "agent_id": agent_id}

        if category:
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
    include_historical: bool = False,
) -> list[MemoryRecord]:
    """Full-text search via SQLite FTS5 con prefix wildcards, scoped to agent."""
    validity_fts = (
        ""
        if include_historical
        else "AND (m.valid_to IS NULL OR m.valid_to > datetime('now')) AND m.invalidated_at IS NULL"
    )
    validity_direct = (
        "" if include_historical else "AND (valid_to IS NULL OR valid_to > datetime('now')) AND invalidated_at IS NULL"
    )

    with get_connection() as conn:
        safe_query = _sanitize_fts_query(query)

        cursor_filter = ""
        if cursor:
            cursor_filter = (
                "AND ((m.decay_score, m.id) < (:cursor_score, :cursor_id))"
                if safe_query
                else "AND ((decay_score, id) < (:cursor_score, :cursor_id))"
            )

        if safe_query:
            sql = f"""
                SELECT m.id, m.content, m.category, m.importance,
                       m.decay_score, m.access_count, m.last_accessed,
                       m.created_at, m.updated_at, rank AS score,
                       m.valid_from, m.valid_to, m.invalidated_at, m.supersedes_id,
                       m.confidence, m.provenance, m.memory_type, m.archived_at,
                       m.compressed_into
                FROM memories_fts
                JOIN memories m ON memories_fts.rowid = m.id
                WHERE memories_fts MATCH :query
                  AND m.agent_id = :agent_id
                  AND m.compressed_into IS NULL
                  AND m.archived_at IS NULL
                  AND (m.expires_at IS NULL OR m.expires_at > datetime('now'))
                  {validity_fts}
                  {{category_filter}}
                  {{cursor_filter}}
                ORDER BY m.decay_score DESC, m.id DESC
                LIMIT :limit
            """
            params: dict = {"query": safe_query, "limit": limit, "agent_id": agent_id}
        else:
            sql = f"""
                SELECT id, content, category, importance,
                       decay_score, access_count, last_accessed,
                       created_at, updated_at, NULL AS score,
                       valid_from, valid_to, invalidated_at, supersedes_id,
                       confidence, provenance, memory_type, archived_at,
                       compressed_into
                FROM memories
                WHERE content LIKE :query ESCAPE '\\'
                  AND agent_id = :agent_id
                  AND compressed_into IS NULL
                  AND archived_at IS NULL
                  AND (expires_at IS NULL OR expires_at > datetime('now'))
                  {validity_direct}
                  {{category_filter}}
                  {{cursor_filter}}
                ORDER BY decay_score DESC, id DESC
                LIMIT :limit
            """
            escaped_query = (
                "" if query.strip() == "*" else (query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_"))
            )
            params = {"query": f"%{escaped_query}%", "limit": limit, "agent_id": agent_id}

        if cursor:
            params["cursor_score"] = cursor[0]
            params["cursor_id"] = cursor[1]

        category_filter = (
            "AND m.category = :category" if safe_query and category else "AND category = :category" if category else ""
        )
        if category:
            params["category"] = category

        rows = conn.execute(
            sql.format(category_filter=category_filter, cursor_filter=cursor_filter),
            params,
        ).fetchall()

    return [_row_to_record(r) for r in rows]


def _semantic_search(
    query: str,
    limit: int,
    category: str | None,
    agent_id: str = "default",
    cursor: tuple[float, int] | None = None,
    include_historical: bool = False,
) -> list[MemoryRecord]:
    """Semantic search with vector index, scoped to agent."""
    from ..embedder import embed_query
    from ..vector_index import get_index

    query_vec = embed_query(query)
    index = get_index()

    top_ids = index.search(query_vec, agent_id, category=category, limit=limit)
    if not top_ids:
        return []

    id_score_map = {mem_id: score for mem_id, score in top_ids}
    placeholders = ",".join("?" for _ in top_ids)

    validity_clause = (
        "" if include_historical else "AND (valid_to IS NULL OR valid_to > datetime('now')) AND invalidated_at IS NULL"
    )

    cursor_filter = ""
    params = [id for id, _ in top_ids]

    with get_connection() as conn:
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
                   created_at, updated_at,
                   valid_from, valid_to, invalidated_at, supersedes_id,
                   confidence, provenance, memory_type, archived_at,
                   compressed_into
            FROM memories
            WHERE id IN ({placeholders})
              AND archived_at IS NULL
              AND (expires_at IS NULL OR expires_at > datetime('now'))
              {validity_clause}
              {category_clause}
              {cursor_filter}
            ORDER BY decay_score DESC, id DESC
        """
        rows = conn.execute(sql, params).fetchall()

    results = []
    for row in rows:
        sim = id_score_map.get(row["id"], 0.0)
        record = _row_to_record(row)
        record.score = round(sim, 4)
        results.append(record)

    results.sort(key=lambda r: r.score or 0.0, reverse=True)
    return results


def _sanitize_fts_query(query: str) -> str:
    """Sanitize FTS5 query: remove special operators, limit token count."""
    special = set('"^():-*+<>&|')
    cleaned = "".join(c if c not in special else " " for c in query).strip()
    if not cleaned:
        return ""
    tokens = [t for t in cleaned.split() if len(t) >= 2][:10]
    if not tokens:
        return ""
    return " OR ".join(f'"{t}"*' for t in tokens)


def _row_to_record(row) -> MemoryRecord:
    """Costruisce MemoryRecord da una row SQLite, computando status e conditions."""
    provenance_raw = _row_field(row, "provenance")
    provenance_dict = None
    if provenance_raw:
        try:
            provenance_dict = json.loads(provenance_raw)
        except (ValueError, TypeError):
            pass

    # conflicted_ids viene aggiornato post-hoc in search_memories (bulk load)
    return MemoryRecord(
        id=row["id"],
        content=row["content"],
        category=row["category"],
        importance=row["importance"],
        decay_score=_row_field(row, "decay_score") or 1.0,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        title=_row_field(row, "title"),
        score=_row_field(row, "score"),
        memory_type=_row_field(row, "memory_type") or "semantic",
        confidence=_row_field(row, "confidence") or 1.0,
        valid_from=_row_field(row, "valid_from"),
        valid_to=_row_field(row, "valid_to"),
        supersedes_id=_row_field(row, "supersedes_id"),
        provenance=provenance_dict,
        status=_compute_memory_status(row),
        conditions=_compute_conditions(row),  # conflicted aggiunto in search_memories
    )
