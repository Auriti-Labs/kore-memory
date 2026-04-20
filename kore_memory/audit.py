"""
Kore — Audit Log
Persists memory lifecycle events to the event_logs table.
Enabled via KORE_AUDIT_LOG=1.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from . import events
from .database import get_connection

logger = logging.getLogger("kore.audit")


def _audit_handler(event: str, data: dict[str, Any]) -> None:
    """Write a single event to the event_logs table."""
    agent_id = data.get("agent_id", "default")
    memory_id = data.get("id")
    # Serialize the full payload (minus agent_id which has its own column)
    data_blob = json.dumps(data) if data else None

    with get_connection() as conn:
        conn.execute(
            "INSERT INTO event_logs (event, agent_id, memory_id, data) VALUES (?, ?, ?, ?)",
            (event, agent_id, memory_id, data_blob),
        )


def register_audit_handler() -> None:
    """Register the audit handler on all known memory event types."""
    all_events = [
        events.MEMORY_SAVED,
        events.MEMORY_DELETED,
        events.MEMORY_UPDATED,
        events.MEMORY_COMPRESSED,
        events.MEMORY_DECAYED,
        events.MEMORY_ARCHIVED,
        events.MEMORY_RESTORED,
        events.POLICY_APPLIED,
    ]
    for event_type in all_events:
        events.on(event_type, _audit_handler)
    logger.info("Audit log handler registered for %d event types", len(all_events))


def query_audit_log(
    agent_id: str,
    event_type: str | None = None,
    limit: int = 100,
    since: str | None = None,
) -> list[dict[str, Any]]:
    """
    Query persisted audit events for a given agent.

    Args:
        agent_id: Filter to this agent's events.
        event_type: Optional event type filter (e.g. "memory.saved").
        limit: Max rows to return (default 100).
        since: ISO datetime string — only return events after this timestamp.

    Returns:
        List of event dicts with id, event, agent_id, memory_id, data, created_at.
    """
    sql = "SELECT id, event, agent_id, memory_id, data, created_at FROM event_logs WHERE agent_id = ?"
    params: list[Any] = [agent_id]

    if event_type:
        sql += " AND event = ?"
        params.append(event_type)

    if since:
        sql += " AND created_at >= ?"
        params.append(since)

    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()

    results = []
    for row in rows:
        entry = {
            "id": row["id"],
            "event": row["event"],
            "agent_id": row["agent_id"],
            "memory_id": row["memory_id"],
            "data": json.loads(row["data"]) if row["data"] else None,
            "created_at": row["created_at"],
        }
        results.append(entry)
    return results


def cleanup_audit_log(days: int = 90) -> int:
    """
    Delete audit log entries older than `days` days.

    Returns:
        Number of rows deleted.
    """
    with get_connection() as conn:
        cursor = conn.execute(
            "DELETE FROM event_logs WHERE created_at < datetime('now', ?)",
            (f"-{days} days",),
        )
        return cursor.rowcount
