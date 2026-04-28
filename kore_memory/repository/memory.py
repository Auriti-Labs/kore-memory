"""
Kore — Repository: Memory CRUD operations.
Save, get, update, delete, batch save, import/export.
"""

from __future__ import annotations

import hashlib
import os
import re as _re_mod
from datetime import UTC, datetime, timedelta

from ..database import _get_db_path, get_connection
from ..events import MEMORY_DELETED, MEMORY_SAVED, MEMORY_UPDATED, emit
from ..models import MemoryRecord, MemorySaveRequest, MemoryUpdateRequest
from ..scorer import auto_score

_EMBEDDINGS_AVAILABLE: bool | None = None


def _embeddings_available() -> bool:
    global _EMBEDDINGS_AVAILABLE
    if _EMBEDDINGS_AVAILABLE is None:
        try:
            import sentence_transformers  # noqa: F401

            _EMBEDDINGS_AVAILABLE = True
        except ImportError:
            _EMBEDDINGS_AVAILABLE = False
    return _EMBEDDINGS_AVAILABLE


# ── M1: Dedup + Title helpers ────────────────────────────────────────────────


def _content_hash(content: str) -> str:
    """SHA-256 hash of normalized content (trim + collapse whitespace, preserve case)."""
    normalized = _re_mod.sub(r"\s+", " ", content.strip())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:32]


def _auto_title(content: str) -> str:
    """Generate title from first sentence or first line, max 120 chars."""
    first_line = content.split("\n")[0].strip()
    # Try first sentence
    parts = _re_mod.split(r"(?<=[.!?])\s", first_line, maxsplit=1)
    title = parts[0].strip()
    return title[:120]


def _prepare_memory(req: MemorySaveRequest) -> dict:
    """
    Run the full pre-INSERT pipeline on a MemorySaveRequest.

    Steps: auto_score → infer_memory_type → privacy_filter → expires_at →
    valid_from/to → provenance → content_hash → title → structured extraction →
    serialize JSON fields.

    Returns a dict with all INSERT column values EXCEPT agent_id, session_id,
    and embedding (embedding is handled separately for batch efficiency).
    The dict also includes 'filtered_content' for downstream embedding/entity/conflict use.
    """
    import json as _json

    from ..models import infer_memory_type
    from ..privacy import privacy_filter
    from ..structured import extract_structured

    importance = req.importance if req.importance is not None else auto_score(req.content, req.category)
    memory_type = req.memory_type or infer_memory_type(req.category)
    filtered_content = privacy_filter(req.content)

    expires_at = None
    if req.ttl_hours:
        expires_at = (datetime.now(UTC) + timedelta(hours=req.ttl_hours)).isoformat()

    _fmt = "%Y-%m-%d %H:%M:%S"
    valid_from = req.valid_from.astimezone(UTC).strftime(_fmt) if req.valid_from else None
    valid_to = req.valid_to.astimezone(UTC).strftime(_fmt) if req.valid_to else None

    provenance_json = _json.dumps(req.provenance.model_dump()) if req.provenance else None
    content_hash = _content_hash(filtered_content)
    title = getattr(req, "title", None) or _auto_title(filtered_content)

    auto_facts, auto_concepts, auto_narrative = extract_structured(filtered_content)
    facts = getattr(req, "facts", None) or auto_facts
    concepts = getattr(req, "concepts", None) or auto_concepts
    narrative = getattr(req, "narrative", None) or auto_narrative

    return {
        "content": filtered_content,
        "category": req.category,
        "importance": importance,
        "expires_at": expires_at,
        "valid_from": valid_from,
        "valid_to": valid_to,
        "supersedes_id": req.supersedes_id,
        "confidence": req.confidence,
        "provenance": provenance_json,
        "memory_type": memory_type,
        "title": title,
        "content_hash": content_hash,
        "facts_json": _json.dumps(facts) if facts else None,
        "concepts_json": _json.dumps(concepts) if concepts else None,
        "narrative": narrative[:500] if narrative else None,
        "metadata_json": _json.dumps(req.metadata) if req.metadata else None,
        # Extra fields for post-commit steps (not INSERT columns)
        "filtered_content": filtered_content,
    }


