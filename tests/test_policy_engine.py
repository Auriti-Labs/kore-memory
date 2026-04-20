"""
Tests for Lifecycle Policy Engine v1 — Step 1: Schema + Seed Data.
Step 8: Integration tests for policy evaluation, actions, API, and conditions.
"""

import json
import sqlite3
from datetime import UTC, datetime, timedelta

from kore_memory.database import get_connection, init_db
from kore_memory.events import POLICY_APPLIED, clear as clear_events, on
from kore_memory.models import MemorySaveRequest
from kore_memory.policy_engine import (
    PolicyRunResult,
    evaluate_and_apply,
    get_enabled_policies,
)
from kore_memory.repository import save_memory
from kore_memory.repository.lifecycle import archive_memory, run_decay_pass


class TestLifecyclePoliciesSchema:
    """Verify lifecycle_policies table structure and constraints."""

    def test_table_exists(self):
        with get_connection() as conn:
            tables = {
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        assert "lifecycle_policies" in tables
        assert "policy_flags" in tables

    def test_columns(self):
        expected = {"id", "agent_id", "name", "trigger", "action", "params_json", "enabled", "created_at"}
        with get_connection() as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(lifecycle_policies)").fetchall()}
        assert cols == expected

    def test_policy_flags_columns(self):
        expected = {"memory_id", "policy_id", "flagged_at"}
        with get_connection() as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(policy_flags)").fetchall()}
        assert cols == expected

    def test_index_exists(self):
        with get_connection() as conn:
            indexes = {
                r[1] for r in conn.execute(
                    "PRAGMA index_list(lifecycle_policies)"
                ).fetchall()
            }
        assert "idx_policies_agent" in indexes

    def test_trigger_check_constraint(self):
        with get_connection() as conn:
            try:
                conn.execute(
                    "INSERT INTO lifecycle_policies (id, name, trigger, action, params_json) "
                    "VALUES ('bad', 'bad', 'invalid_trigger', 'archive', '{}')"
                )
                assert False, "Should have raised CHECK constraint"
            except sqlite3.IntegrityError:
                pass

    def test_action_check_constraint(self):
        with get_connection() as conn:
            try:
                conn.execute(
                    "INSERT INTO lifecycle_policies (id, name, trigger, action, params_json) "
                    "VALUES ('bad', 'bad', 'decay_below', 'delete', '{}')"
                )
                assert False, "Should have raised CHECK constraint"
            except sqlite3.IntegrityError:
                pass


class TestSeedPolicies:
    """Verify the 3 default policies are seeded correctly."""

    def _get_policies(self):
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT id, agent_id, name, trigger, action, params_json, enabled "
                "FROM lifecycle_policies ORDER BY id"
            ).fetchall()
        return {r[0]: dict(r) for r in rows}

    def test_three_policies_seeded(self):
        policies = self._get_policies()
        assert len(policies) >= 3
        assert "auto_archive_forgotten" in policies
        assert "flag_old_conflicts" in policies
        assert "archive_stale_runbooks" in policies

    def test_all_global(self):
        policies = self._get_policies()
        for pid in ("auto_archive_forgotten", "flag_old_conflicts", "archive_stale_runbooks"):
            assert policies[pid]["agent_id"] == "*"

    def test_all_enabled(self):
        policies = self._get_policies()
        for pid in ("auto_archive_forgotten", "flag_old_conflicts", "archive_stale_runbooks"):
            assert policies[pid]["enabled"] == 1

    def test_auto_archive_forgotten_params(self):
        policies = self._get_policies()
        p = policies["auto_archive_forgotten"]
        assert p["trigger"] == "decay_below"
        assert p["action"] == "archive"
        params = json.loads(p["params_json"])
        assert params["decay_threshold"] == 0.02
        assert params["min_idle_days"] == 90

    def test_flag_old_conflicts_params(self):
        policies = self._get_policies()
        p = policies["flag_old_conflicts"]
        assert p["trigger"] == "conflict_unresolved_days"
        assert p["action"] == "flag"
        params = json.loads(p["params_json"])
        assert params["unresolved_days"] == 30

    def test_archive_stale_runbooks_params(self):
        policies = self._get_policies()
        p = policies["archive_stale_runbooks"]
        assert p["trigger"] == "age_and_idle"
        assert p["action"] == "archive"
        params = json.loads(p["params_json"])
        assert params["min_age_days"] == 365
        assert params["min_idle_days"] == 180
        assert params["category"] == "runbook"
        assert params["memory_type"] == "procedural"


