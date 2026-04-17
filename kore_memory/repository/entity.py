"""
Kore — Repository: Graph Entity operations.
CRUD for graph_entities and memory_entity_links (M1 v4.0).
"""

from __future__ import annotations

import re

from ..database import get_connection

_MAX_ENTITIES_PER_MEMORY = 20


def _canonicalize_name(name: str) -> str:
    """Normalize entity name: strip, collapse whitespace, lowercase, strip leading /, strip version suffix."""
    name = name.strip().lower()
    name = re.sub(r"\s+", " ", name)
    name = name.lstrip("/")
    # Strip trailing version suffix (e.g. "react v18.2" → "react")
    stripped = re.sub(r"\s*v?\d+(\.\d+)+$", "", name)
    if stripped:
        name = stripped
    if len(name) < 3:
        return ""
    return name[:200]


def get_or_create_entity(
    agent_id: str,
    name: str,
    entity_type: str = "concept",
    properties: str | None = None,
    conn=None,
) -> int | None:
    """Get existing entity or create new one. Returns entity id, or None if name invalid."""
    canonical = _canonicalize_name(name)
    if not canonical:
        return None

    def _do(c):
        c.execute(
            "INSERT OR IGNORE INTO graph_entities (agent_id, name, entity_type, properties) VALUES (?, ?, ?, ?)",
            (agent_id, canonical, entity_type, properties),
        )
        row = c.execute(
            "SELECT id FROM graph_entities WHERE agent_id = ? AND name = ? AND entity_type = ?",
            (agent_id, canonical, entity_type),
        ).fetchone()
        return row[0] if row else None

    if conn is not None:
        return _do(conn)
    with get_connection() as c:
        return _do(c)


def link_memory_entity(
    memory_id: int,
    entity_id: int,
    role: str = "mentions",
    confidence: float = 1.0,
    conn=None,
) -> bool:
    """Create a link between a memory and an entity. Returns True if created."""
    confidence = max(0.0, min(1.0, confidence))

    def _do(c):
        try:
            c.execute(
                "INSERT OR IGNORE INTO memory_entity_links (memory_id, entity_id, role, confidence) VALUES (?, ?, ?, ?)",
                (memory_id, entity_id, role, confidence),
            )
            return True
        except Exception:
            return False

    if conn is not None:
        return _do(conn)
    with get_connection() as c:
        return _do(c)


def link_entities_to_memory(
    memory_id: int,
    entities: list[tuple[str, str]],
    agent_id: str = "default",
    role: str = "mentions",
) -> int:
    """
    Bulk-link entities to a memory. Creates entities if needed.
    entities: list of (name, entity_type) tuples.
    Returns number of links created. Caps at _MAX_ENTITIES_PER_MEMORY.
    """
    linked = 0
    with get_connection() as conn:
        for name, etype in entities[:_MAX_ENTITIES_PER_MEMORY]:
            eid = get_or_create_entity(agent_id, name, etype, conn=conn)
            if eid is not None:
                if link_memory_entity(memory_id, eid, role=role, conn=conn):
                    linked += 1
    return linked


def get_entities_for_memory(memory_id: int, agent_id: str = "default") -> list[dict]:
    """Return all entities linked to a memory."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT ge.id, ge.name, ge.entity_type, mel.role, mel.confidence
            FROM memory_entity_links mel
            JOIN graph_entities ge ON ge.id = mel.entity_id
            JOIN memories m ON m.id = mel.memory_id
            WHERE mel.memory_id = ? AND m.agent_id = ?
            ORDER BY mel.confidence DESC, ge.name
            """,
            (memory_id, agent_id),
        ).fetchall()
    return [dict(r) for r in rows]


def get_memories_for_entity(entity_id: int, agent_id: str = "default", limit: int = 50) -> list[int]:
    """Return memory IDs linked to an entity."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT mel.memory_id
            FROM memory_entity_links mel
            JOIN memories m ON m.id = mel.memory_id
            WHERE mel.entity_id = ? AND m.agent_id = ?
              AND m.archived_at IS NULL AND m.compressed_into IS NULL
            ORDER BY mel.confidence DESC
            LIMIT ?
            """,
            (entity_id, agent_id, limit),
        ).fetchall()
    return [r[0] for r in rows]


def find_entities_by_names(
    names: list[str],
    agent_id: str = "default",
) -> list[tuple[int, str, str]]:
    """Find entities matching a list of canonical names. Returns [(id, name, entity_type)]."""
    if not names:
        return []
    canonical = [_canonicalize_name(n) for n in names]
    canonical = [c for c in canonical if c]
    if not canonical:
        return []
    placeholders = ",".join("?" for _ in canonical)
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT id, name, entity_type FROM graph_entities WHERE agent_id = ? AND name IN ({placeholders})",
            [agent_id, *canonical],
        ).fetchall()
    return [(r[0], r[1], r[2]) for r in rows]