_INSERT_SQL = """
    INSERT INTO memories (
        agent_id, content, category, importance, embedding, expires_at, session_id,
        valid_from, valid_to, supersedes_id, confidence, provenance, memory_type,
        title, content_hash, facts_json, concepts_json, narrative, metadata_json
    ) VALUES (
        :agent_id, :content, :category, :importance, :embedding, :expires_at, :session_id,
        :valid_from, :valid_to, :supersedes_id, :confidence, :provenance, :memory_type,
        :title, :content_hash, :facts_json, :concepts_json, :narrative, :metadata_json
    )
"""


def _update_vector_index(row_ids_and_blobs: list[tuple[int, str | None]], agent_id: str) -> None:
    """Update vector index for a list of (row_id, embedding_blob) pairs."""
    blobs = [(rid, b) for rid, b in row_ids_and_blobs if b is not None]
    if not blobs:
        return
    from ..vector_index import get_index, has_sqlite_vec

    index = get_index()
    if has_sqlite_vec():
        from ..embedder import deserialize

        with get_connection() as conn:
            for row_id, blob in blobs:
                try:
                    index.upsert(conn, row_id, agent_id, deserialize(blob))
                except Exception:
                    pass
    else:
        index.invalidate(agent_id)


def _post_commit(row_id: int, prepared: dict, agent_id: str) -> list[str]:
    """Run post-commit steps: emit event, entity extraction, conflict detection. Returns conflict IDs."""
    from .. import config as _cfg

    emit(MEMORY_SAVED, {"id": row_id, "agent_id": agent_id})

    if _cfg.ENTITY_EXTRACTION:
        from ..integrations.entities import auto_tag_entities

        try:
            auto_tag_entities(row_id, prepared["filtered_content"], agent_id)
        except Exception:
            pass

    conflicts: list[str] = []
    if _cfg.CONFLICT_SYNC:
        try:
            from ..conflict_detector import detect_conflicts

            conflicts = detect_conflicts(
                memory_id=row_id,
                content=prepared["filtered_content"],
                agent_id=agent_id,
                valid_from=prepared["valid_from"],
                valid_to=prepared["valid_to"],
                confidence=prepared["confidence"],
            )
        except Exception:
            pass
    return conflicts


def save_memory(
    req: MemorySaveRequest,
    agent_id: str = "default",
    session_id: str | None = None,
) -> tuple[int, int, list[str]]:
    """
    Persist a new memory record scoped to agent_id.
    Auto-scores importance if not explicitly set.

    Se req.supersedes_id è fornito, invalida atomicamente la memoria precedente
    nella stessa transazione (Correction D: single source of truth via supersedes_id).

    Returns (row_id, importance, conflicts_detected).
    conflicts_detected = lista di conflict IDs rilevati (lista vuota = nessun conflitto).
    """
    prepared = _prepare_memory(req)
    filtered_content = prepared["filtered_content"]
    importance = prepared["importance"]

    # Embed single content
    embedding_blob = None
    if _embeddings_available():
        from ..embedder import embed, serialize

        try:
            embedding_blob = serialize(embed(filtered_content))
        except Exception:
            pass

    # M1: dedup check (skip if supersedes_id set, KORE_DEDUP=0, or test mode)
    _dedup_enabled = (
        req.supersedes_id is None and os.getenv("KORE_DEDUP", "1") != "0" and os.getenv("KORE_TEST_MODE", "0") != "1"
    )

    now = datetime.now(UTC).isoformat()
    with get_connection() as conn:
        # Dedup check inside same transaction as INSERT — prevents TOCTOU race
        if _dedup_enabled:
            dup = conn.execute(
                "SELECT id, importance FROM memories WHERE agent_id = ? AND content_hash = ?"
                " AND created_at > datetime('now', '-5 minutes') AND compressed_into IS NULL LIMIT 1",
                (agent_id, prepared["content_hash"]),
            ).fetchone()
            if dup:
                return dup[0], dup[1], []

        if session_id:
            conn.execute("INSERT OR IGNORE INTO sessions (id, agent_id) VALUES (?, ?)", (session_id, agent_id))

        if req.supersedes_id is not None:
            conn.execute(
                "UPDATE memories SET invalidated_at = ? WHERE id = ? AND agent_id = ? AND invalidated_at IS NULL",
                (now, req.supersedes_id, agent_id),
            )

        params = {**prepared, "agent_id": agent_id, "embedding": embedding_blob, "session_id": session_id}
        del params["filtered_content"]
        cursor = conn.execute(_INSERT_SQL, params)
        row_id = cursor.lastrowid

    _update_vector_index([(row_id, embedding_blob)], agent_id)
    conflicts = _post_commit(row_id, prepared, agent_id)
    return row_id, importance, conflicts