class TestIdempotency:
    """Verify init_db() can be called multiple times safely."""

    def test_init_db_idempotent(self):
        init_db()
        init_db()
        policies = {}
        with get_connection() as conn:
            rows = conn.execute("SELECT id FROM lifecycle_policies").fetchall()
            policies = {r[0] for r in rows}
        assert "auto_archive_forgotten" in policies
        assert "flag_old_conflicts" in policies
        assert "archive_stale_runbooks" in policies
        assert len(policies) == 3

    def test_seed_not_duplicated(self):
        init_db()
        with get_connection() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM lifecycle_policies WHERE id = 'auto_archive_forgotten'"
            ).fetchone()[0]
        assert count == 1


class TestPolicyFlagsCascade:
    """Verify policy_flags FK cascade behavior."""

    def test_flag_fk_memory_cascade(self):
        """Deleting a memory cascades to policy_flags."""
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO memories (agent_id, content, category) VALUES ('test_cascade', 'test', 'general')"
            )
            mem_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute(
                "INSERT OR IGNORE INTO policy_flags (memory_id, policy_id) "
                "VALUES (?, 'auto_archive_forgotten')",
                (mem_id,),
            )
            flags_before = conn.execute(
                "SELECT COUNT(*) FROM policy_flags WHERE memory_id = ?", (mem_id,)
            ).fetchone()[0]
            assert flags_before == 1

            conn.execute("DELETE FROM memories WHERE id = ?", (mem_id,))
            flags_after = conn.execute(
                "SELECT COUNT(*) FROM policy_flags WHERE memory_id = ?", (mem_id,)
            ).fetchone()[0]
            assert flags_after == 0


# ── Helpers ──────────────────────────────────────────────────────────────────

_AGENT = "test_policy_engine"


def _create_memory(content="test memory", category="general", importance=3, agent_id=_AGENT):
    req = MemorySaveRequest(content=content, category=category, importance=importance)
    mem_id, _, _ = save_memory(req, agent_id=agent_id)
    return mem_id


def _set_decay(mem_id, score, days_idle=0):
    """Force a decay score and optionally backdate last_accessed."""
    with get_connection() as conn:
        if days_idle > 0:
            idle_date = (datetime.now(UTC) - timedelta(days=days_idle)).isoformat()
            conn.execute(
                "UPDATE memories SET decay_score = ?, last_accessed = ? WHERE id = ?",
                (score, idle_date, mem_id),
            )
        else:
            conn.execute("UPDATE memories SET decay_score = ? WHERE id = ?", (score, mem_id))


def _set_created_at(mem_id, days_ago):
    dt = (datetime.now(UTC) - timedelta(days=days_ago)).isoformat()
    with get_connection() as conn:
        conn.execute("UPDATE memories SET created_at = ? WHERE id = ?", (dt, mem_id))


def _create_conflict(mem_a, mem_b, days_ago=0):
    import uuid

    dt = (datetime.now(UTC) - timedelta(days=days_ago)).isoformat()
    cid = str(uuid.uuid4())[:8]
    with get_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO memory_conflicts (id, memory_a_id, memory_b_id, agent_id, conflict_type, detected_at) "
            "VALUES (?, ?, ?, ?, 'factual', ?)",
            (cid, mem_a, mem_b, _AGENT, dt),
        )


def _cleanup():
    with get_connection() as conn:
        conn.execute("DELETE FROM memories WHERE agent_id = ?", (_AGENT,))
        conn.execute("DELETE FROM policy_flags WHERE memory_id NOT IN (SELECT id FROM memories)")
        conn.execute("DELETE FROM memory_conflicts WHERE agent_id = ?", (_AGENT,))


# ── Integration Tests ────────────────────────────────────────────────────────


class TestGetEnabledPolicies:
    def test_returns_global_policies(self):
        policies = get_enabled_policies(_AGENT)
        ids = {p["id"] for p in policies}
        assert "auto_archive_forgotten" in ids
        assert "flag_old_conflicts" in ids
        assert "archive_stale_runbooks" in ids

    def test_disabled_policy_excluded(self):
        with get_connection() as conn:
            conn.execute("UPDATE lifecycle_policies SET enabled = 0 WHERE id = 'flag_old_conflicts'")
        try:
            policies = get_enabled_policies(_AGENT)
            ids = {p["id"] for p in policies}
            assert "flag_old_conflicts" not in ids
        finally:
            with get_connection() as conn:
                conn.execute("UPDATE lifecycle_policies SET enabled = 1 WHERE id = 'flag_old_conflicts'")


