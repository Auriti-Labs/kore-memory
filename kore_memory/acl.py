"""
Kore — Access Control Layer
Multi-agent shared memory with permission management.
Permissions: read, write, admin.
"""

from __future__ import annotations

from .database import get_connection

# Valid permission levels
PERMISSIONS = ("read", "write", "admin")


def _ensure_acl_table() -> None:
    """Create the ACL table if it doesn't exist (migration-safe)."""
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS memory_acl (
                memory_id   INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
                agent_id    TEXT    NOT NULL,
                permission  TEXT    NOT NULL CHECK (permission IN ('read', 'write', 'admin')),
                granted_by  TEXT    NOT NULL,
                created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (memory_id, agent_id)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_acl_agent ON memory_acl (agent_id)")


def grant_access(
    memory_id: int,
    target_agent: str,
    permission: str,
    grantor_agent: str,
) -> bool:
    """
    Grant access to a memory for another agent.
    Only the memory owner or an agent with admin permission can grant access.
    """
    if permission not in PERMISSIONS:
        return False

    _ensure_acl_table()

    with get_connection() as conn:
        # Verify grantor owns the memory or has admin permission
        owner = conn.execute(
            "SELECT agent_id FROM memories WHERE id = ? AND archived_at IS NULL",
            (memory_id,),
        ).fetchone()
        if not owner:
            return False

        is_owner = owner["agent_id"] == grantor_agent
        has_admin = False
        if not is_owner:
            acl_row = conn.execute(
                "SELECT permission FROM memory_acl WHERE memory_id = ? AND agent_id = ?",
                (memory_id, grantor_agent),
            ).fetchone()
            has_admin = acl_row and acl_row["permission"] == "admin"

        if not is_owner and not has_admin:
            return False

        # Upsert permission
        conn.execute(
            """INSERT INTO memory_acl (memory_id, agent_id, permission, granted_by)
               VALUES (?, ?, ?, ?)
               ON CONFLICT (memory_id, agent_id)
               DO UPDATE SET permission = excluded.permission, granted_by = excluded.granted_by""",
            (memory_id, target_agent, permission, grantor_agent),
        )
        return True


def revoke_access(memory_id: int, target_agent: str, grantor_agent: str) -> bool:
    """Revoke access for an agent. Only the owner or an admin can revoke."""
    _ensure_acl_table()

    with get_connection() as conn:
        owner = conn.execute(
            "SELECT agent_id FROM memories WHERE id = ?",
            (memory_id,),
        ).fetchone()
        if not owner:
            return False

        is_owner = owner["agent_id"] == grantor_agent
        has_admin = False
        if not is_owner:
            acl_row = conn.execute(
                "SELECT permission FROM memory_acl WHERE memory_id = ? AND agent_id = ?",
                (memory_id, grantor_agent),
            ).fetchone()
            has_admin = acl_row and acl_row["permission"] == "admin"

        if not is_owner and not has_admin:
            return False

        cursor = conn.execute(
            "DELETE FROM memory_acl WHERE memory_id = ? AND agent_id = ?",
            (memory_id, target_agent),
        )
        return cursor.rowcount > 0


def list_permissions(memory_id: int, agent_id: str) -> list[dict]:
    """
    List all permissions for a memory.
    Only visible to the owner or agents with admin permission.
    """
    _ensure_acl_table()

    with get_connection() as conn:
        owner = conn.execute(
            "SELECT agent_id FROM memories WHERE id = ?",
            (memory_id,),
        ).fetchone()
        if not owner:
            return []

        is_owner = owner["agent_id"] == agent_id
        if not is_owner:
            acl_row = conn.execute(
                "SELECT permission FROM memory_acl WHERE memory_id = ? AND agent_id = ?",
                (memory_id, agent_id),
            ).fetchone()
            if not (acl_row and acl_row["permission"] == "admin"):
                return []

        rows = conn.execute(
            """SELECT agent_id, permission, granted_by, created_at
               FROM memory_acl WHERE memory_id = ?
               ORDER BY created_at""",
            (memory_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def check_access(memory_id: int, agent_id: str, required: str = "read") -> bool:
    """
    Check if an agent has the required permission on a memory.
    The owner always has full access. Permission hierarchy: admin > write > read.
    """
    _ensure_acl_table()
    hierarchy = {"read": 0, "write": 1, "admin": 2}

    with get_connection() as conn:
        # Owner always has access
        owner = conn.execute(
            "SELECT agent_id FROM memories WHERE id = ?",
            (memory_id,),
        ).fetchone()
        if not owner:
            return False
        if owner["agent_id"] == agent_id:
            return True

        # Check ACL
        acl_row = conn.execute(
            "SELECT permission FROM memory_acl WHERE memory_id = ? AND agent_id = ?",
            (memory_id, agent_id),
        ).fetchone()
        if not acl_row:
            return False

        return hierarchy.get(acl_row["permission"], -1) >= hierarchy.get(required, 0)


def get_shared_memories(agent_id: str, limit: int = 50) -> list[dict]:
    """Get all memories shared with an agent (not owned by them)."""
    _ensure_acl_table()

    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT m.id, m.content, m.category, m.importance, m.decay_score,
                   m.created_at, m.updated_at, m.agent_id AS owner_agent,
                   a.permission
            FROM memory_acl a
            JOIN memories m ON m.id = a.memory_id
            WHERE a.agent_id = ? AND m.agent_id != ?
              AND m.archived_at IS NULL AND m.compressed_into IS NULL
            ORDER BY m.created_at DESC
            LIMIT ?
            """,
            (agent_id, agent_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]