def save_memory_batch(
    reqs: list[MemorySaveRequest],
    agent_id: str = "default",
    session_id: str | None = None,
) -> list[tuple[int, int, list]]:
    """
    Batch save: full pipeline per item, batch embeddings, single transaction INSERT.
    Returns list of (row_id, importance, conflicts_detected) tuples.
    """
    if not reqs:
        return []

    # Run full pre-INSERT pipeline per item
    prepared_list = [_prepare_memory(req) for req in reqs]

    # Batch embed all filtered contents at once
    embeddings: list[str | None] = [None] * len(reqs)
    if _embeddings_available():
        from ..embedder import embed_batch, serialize

        try:
            vectors = embed_batch([p["filtered_content"] for p in prepared_list])
            embeddings = [serialize(v) for v in vectors]
        except Exception:
            pass

    # Dedup check per item (before transaction)
    _dedup_on = os.getenv("KORE_DEDUP", "1") != "0" and os.getenv("KORE_TEST_MODE", "0") != "1"
    keep: list[tuple[dict, str | None]] = []
    deduped: list[tuple[int, int, list]] = []
    if _dedup_on:
        with get_connection() as conn:
            for i, prepared in enumerate(prepared_list):
                if reqs[i].supersedes_id is not None:
                    keep.append((prepared, embeddings[i]))
                    continue
                dup = conn.execute(
                    "SELECT id, importance FROM memories WHERE agent_id = ? AND content_hash = ?"
                    " AND created_at > datetime('now', '-5 minutes') AND compressed_into IS NULL LIMIT 1",
                    (agent_id, prepared["content_hash"]),
                ).fetchone()
                if dup:
                    deduped.append((dup[0], dup[1], []))
                else:
                    keep.append((prepared, embeddings[i]))
    else:
        keep = list(zip(prepared_list, embeddings))

    if not keep:
        return deduped

    # Single transaction for all inserts
    results: list[tuple[int, int, list, dict]] = []
    with get_connection() as conn:
        if session_id:
            conn.execute("INSERT OR IGNORE INTO sessions (id, agent_id) VALUES (?, ?)", (session_id, agent_id))

        now = datetime.now(UTC).isoformat()
        for prepared, emb in keep:
            if prepared["supersedes_id"] is not None:
                conn.execute(
                    "UPDATE memories SET invalidated_at = ? WHERE id = ? AND agent_id = ? AND invalidated_at IS NULL",
                    (now, prepared["supersedes_id"], agent_id),
                )
            params = {**prepared, "agent_id": agent_id, "embedding": emb, "session_id": session_id}
            del params["filtered_content"]
            cursor = conn.execute(_INSERT_SQL, params)
            results.append((cursor.lastrowid, prepared["importance"], [], prepared))

    # Post-commit: vector index (batch), events, entity extraction, conflict detection
    _update_vector_index([(rid, emb) for (_, emb), (rid, *_) in zip(keep, results)], agent_id)

    final: list[tuple[int, int, list]] = list(deduped)
    for row_id, importance, _, prepared in results:
        conflicts = _post_commit(row_id, prepared, agent_id)
        final.append((row_id, importance, conflicts))

    return final


