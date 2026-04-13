"""
Kore — Repository: Memory CRUD operations.
Save, get, update, delete, batch save, import/export.
"""

from __future__ import annotations

import os
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
    importance = req.importance
    if importance is None:
        importance = auto_score(req.content, req.category)

    # Inferenza memory_type dalla category se non fornito esplicitamente
    from ..models import infer_memory_type

    memory_type = req.memory_type or infer_memory_type(req.category)

    embedding_blob = None
    if _embeddings_available():
        from ..embedder import embed, serialize

        try:
            embedding_blob = serialize(embed(req.content))
        except Exception:
            embedding_blob = None

    expires_at = None
    if req.ttl_hours:
        expires_at = (datetime.now(UTC) + timedelta(hours=req.ttl_hours)).isoformat()

    # Formato SQLite (senza T e timezone) per comparazioni corrette con datetime('now')
    _fmt = "%Y-%m-%d %H:%M:%S"
    valid_from = req.valid_from.astimezone(UTC).strftime(_fmt) if req.valid_from else None
    valid_to = req.valid_to.astimezone(UTC).strftime(_fmt) if req.valid_to else None

    import json as _json

    provenance_json = _json.dumps(req.provenance.model_dump()) if req.provenance else None
    now = datetime.now(UTC).isoformat()

    with get_connection() as conn:
        # Auto-create session se necessario
        if session_id:
            conn.execute(
                "INSERT OR IGNORE INTO sessions (id, agent_id) VALUES (?, ?)",
                (session_id, agent_id),
            )

        # Supersessione atomica: invalida il predecessore nella stessa transazione
        if req.supersedes_id is not None:
            conn.execute(
                "UPDATE memories SET invalidated_at = ? WHERE id = ? AND agent_id = ? AND invalidated_at IS NULL",
                (now, req.supersedes_id, agent_id),
            )

        cursor = conn.execute(
            """
            INSERT INTO memories (
                agent_id, content, category, importance, embedding, expires_at, session_id,
                valid_from, valid_to, supersedes_id, confidence, provenance, memory_type
            )
            VALUES (
                :agent_id, :content, :category, :importance, :embedding, :expires_at, :session_id,
                :valid_from, :valid_to, :supersedes_id, :confidence, :provenance, :memory_type
            )
            """,
            {
                "agent_id": agent_id,
                "content": req.content,
                "category": req.category,
                "importance": importance,
                "embedding": embedding_blob,
                "expires_at": expires_at,
                "session_id": session_id,
                "valid_from": valid_from,
                "valid_to": valid_to,
                "supersedes_id": req.supersedes_id,
                "confidence": req.confidence,
                "provenance": provenance_json,
                "memory_type": memory_type,
            },
        )
        row_id = cursor.lastrowid

    # Update vector index
    if embedding_blob:
        from ..vector_index import get_index, has_sqlite_vec

        index = get_index()
        if has_sqlite_vec():
            # Insert into sqlite-vec native index
            from ..embedder import deserialize

            try:
                vec = deserialize(embedding_blob)
                with get_connection() as conn:
                    index.upsert(conn, row_id, agent_id, vec)
            except Exception:
                pass  # graceful degradation
        else:
            index.invalidate(agent_id)

    emit(MEMORY_SAVED, {"id": row_id, "agent_id": agent_id})

    # Entity extraction (optional, enabled via KORE_ENTITY_EXTRACTION=1)
    from .. import config as _cfg

    if _cfg.ENTITY_EXTRACTION:
        from ..integrations.entities import auto_tag_entities

        try:
            auto_tag_entities(row_id, req.content, agent_id)
        except Exception:
            pass  # graceful degradation

    # Conflict detection (sincrono se KORE_CONFLICT_SYNC=true)
    conflicts: list[str] = []
    if _cfg.CONFLICT_SYNC:
        try:
            from ..conflict_detector import detect_conflicts

            conflicts = detect_conflicts(
                memory_id=row_id,
                content=req.content,
                agent_id=agent_id,
                valid_from=valid_from,
                valid_to=valid_to,
                confidence=req.confidence,
            )
        except Exception:
            pass  # graceful degradation — non blocca il save

    return row_id, importance, conflicts


def save_memory_batch(reqs: list[MemorySaveRequest], agent_id: str = "default") -> list[tuple[int, int, list]]:
    """
    Batch save: single transaction, batch embeddings.
    Returns list of (row_id, importance, conflicts_detected) tuples.
    """
    if not reqs:
        return []

    # Auto-score importances
    importances = []
    for req in reqs:
        imp = req.importance
        if imp is None:
            imp = auto_score(req.content, req.category)
        importances.append(imp)

    # Batch embed all contents at once
    embeddings: list[str | None] = [None] * len(reqs)
    if _embeddings_available():
        from ..embedder import embed_batch, serialize

        try:
            vectors = embed_batch([req.content for req in reqs])
            embeddings = [serialize(v) for v in vectors]
        except Exception:
            pass  # Fall back to no embeddings

    # Single transaction for all inserts
    results = []
    with get_connection() as conn:
        for i, req in enumerate(reqs):
            expires_at = None
            if req.ttl_hours:
                expires_at = (datetime.now(UTC) + timedelta(hours=req.ttl_hours)).isoformat()

            cursor = conn.execute(
                """INSERT INTO memories (agent_id, content, category, importance, embedding, expires_at)
                   VALUES (:agent_id, :content, :category, :importance, :embedding, :expires_at)""",
                {
                    "agent_id": agent_id,
                    "content": req.content,
                    "category": req.category,
                    "importance": importances[i],
                    "embedding": embeddings[i],
                    "expires_at": expires_at,
                },
            )
            results.append((cursor.lastrowid, importances[i], []))

    # Emit audit event for each saved memory
    for row_id, _, _conflicts in results:
        emit(MEMORY_SAVED, {"id": row_id, "agent_id": agent_id})

    # Update vector index
    if any(e is not None for e in embeddings):
        from ..vector_index import get_index, has_sqlite_vec

        index = get_index()
        if has_sqlite_vec():
            from ..embedder import deserialize

            with get_connection() as conn:
                for i, emb in enumerate(embeddings):
                    if emb is not None:
                        try:
                            vec = deserialize(emb)
                            index.upsert(conn, results[i][0], agent_id, vec)
                        except Exception:
                            pass
        else:
            index.invalidate(agent_id)

    return results


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
                      compressed_into
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
                   access_count, last_accessed, created_at, updated_at
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
