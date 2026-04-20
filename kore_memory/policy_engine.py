"""
Kore — Lifecycle Policy Engine
Evaluates lifecycle policies against memories and applies actions (archive, flag).
Called during run_decay_pass() after decay scores are recalculated.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from .database import get_connection
from .events import POLICY_APPLIED, emit

logger = logging.getLogger("kore.policy_engine")


@dataclass
class PolicyAction:
    """A single action to apply to a memory."""

    memory_id: int
    policy_id: str
    policy_name: str
    action: str  # "archive" | "flag"


@dataclass
class PolicyRunResult:
    """Result of a full policy evaluation pass."""

    evaluated: int = 0
    archived: int = 0
    flagged: int = 0
    actions: list[PolicyAction] = field(default_factory=list)


def get_enabled_policies(agent_id: str | None = None) -> list[dict]:
    """Load enabled policies applicable to the given agent (or all global)."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, agent_id, name, trigger, action, params_json "
            "FROM lifecycle_policies WHERE enabled = 1 "
            "AND (agent_id = '*' OR agent_id = ?) "
            "ORDER BY id",
            (agent_id or "default",),
        ).fetchall()
    return [dict(r) for r in rows]


def evaluate_and_apply(agent_id: str | None = None, dry_run: bool = False) -> PolicyRunResult:
    """
    Evaluate all enabled policies against active memories and apply actions.

    Args:
        agent_id: Scope to this agent (None = all agents, policies with agent_id='*').
        dry_run: If True, compute actions but don't apply them.

    Returns:
        PolicyRunResult with counts and action list.
    """
    policies = get_enabled_policies(agent_id)
    if not policies:
        return PolicyRunResult()

    result = PolicyRunResult()

    for policy in policies:
        params = json.loads(policy["params_json"])
        trigger = policy["trigger"]
        action = policy["action"]

        if trigger == "decay_below":
            memories = _find_decay_below(params, agent_id)
        elif trigger == "conflict_unresolved_days":
            memories = _find_unresolved_conflicts(params, agent_id)
        elif trigger == "age_and_idle":
            memories = _find_age_and_idle(params, agent_id)
        else:
            continue

        for mem_id in memories:
            result.evaluated += 1
            pa = PolicyAction(
                memory_id=mem_id,
                policy_id=policy["id"],
                policy_name=policy["name"],
                action=action,
            )
            result.actions.append(pa)

    if not dry_run:
        _apply_actions(result)

    return result


def _find_decay_below(params: dict, agent_id: str | None) -> list[int]:
    """Find memories with decay_score below threshold and idle for min_idle_days."""
    threshold = params.get("decay_threshold", 0.02)
    min_idle = params.get("min_idle_days", 90)

    agent_filter = "AND m.agent_id = ?" if agent_id else ""
    agent_params: list = [agent_id] if agent_id else []

    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT m.id FROM memories m
            WHERE m.decay_score < ?
              AND m.archived_at IS NULL
              AND m.compressed_into IS NULL
              AND (m.last_accessed IS NULL
                   OR m.last_accessed < datetime('now', ?))
              {agent_filter}
            """,
            [threshold, f"-{min_idle} days", *agent_params],
        ).fetchall()
    return [r[0] for r in rows]


def _find_unresolved_conflicts(params: dict, agent_id: str | None) -> list[int]:
    """Find memories involved in unresolved conflicts older than N days."""
    days = params.get("unresolved_days", 30)

    agent_filter = "AND mc.agent_id = ?" if agent_id else ""
    agent_params: list = [agent_id] if agent_id else []

    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT DISTINCT m.id
            FROM memory_conflicts mc
            JOIN memories m ON m.id IN (mc.memory_a_id, mc.memory_b_id)
            WHERE mc.resolved_at IS NULL
              AND mc.detected_at < datetime('now', ?)
              AND m.archived_at IS NULL
              AND m.compressed_into IS NULL
              {agent_filter}
            """,
            [f"-{days} days", *agent_params],
        ).fetchall()
    return [r[0] for r in rows]


def _find_age_and_idle(params: dict, agent_id: str | None) -> list[int]:
    """Find memories older than min_age_days and idle for min_idle_days, optionally filtered by category/type."""
    min_age = params.get("min_age_days", 365)
    min_idle = params.get("min_idle_days", 180)
    category = params.get("category")
    memory_type = params.get("memory_type")

    conditions = [
        "m.archived_at IS NULL",
        "m.compressed_into IS NULL",
        "m.created_at < datetime('now', ?)",
        "(m.last_accessed IS NULL OR m.last_accessed < datetime('now', ?))",
    ]
    bind: list = [f"-{min_age} days", f"-{min_idle} days"]

    if category:
        conditions.append("m.category = ?")
        bind.append(category)
    if memory_type:
        conditions.append("m.memory_type = ?")
        bind.append(memory_type)
    if agent_id:
        conditions.append("m.agent_id = ?")
        bind.append(agent_id)

    where = " AND ".join(conditions)

    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT m.id FROM memories m WHERE {where}",
            bind,
        ).fetchall()
    return [r[0] for r in rows]


def _apply_actions(result: PolicyRunResult) -> None:
    """Execute archive/flag actions and emit events."""
    if not result.actions:
        return

    now = datetime.now(UTC).isoformat()

    with get_connection() as conn:
        for pa in result.actions:
            if pa.action == "archive":
                cursor = conn.execute(
                    "UPDATE memories SET archived_at = ? WHERE id = ? AND archived_at IS NULL",
                    (now, pa.memory_id),
                )
                if cursor.rowcount > 0:
                    result.archived += 1
                    emit(
                        POLICY_APPLIED,
                        {
                            "id": pa.memory_id,
                            "policy_id": pa.policy_id,
                            "action": "archive",
                        },
                    )

            elif pa.action == "flag":
                conn.execute(
                    "INSERT OR IGNORE INTO policy_flags (memory_id, policy_id) VALUES (?, ?)",
                    (pa.memory_id, pa.policy_id),
                )
                result.flagged += 1
                emit(
                    POLICY_APPLIED,
                    {
                        "id": pa.memory_id,
                        "policy_id": pa.policy_id,
                        "action": "flag",
                    },
                )

    logger.info(
        "Policy engine: %d evaluated, %d archived, %d flagged",
        result.evaluated,
        result.archived,
        result.flagged,
    )