class TestDecayBelowPolicy:
    def setup_method(self):
        _cleanup()

    def teardown_method(self):
        _cleanup()

    def test_archives_forgotten_memory(self):
        mem_id = _create_memory("forgotten memory")
        _set_decay(mem_id, 0.01, days_idle=100)
        result = evaluate_and_apply(agent_id=_AGENT)
        assert result.archived >= 1
        with get_connection() as conn:
            row = conn.execute("SELECT archived_at FROM memories WHERE id = ?", (mem_id,)).fetchone()
        assert row["archived_at"] is not None

    def test_skips_fresh_memory(self):
        mem_id = _create_memory("fresh memory")
        _set_decay(mem_id, 0.95)
        result = evaluate_and_apply(agent_id=_AGENT)
        archived_ids = {a.memory_id for a in result.actions if a.action == "archive"}
        assert mem_id not in archived_ids

    def test_skips_recently_accessed(self):
        mem_id = _create_memory("recently accessed")
        _set_decay(mem_id, 0.01, days_idle=10)  # idle only 10 days, threshold is 90
        result = evaluate_and_apply(agent_id=_AGENT)
        archived_ids = {a.memory_id for a in result.actions if a.action == "archive"}
        assert mem_id not in archived_ids

    def test_skips_already_archived(self):
        mem_id = _create_memory("already archived")
        _set_decay(mem_id, 0.01, days_idle=100)
        archive_memory(mem_id, _AGENT)
        result = evaluate_and_apply(agent_id=_AGENT)
        archived_ids = {a.memory_id for a in result.actions if a.action == "archive"}
        assert mem_id not in archived_ids


class TestConflictPolicy:
    def setup_method(self):
        _cleanup()

    def teardown_method(self):
        _cleanup()

    def test_flags_old_unresolved_conflict(self):
        mem_a = _create_memory("conflict A")
        mem_b = _create_memory("conflict B")
        _create_conflict(mem_a, mem_b, days_ago=45)
        result = evaluate_and_apply(agent_id=_AGENT)
        flagged_ids = {a.memory_id for a in result.actions if a.action == "flag"}
        assert mem_a in flagged_ids or mem_b in flagged_ids

    def test_skips_recent_conflict(self):
        mem_a = _create_memory("recent conflict A")
        mem_b = _create_memory("recent conflict B")
        _create_conflict(mem_a, mem_b, days_ago=5)
        result = evaluate_and_apply(agent_id=_AGENT)
        flagged_ids = {a.memory_id for a in result.actions if a.action == "flag"}
        assert mem_a not in flagged_ids
        assert mem_b not in flagged_ids

    def test_flag_persisted_in_policy_flags(self):
        mem_a = _create_memory("flag persist A")
        mem_b = _create_memory("flag persist B")
        _create_conflict(mem_a, mem_b, days_ago=45)
        evaluate_and_apply(agent_id=_AGENT)
        with get_connection() as conn:
            flags = conn.execute(
                "SELECT memory_id, policy_id FROM policy_flags WHERE policy_id = 'flag_old_conflicts'"
            ).fetchall()
        flagged_mem_ids = {r[0] for r in flags}
        assert mem_a in flagged_mem_ids or mem_b in flagged_mem_ids


class TestAgeAndIdlePolicy:
    def setup_method(self):
        _cleanup()

    def teardown_method(self):
        _cleanup()

    def test_archives_stale_runbook(self):
        mem_id = _create_memory("old runbook", category="runbook")
        _set_created_at(mem_id, days_ago=400)
        _set_decay(mem_id, 0.5, days_idle=200)
        # Set memory_type to procedural
        with get_connection() as conn:
            conn.execute("UPDATE memories SET memory_type = 'procedural' WHERE id = ?", (mem_id,))
        result = evaluate_and_apply(agent_id=_AGENT)
        archived_ids = {a.memory_id for a in result.actions if a.action == "archive"}
        assert mem_id in archived_ids

    def test_skips_recent_runbook(self):
        mem_id = _create_memory("recent runbook", category="runbook")
        with get_connection() as conn:
            conn.execute("UPDATE memories SET memory_type = 'procedural' WHERE id = ?", (mem_id,))
        result = evaluate_and_apply(agent_id=_AGENT)
        archived_ids = {a.memory_id for a in result.actions if a.action == "archive"}
        assert mem_id not in archived_ids

    def test_skips_non_runbook_category(self):
        mem_id = _create_memory("old general memory", category="general")
        _set_created_at(mem_id, days_ago=400)
        _set_decay(mem_id, 0.5, days_idle=200)
        result = evaluate_and_apply(agent_id=_AGENT)
        # archive_stale_runbooks only targets category=runbook
        actions_for_mem = [a for a in result.actions if a.memory_id == mem_id and a.policy_id == "archive_stale_runbooks"]
        assert len(actions_for_mem) == 0