def update_memory(memory_id: int, req: MemoryUpdateRequest, agent_id: str = "default") -> bool:
    """
    Update an existing memory atomically. Only provided fields are changed.
    Re-generates embedding if content changes.
    Returns True if updated, False if not found.
    """
    updates = []
    params: list = []

    if req.content is not None:
        updates.append("content = ?")
        params.append(req.content)
        # Regenerate embedding if content changes
        if _embeddings_available():
            from ..embedder import embed, serialize

            try:
                embedding_blob = serialize(embed(req.content))
                updates.append("embedding = ?")
                params.append(embedding_blob)
            except Exception:
                pass

    if req.category is not None:
        updates.append("category = ?")
        params.append(req.category)

    if req.importance is not None:
        updates.append("importance = ?")
        params.append(req.importance)

    if not updates:
        # Nothing to update — check if memory exists
        with get_connection() as conn:
            row = conn.execute(
                "SELECT id FROM memories WHERE id = ? AND agent_id = ? AND compressed_into IS NULL",
                (memory_id, agent_id),
            ).fetchone()
        return row is not None

    updates.append("updated_at = ?")
    params.append(datetime.now(UTC).isoformat())
    params.append(memory_id)
    params.append(agent_id)

    # Single atomic UPDATE — no read-then-write race condition
    with get_connection() as conn:
        cursor = conn.execute(
            f"UPDATE memories SET {', '.join(updates)} WHERE id = ? AND agent_id = ? AND compressed_into IS NULL",
            params,
        )
        if cursor.rowcount == 0:
            return False

    # Update vector index
    if req.content is not None:
        from ..vector_index import get_index, has_sqlite_vec

        index = get_index()
        if has_sqlite_vec() and _embeddings_available():
            from ..embedder import embed

            try:
                vec = embed(req.content)
                with get_connection() as conn:
                    index.upsert(conn, memory_id, agent_id, vec)
            except Exception:
                pass
        else:
            index.invalidate(agent_id)

    emit(MEMORY_UPDATED, {"id": memory_id, "agent_id": agent_id})
    return True


def get_memory(memory_id: int, agent_id: str = "default") -> MemoryRecord | None:
    """Get a single memory by ID, scoped to agent. None if not found."""
    from ..repository.search import _row_to_record

    with get_connection() as conn:
        row = conn.execute(
            """SELECT id, content, category, importance, decay_score,
                      created_at, updated_at, NULL AS score,
                      valid_from, valid_to, invalidated_at, supersedes_id,
                      confidence, provenance, memory_type, archived_at,
                      compressed_into, title,
                      facts_json, concepts_json, narrative, metadata_json
               FROM memories
               WHERE id = ? AND agent_id = ? AND archived_at IS NULL""",
            (memory_id, agent_id),
        ).fetchone()
    if not row:
        return None
    return _row_to_record(row)


def get_memory_history(memory_id: int, agent_id: str = "default") -> list[MemoryRecord]:
    """
    Restituisce la catena di supersessioni per una memoria, ordinata per created_at.

    La catena naviga all'indietro dal nodo di partenza verso i predecessori
    tramite CTE ricorsiva su supersedes_id (Correction D: single source of truth).

    Esempio: mem_v3 → supersedes_id → mem_v2 → supersedes_id → mem_v1
    Risultato: [mem_v1, mem_v2, mem_v3] (ordine cronologico, dal più vecchio)
    """
    from ..repository.search import _row_to_record

    with get_connection() as conn:
        rows = conn.execute(
            """
            WITH RECURSIVE chain(id, content, category, importance, decay_score,
                                  created_at, updated_at,
                                  valid_from, valid_to, invalidated_at, supersedes_id,
                                  confidence, provenance, memory_type, archived_at,
                                  compressed_into, depth) AS (
                -- Nodo di partenza
                SELECT id, content, category, importance, decay_score,
                       created_at, updated_at,
                       valid_from, valid_to, invalidated_at, supersedes_id,
                       confidence, provenance, memory_type, archived_at,
                       compressed_into, 0
                FROM memories
                WHERE id = ? AND agent_id = ?
                UNION ALL
                -- Predecessori ricorsivi (naviga indietro via supersedes_id)
                SELECT m.id, m.content, m.category, m.importance, m.decay_score,
                       m.created_at, m.updated_at,
                       m.valid_from, m.valid_to, m.invalidated_at, m.supersedes_id,
                       m.confidence, m.provenance, m.memory_type, m.archived_at,
                       m.compressed_into, c.depth + 1
                FROM memories m
                JOIN chain c ON m.id = c.supersedes_id
                WHERE m.agent_id = ? AND c.depth < 50
            )
            SELECT *, NULL AS score FROM chain
            ORDER BY created_at ASC, id ASC
            """,
            (memory_id, agent_id, agent_id),
        ).fetchall()

    return [_row_to_record(row) for row in rows]


