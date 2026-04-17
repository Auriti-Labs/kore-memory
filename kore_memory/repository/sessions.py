"""
Kore — Repository: Session management.
Create, list, end, delete, summarize conversation sessions.
"""

from __future__ import annotations

from ..database import get_connection
from ..models import MemoryRecord
from .search import _row_to_record


def create_session(session_id: str, agent_id: str = "default", title: str | None = None) -> dict:
    """Create a new conversation session."""
    with get_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO sessions (id, agent_id, title) VALUES (?, ?, ?)",
            (session_id, agent_id, title),
        )
        row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    return dict(row) if row else {}


def list_sessions(agent_id: str = "default", limit: int = 50) -> list[dict]:
    """List all sessions for an agent with memory count."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT s.id, s.agent_id, s.title, s.created_at, s.ended_at,
                   COUNT(m.id) AS memory_count
            FROM sessions s
            LEFT JOIN memories m ON m.session_id = s.id AND m.agent_id = s.agent_id
            WHERE s.agent_id = ?
            GROUP BY s.id
            ORDER BY s.created_at DESC
            LIMIT ?
            """,
            (agent_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def get_session_memories(session_id: str, agent_id: str = "default") -> list[MemoryRecord]:
    """Get all memories in a session."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, content, category, importance, decay_score,
                   access_count, last_accessed, created_at, updated_at, NULL AS score
            FROM memories
            WHERE session_id = ? AND agent_id = ? AND compressed_into IS NULL
            ORDER BY created_at ASC
            """,
            (session_id, agent_id),
        ).fetchall()
    return [_row_to_record(r) for r in rows]


def end_session(session_id: str, agent_id: str = "default") -> bool:
    """Mark a session as ended. Auto-consolidates if eligible (non-blocking)."""
    with get_connection() as conn:
        cursor = conn.execute(
            "UPDATE sessions SET ended_at = datetime('now') WHERE id = ? AND agent_id = ? AND ended_at IS NULL",
            (session_id, agent_id),
        )
        ended = cursor.rowcount > 0

    if ended:
        try:
            from ..consolidation import consolidate_session

            consolidate_session(session_id, agent_id)
        except Exception:
            pass  # non-blocking: session end succeeds even if consolidation fails

    return ended


def delete_session(session_id: str, agent_id: str = "default") -> int:
    """Delete a session and unlink its memories. Returns number of memories unlinked."""
    with get_connection() as conn:
        # Unlink memories from session (don't delete them)
        cursor = conn.execute(
            "UPDATE memories SET session_id = NULL WHERE session_id = ? AND agent_id = ?",
            (session_id, agent_id),
        )
        unlinked = cursor.rowcount
        conn.execute(
            "DELETE FROM sessions WHERE id = ? AND agent_id = ?",
            (session_id, agent_id),
        )
    return unlinked


def get_session_summary(session_id: str, agent_id: str = "default") -> dict:
    """Get a summary of a session (no LLM, just aggregation)."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM sessions WHERE id = ? AND agent_id = ?",
            (session_id, agent_id),
        ).fetchone()
        if not row:
            return {}

        stats = conn.execute(
            """
            SELECT COUNT(*) AS memory_count,
                   GROUP_CONCAT(DISTINCT category) AS categories,
                   AVG(importance) AS avg_importance,
                   MIN(created_at) AS first_memory,
                   MAX(created_at) AS last_memory
            FROM memories
            WHERE session_id = ? AND agent_id = ? AND compressed_into IS NULL
            """,
            (session_id, agent_id),
        ).fetchone()

    return {
        "session_id": row["id"],
        "agent_id": row["agent_id"],
        "title": row["title"],
        "created_at": row["created_at"],
        "ended_at": row["ended_at"],
        "memory_count": stats["memory_count"] or 0,
        "categories": stats["categories"].split(",") if stats["categories"] else [],
        "avg_importance": round(stats["avg_importance"] or 0, 1),
        "first_memory": stats["first_memory"],
        "last_memory": stats["last_memory"],
    }