class TestDryRun:
    def setup_method(self):
        _cleanup()

    def teardown_method(self):
        _cleanup()

    def test_dry_run_does_not_archive(self):
        mem_id = _create_memory("dry run test")
        _set_decay(mem_id, 0.01, days_idle=100)
        result = evaluate_and_apply(agent_id=_AGENT, dry_run=True)
        assert result.evaluated >= 1
        assert result.archived == 0
        with get_connection() as conn:
            row = conn.execute("SELECT archived_at FROM memories WHERE id = ?", (mem_id,)).fetchone()
        assert row["archived_at"] is None

    def test_dry_run_does_not_flag(self):
        mem_a = _create_memory("dry flag A")
        mem_b = _create_memory("dry flag B")
        _create_conflict(mem_a, mem_b, days_ago=45)
        result = evaluate_and_apply(agent_id=_AGENT, dry_run=True)
        assert result.flagged == 0
        with get_connection() as conn:
            count = conn.execute("SELECT COUNT(*) FROM policy_flags WHERE memory_id IN (?, ?)", (mem_a, mem_b)).fetchone()[0]
        assert count == 0


class TestPolicyAppliedEvent:
    def setup_method(self):
        _cleanup()
        clear_events()
        self.events_received = []
        on(POLICY_APPLIED, lambda e, d: self.events_received.append(d))

    def teardown_method(self):
        _cleanup()
        clear_events()

    def test_archive_emits_policy_applied(self):
        mem_id = _create_memory("event test")
        _set_decay(mem_id, 0.01, days_idle=100)
        evaluate_and_apply(agent_id=_AGENT)
        archive_events = [e for e in self.events_received if e.get("action") == "archive"]
        assert len(archive_events) >= 1
        assert archive_events[0]["policy_id"] == "auto_archive_forgotten"

    def test_flag_emits_policy_applied(self):
        mem_a = _create_memory("event flag A")
        mem_b = _create_memory("event flag B")
        _create_conflict(mem_a, mem_b, days_ago=45)
        evaluate_and_apply(agent_id=_AGENT)
        flag_events = [e for e in self.events_received if e.get("action") == "flag"]
        assert len(flag_events) >= 1


class TestRunDecayPassIntegration:
    def setup_method(self):
        _cleanup()

    def teardown_method(self):
        _cleanup()

    def test_decay_pass_returns_policy_result(self):
        mem_id = _create_memory("decay pass integration")
        updated, policy_result = run_decay_pass(agent_id=_AGENT)
        assert updated >= 1
        assert policy_result is not None
        assert isinstance(policy_result, PolicyRunResult)

    def test_decay_pass_dry_run(self):
        mem_id = _create_memory("decay dry run")
        _set_decay(mem_id, 0.01, days_idle=100)
        updated, policy_result = run_decay_pass(agent_id=_AGENT, dry_run=True)
        assert updated >= 1
        # dry_run: no actual archiving
        with get_connection() as conn:
            row = conn.execute("SELECT archived_at FROM memories WHERE id = ?", (mem_id,)).fetchone()
        assert row["archived_at"] is None


class TestPolicyFlaggedCondition:
    def setup_method(self):
        _cleanup()

    def teardown_method(self):
        _cleanup()

    def test_flagged_memory_has_condition(self):
        from kore_memory.repository.search import _load_flagged_ids

        mem_id = _create_memory("flagged condition test")
        with get_connection() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO policy_flags (memory_id, policy_id) VALUES (?, 'flag_old_conflicts')",
                (mem_id,),
            )
        flagged = _load_flagged_ids([mem_id])
        assert mem_id in flagged

    def test_unflagged_memory_not_in_set(self):
        from kore_memory.repository.search import _load_flagged_ids

        mem_id = _create_memory("unflagged test")
        flagged = _load_flagged_ids([mem_id])
        assert mem_id not in flagged


class TestPolicyRunResult:
    def test_default_values(self):
        r = PolicyRunResult()
        assert r.evaluated == 0
        assert r.archived == 0
        assert r.flagged == 0
        assert r.actions == []
