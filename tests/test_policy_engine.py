"""
Tests for Lifecycle Policy Engine v1 — Step 1: Schema + Seed Data.
"""

import json
import sqlite3

from kore_memory.database import get_connection, init_db


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
