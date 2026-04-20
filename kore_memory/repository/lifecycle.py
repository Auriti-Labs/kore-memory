"""
Kore — Repository: Memory lifecycle operations.
Decay, cleanup, archive, restore.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime

from ..database import get_connection
from ..decay import compute_decay
from ..events import MEMORY_ARCHIVED, MEMORY_DECAYED, MEMORY_RESTORED, emit
from ..models import MemoryRecord
from .search import _row_to_record

# Lock for maintenance operations — prevents concurrent runs
_decay_lock = threading.Lock()
_compress_lock = threading.Lock()


def cleanup_expired(agent_id: str | None = None) -> int:
    """Delete memories with elapsed TTL. Returns the number of records removed."""
    with get_connection() as conn:
        sql = "DELETE FROM memories WHERE expires_at IS NOT NULL AND expires_at <= datetime('now')"
        params: list = []
        if agent_id:
            sql += " AND agent_id = ?"
            params.append(agent_id)
        cursor = conn.execute(sql, params)
        return cursor.rowcount


def run_decay_pass(
    agent_id: str | None = None,
    dry_run: bool = False,
) -> tuple[int, object | None]:
    """
    Recalculate decay_score for all active memories (optionally scoped to agent).
    Also cleans up memories with elapsed TTL and runs lifecycle policies.
    Returns (memories_updated, policy_result). Thread-safe: only one run at a time.
    """
    if not _decay_lock.acquire(blocking=False):
        return 0, None  # run already in progress — silent skip

    try:
        # Clean up expired memories before recalculating
        cleanup_expired(agent_id)
        updated = _run_decay_pass_inner(agent_id)

        # Run lifecycle policies after decay recalculation
        from ..policy_engine import evaluate_and_apply

        policy_result = None
        try:
            policy_result = evaluate_and_apply(agent_id=agent_id, dry_run=dry_run)
        except Exception:
            import logging

            logging.getLogger("kore.lifecycle").exception("Policy engine error during decay pass")

        return updated, policy_result
    finally:
        _decay_lock.release()


def _run_decay_pass_inner(agent_id: str | None = None) -> int:
    with get_connection() as conn:
        sql = (
            "SELECT id, importance, created_at, last_accessed, access_count"
            " FROM memories WHERE compressed_into IS NULL AND archived_at IS NULL"
        )
        params: list = []
        if agent_id:
            sql += " AND agent_id = ?"
            params.append(agent_id)
        rows = conn.execute(sql, params).fetchall()

    now = datetime.now(UTC).isoformat()
    updates = []
    for row in rows:
        new_score = compute_decay(
            importance=row["importance"],
            created_at=row["created_at"],
            last_accessed=row["last_accessed"],
            access_count=row["access_count"],
        )
        updates.append((new_score, now, row["id"]))

    if updates:
        with get_connection() as conn:
            conn.executemany(
                "UPDATE memories SET decay_score = ?, updated_at = ? WHERE id = ?",
                updates,
            )
        emit(MEMORY_DECAYED, {"agent_id": agent_id or "all", "updated": len(updates)})

    return len(updates)


def archive_memory(memory_id: int, agent_id: str = "default") -> bool:
    """Archive a memory (soft-delete). Returns True if archived."""
    with get_connection() as conn:
        cursor = conn.execute(
            "UPDATE memories SET archived_at = datetime('now') WHERE id = ? AND agent_id = ? AND archived_at IS NULL",
            (memory_id, agent_id),
        )
        archived = cursor.rowcount > 0

    if archived:
        emit(MEMORY_ARCHIVED, {"id": memory_id, "agent_id": agent_id})

    return archived


def restore_memory(memory_id: int, agent_id: str = "default") -> bool:
    """Restore an archived memory. Returns True if restored."""
    with get_connection() as conn:
        cursor = conn.execute(
            "UPDATE memories SET archived_at = NULL WHERE id = ? AND agent_id = ? AND archived_at IS NOT NULL",
            (memory_id, agent_id),
        )
        restored = cursor.rowcount > 0

    if restored:
        emit(MEMORY_RESTORED, {"id": memory_id, "agent_id": agent_id})

    return restored


def get_archived(agent_id: str = "default", limit: int = 50) -> list[MemoryRecord]:
    """List archived memories for an agent."""
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT id, content, category, importance, decay_score, access_count,
                      last_accessed, created_at, updated_at, NULL AS score
               FROM memories WHERE agent_id = ? AND archived_at IS NOT NULL
               ORDER BY archived_at DESC LIMIT ?""",
            (agent_id, limit),
        ).fetchall()
    return [_row_to_record(r) for r in rows]