def delete_memory(memory_id: int, agent_id: str = "default") -> bool:
    """Delete a memory by id, scoped to agent. Returns True if deleted."""
    with get_connection() as conn:
        cursor = conn.execute(
            "DELETE FROM memories WHERE id = ? AND agent_id = ?",
            (memory_id, agent_id),
        )
        deleted = cursor.rowcount > 0

    if deleted:
        from ..vector_index import get_index, has_sqlite_vec

        index = get_index()
        if has_sqlite_vec():
            try:
                with get_connection() as conn:
                    index.remove(conn, memory_id)
            except Exception:
                pass
        else:
            index.invalidate(agent_id)
        emit(MEMORY_DELETED, {"id": memory_id, "agent_id": agent_id})

    return deleted


def export_memories(agent_id: str = "default") -> list[dict]:
    """Export all active memories for the agent as a list of dicts (without embeddings)."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, content, category, importance, decay_score,
                   access_count, last_accessed, created_at, updated_at,
                   title, facts_json, concepts_json, narrative, metadata_json
            FROM memories
            WHERE agent_id = ? AND compressed_into IS NULL
              AND archived_at IS NULL
              AND (expires_at IS NULL OR expires_at > datetime('now'))
            ORDER BY created_at DESC
            """,
            (agent_id,),
        ).fetchall()
    return [dict(r) for r in rows]


_VALID_CATEGORIES = {
    # Categorie generali
    "general",
    "project",
    "trading",
    "finance",
    "person",
    "preference",
    "task",
    "decision",
    # Categorie coding memory mode (v2.1)
    "architectural_decision",
    "root_cause",
    "runbook",
    "regression_note",
    "tech_debt",
    "api_contract",
}


def import_memories(records: list[dict], agent_id: str = "default") -> int:
    """Import memories from a list of dicts. Returns the number of records imported."""
    imported = 0
    for rec in records:
        content = rec.get("content", "").strip()
        if not content or len(content) < 3:
            continue
        category = rec.get("category", "general")
        if category not in _VALID_CATEGORIES:
            category = "general"
        importance = rec.get("importance", 1)
        importance = max(1, min(5, int(importance)))

        req = MemorySaveRequest(
            content=content[:4000],
            category=category,
            importance=importance,
        )
        save_memory(req, agent_id=agent_id)
        imported += 1

    return imported


def get_stats(agent_id: str | None = None) -> dict:
    """Get database statistics for monitoring."""
    with get_connection() as conn:
        if agent_id:
            total = conn.execute(
                "SELECT COUNT(*) FROM memories WHERE agent_id = ? AND compressed_into IS NULL",
                (agent_id,),
            ).fetchone()[0]
            active = conn.execute(
                "SELECT COUNT(*) FROM memories WHERE agent_id = ? AND compressed_into IS NULL AND decay_score >= 0.05",
                (agent_id,),
            ).fetchone()[0]
            try:
                archived = conn.execute(
                    "SELECT COUNT(*) FROM memories WHERE agent_id = ? AND archived_at IS NOT NULL",
                    (agent_id,),
                ).fetchone()[0]
            except Exception:
                archived = 0
        else:
            total = conn.execute(
                "SELECT COUNT(*) FROM memories WHERE compressed_into IS NULL",
            ).fetchone()[0]
            active = conn.execute(
                "SELECT COUNT(*) FROM memories WHERE compressed_into IS NULL AND decay_score >= 0.05",
            ).fetchone()[0]
            try:
                archived = conn.execute(
                    "SELECT COUNT(*) FROM memories WHERE archived_at IS NOT NULL",
                ).fetchone()[0]
            except Exception:
                archived = 0

        db_path = _get_db_path()
        db_size = os.path.getsize(str(db_path)) if db_path.exists() else 0

    return {"total_memories": total, "active_memories": active, "archived_memories": archived, "db_size_bytes": db_size}


def list_agents() -> list[dict]:
    """Return all distinct agent_ids with memory count and last activity."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT agent_id,
                   COUNT(*) AS memory_count,
                   MAX(created_at) AS last_active
            FROM memories
            WHERE compressed_into IS NULL
            GROUP BY agent_id
            ORDER BY last_active DESC
            """
        ).fetchall()
    return [dict(r) for r in rows]
