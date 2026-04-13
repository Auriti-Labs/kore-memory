"""
Kore — MCP server tests
Tests MCP tool functions directly (no MCP protocol needed).
Each tool is a plain Python function that calls repository layer.

Setup: temp DB + local-only mode, same pattern as test_api.py.
Must set env vars BEFORE importing mcp_server (it calls init_db at import time).
Requires optional [mcp] dependency — skipped if not installed.
"""

import pytest

pytest.importorskip("mcp", reason="mcp package not installed (optional dependency)")

from kore_memory.mcp_server import (  # noqa: E402
    memory_add_relation,
    memory_add_tags,
    memory_cleanup,
    memory_delete,
    memory_export,
    memory_import,
    memory_save,
    memory_save_batch,
    memory_search,
    memory_search_by_tag,
    memory_timeline,
    memory_update,
)

AGENT = "mcp-test-agent"


class TestMemorySave:
    def test_save_returns_id_and_importance(self):
        result = memory_save(
            content="MCP test: remember this important fact",
            category="general",
            agent_id=AGENT,
        )
        assert "id" in result
        assert result["id"] > 0
        assert "importance" in result
        assert result["importance"] >= 1
        assert result["message"] == "Memory saved"

    def test_save_with_explicit_importance(self):
        result = memory_save(
            content="Critical security credential for production",
            category="project",
            importance=5,
            agent_id=AGENT,
        )
        assert result["importance"] == 5

    def test_save_with_category(self):
        result = memory_save(
            content="Juan prefers dark mode in all editors",
            category="preference",
            agent_id=AGENT,
        )
        assert result["id"] > 0


class TestMemorySearch:
    def test_search_finds_saved_memory(self):
        memory_save(
            content="Semantic search test: unique kangaroo phrase",
            category="general",
            agent_id=AGENT,
        )
        result = memory_search(
            query="kangaroo",
            limit=5,
            semantic=False,
            agent_id=AGENT,
        )
        assert "results" in result
        assert "total" in result
        assert "has_more" in result
        assert any("kangaroo" in r["content"] for r in result["results"])

    def test_search_returns_empty_for_no_match(self):
        result = memory_search(
            query="zzzyyyxxx_nonexistent_term",
            limit=5,
            semantic=False,
            agent_id=AGENT,
        )
        assert result["results"] == []

    def test_search_with_category_filter(self):
        memory_save(
            content="Finance test: quarterly earnings report analysis",
            category="finance",
            agent_id=AGENT,
        )
        result = memory_search(
            query="earnings",
            limit=5,
            category="finance",
            semantic=False,
            agent_id=AGENT,
        )
        found = result["results"]
        assert all(r["category"] == "finance" for r in found)


class TestMemoryDelete:
    def test_delete_existing_memory(self):
        saved = memory_save(
            content="This memory will be deleted soon",
            category="general",
            agent_id=AGENT,
        )
        mem_id = saved["id"]
        result = memory_delete(memory_id=mem_id, agent_id=AGENT)
        assert result["success"] is True
        assert result["message"] == "Memory deleted"

    def test_delete_nonexistent_memory(self):
        result = memory_delete(memory_id=999999, agent_id=AGENT)
        assert result["success"] is False
        assert result["message"] == "Memory not found"

    def test_delete_wrong_agent(self):
        saved = memory_save(
            content="Memory owned by mcp-test-agent only",
            category="general",
            agent_id=AGENT,
        )
        result = memory_delete(memory_id=saved["id"], agent_id="wrong-agent")
        assert result["success"] is False


class TestMemoryUpdate:
    def test_update_content(self):
        saved = memory_save(
            content="Original content before update",
            category="general",
            agent_id=AGENT,
        )
        result = memory_update(
            memory_id=saved["id"],
            content="Updated content after modification",
            agent_id=AGENT,
        )
        assert result["success"] is True
        assert result["message"] == "Memory updated"

    def test_update_category(self):
        saved = memory_save(
            content="Will change category from general to project",
            category="general",
            agent_id=AGENT,
        )
        result = memory_update(
            memory_id=saved["id"],
            category="project",
            agent_id=AGENT,
        )
        assert result["success"] is True

    def test_update_importance(self):
        saved = memory_save(
            content="Will increase importance to maximum",
            category="general",
            importance=1,
            agent_id=AGENT,
        )
        result = memory_update(
            memory_id=saved["id"],
            importance=5,
            agent_id=AGENT,
        )
        assert result["success"] is True

    def test_update_nonexistent(self):
        result = memory_update(
            memory_id=999999,
            content="This should fail",
            agent_id=AGENT,
        )
        assert result["success"] is False
        assert result["message"] == "Memory not found"


class TestMemoryAddTags:
    def test_add_tags_to_memory(self):
        saved = memory_save(
            content="Memory that needs tags for organization",
            category="general",
            agent_id=AGENT,
        )
        result = memory_add_tags(
            memory_id=saved["id"],
            tags=["python", "testing", "mcp"],
            agent_id=AGENT,
        )
        assert result["count"] == 3
        assert "3 tags added" in result["message"]

    def test_add_tags_to_nonexistent_memory(self):
        result = memory_add_tags(
            memory_id=999999,
            tags=["orphan"],
            agent_id=AGENT,
        )
        assert result["count"] == 0


class TestMemorySearchByTag:
    def test_search_by_tag_finds_tagged_memory(self):
        saved = memory_save(
            content="Tagged memory for search by tag test",
            category="project",
            agent_id=AGENT,
        )
        memory_add_tags(
            memory_id=saved["id"],
            tags=["unique-tag-xyz"],
            agent_id=AGENT,
        )
        result = memory_search_by_tag(
            tag="unique-tag-xyz",
            agent_id=AGENT,
        )
        assert result["total"] >= 1
        assert any(r["id"] == saved["id"] for r in result["results"])

    def test_search_by_tag_no_results(self):
        result = memory_search_by_tag(
            tag="nonexistent-tag-abc",
            agent_id=AGENT,
        )
        assert result["total"] == 0
        assert result["results"] == []


class TestMemoryCleanup:
    def test_cleanup_returns_count(self):
        result = memory_cleanup(agent_id=AGENT)
        assert "removed" in result
        assert isinstance(result["removed"], int)
        assert "message" in result


class TestMemoryExport:
    def test_export_returns_memories(self):
        # Save a memory first to ensure there's something to export
        memory_save(
            content="Memory for export test verification",
            category="general",
            agent_id=AGENT,
        )
        result = memory_export(agent_id=AGENT)
        assert "memories" in result
        assert "total" in result
        assert result["total"] >= 1
        assert isinstance(result["memories"], list)

    def test_export_empty_agent(self):
        result = memory_export(agent_id="empty-agent-no-memories")
        assert result["total"] == 0
        assert result["memories"] == []


class TestMemoryImport:
    def test_import_memories(self):
        records = [
            {"content": "Imported memory one for testing", "category": "general", "importance": 2},
            {"content": "Imported memory two for testing", "category": "project", "importance": 3},
        ]
        result = memory_import(memories=records, agent_id=AGENT)
        assert result["imported"] == 2
        assert "2 memories imported" in result["message"]

    def test_import_skips_invalid(self):
        records = [
            {"content": "Valid imported memory content"},
            {"content": "ab"},           # too short (< 3 chars)
            {"content": "  "},           # blank
            {"content": ""},             # empty
        ]
        result = memory_import(memories=records, agent_id=AGENT)
        assert result["imported"] == 1


class TestMemorySaveBatch:
    def test_save_batch(self):
        memories = [
            {"content": "Batch memory alpha for testing", "category": "general"},
            {"content": "Batch memory beta for testing", "category": "project", "importance": 3},
        ]
        result = memory_save_batch(memories=memories, agent_id=AGENT)
        assert result["total"] == 2
        assert len(result["saved"]) == 2
        assert all("id" in s for s in result["saved"])

    def test_save_batch_skips_invalid(self):
        memories = [
            {"content": "Valid batch content here"},
            {"content": "ab"},   # too short
        ]
        result = memory_save_batch(memories=memories, agent_id=AGENT)
        assert result["total"] == 1


class TestMemoryAddRelation:
    def test_add_relation_between_memories(self):
        m1 = memory_save(content="Source memory for relation test", category="general", agent_id=AGENT)
        m2 = memory_save(content="Target memory for relation test", category="general", agent_id=AGENT)
        result = memory_add_relation(
            source_id=m1["id"],
            target_id=m2["id"],
            relation="related",
            agent_id=AGENT,
        )
        assert result["success"] is True
        assert result["message"] == "Relation created"

    def test_add_relation_nonexistent_memory(self):
        m1 = memory_save(content="Existing memory for failed relation", category="general", agent_id=AGENT)
        result = memory_add_relation(
            source_id=m1["id"],
            target_id=999999,
            relation="related",
            agent_id=AGENT,
        )
        assert result["success"] is False


class TestMemoryTimeline:
    def test_timeline_returns_results(self):
        memory_save(
            content="Timeline event: project Kore started development",
            category="project",
            agent_id=AGENT,
        )
        result = memory_timeline(
            subject="Kore",
            limit=10,
            agent_id=AGENT,
        )
        assert "results" in result
        assert "total" in result
        assert "has_more" in result
        assert isinstance(result["results"], list)


# ── Issue #007: MCP Hardening ─────────────────────────────────────────────────


class TestMCPHardening:
    def test_mcp_timeout_config_exists(self):
        """KORE_MCP_TIMEOUT_SECONDS è configurabile via config."""
        from kore_memory import config as cfg
        assert hasattr(cfg, "MCP_TIMEOUT_SECONDS")
        assert isinstance(cfg.MCP_TIMEOUT_SECONDS, int)
        assert cfg.MCP_TIMEOUT_SECONDS > 0

    def test_mcp_port_config_exists(self):
        """KORE_MCP_PORT è configurabile via config."""
        from kore_memory import config as cfg
        assert hasattr(cfg, "MCP_PORT")
        assert isinstance(cfg.MCP_PORT, int)

    def test_mcp_timeout_from_env(self, monkeypatch):
        """KORE_MCP_TIMEOUT_SECONDS viene letto dall'env var."""
        import importlib
        import kore_memory.config as cfg_mod
        monkeypatch.setenv("KORE_MCP_TIMEOUT_SECONDS", "60")
        importlib.reload(cfg_mod)
        assert cfg_mod.MCP_TIMEOUT_SECONDS == 60
        monkeypatch.delenv("KORE_MCP_TIMEOUT_SECONDS", raising=False)
        importlib.reload(cfg_mod)

    def test_health_module_importable(self):
        """Il modulo mcp_server esporta _add_health_route e _SERVER_START_TIME."""
        from kore_memory.mcp_server import _add_health_route, _SERVER_START_TIME
        assert callable(_add_health_route)
        assert isinstance(_SERVER_START_TIME, float)
        assert _SERVER_START_TIME > 0

    def test_error_helper_returns_dict_with_error_key(self):
        """_error() formatta gli errori in modo consistente per i tool MCP."""
        from kore_memory.mcp_server import _error
        result = _error("Operazione non consentita")
        assert isinstance(result, dict)
        assert "error" in result
        assert "Operazione" in result["error"]

    def test_save_100_consecutive_calls(self):
        """100 chiamate consecutive a memory_save senza crash o leak."""
        for i in range(100):
            result = memory_save(
                content=f"Stress test memoria numero {i} per hardening MCP",
                category="general",
                agent_id="test-hardening",
            )
            assert "id" in result
            assert result["id"] > 0


# ── Issue #012: Coding Memory Mode Alpha ─────────────────────────────────────


class TestCodingMemoryMode:
    """Test per i tool MCP specializzati del Coding Memory Mode."""

    def test_memory_save_decision_returns_correct_fields(self):
        """memory_save_decision ritorna id, category e memory_type."""
        from kore_memory.mcp_server import memory_save_decision
        result = memory_save_decision(
            content="Usiamo FastAPI invece di Django per la leggerezza e le performance",
            rationale="FastAPI ha type hints nativi e async out-of-the-box",
            alternatives_considered="Django, Flask, Starlette",
            decided_by="tech-lead",
            agent_id="test-coding",
        )
        assert "id" in result
        assert result["id"] > 0
        assert result["category"] == "architectural_decision"
        assert result["memory_type"] == "semantic"
        assert result["importance"] >= 4
        assert "conflicts_detected" in result

    def test_memory_save_decision_with_repo_namespace(self):
        """memory_save_decision usa namespace agent/repo per l'isolamento."""
        from kore_memory.mcp_server import memory_save_decision
        result = memory_save_decision(
            content="Database schema: usa UUID come primary key",
            rationale="Distribuito, no conflicts tra istanze",
            repo="my-project",
            agent_id="claude-code",
        )
        assert result["id"] > 0
        assert result["message"] == "Decision saved"

    def test_memory_get_runbook_returns_list(self):
        """memory_get_runbook ritorna una lista di runbook."""
        from kore_memory.mcp_server import memory_get_runbook, memory_save
        # Salva un runbook prima
        memory_save(
            content="Procedura rollback: 1) git revert 2) deploy 3) verifica",
            category="runbook",
            agent_id="test-coding",
        )
        result = memory_get_runbook(
            trigger="rollback",
            component="deploy",
            agent_id="test-coding",
        )
        assert "results" in result
        assert "total" in result
        assert isinstance(result["results"], list)

    def test_memory_log_regression_returns_episodic(self):
        """memory_log_regression crea memoria di tipo episodic."""
        from kore_memory.mcp_server import memory_log_regression
        result = memory_log_regression(
            content="Race condition nel pool SQLite con scritture concorrenti",
            introduced_in="v1.2.0",
            fixed_in="v1.2.1",
            test_ref="tests/test_database.py::test_concurrent_writes",
            agent_id="test-coding",
        )
        assert "id" in result
        assert result["id"] > 0
        assert result["category"] == "regression_note"
        assert result["memory_type"] == "episodic"
        assert result["importance"] >= 4

    def test_coding_categories_all_valid(self):
        """Tutte le categorie coding mode sono accettate da memory_save."""
        coding_cats = [
            "architectural_decision",
            "root_cause",
            "runbook",
            "regression_note",
            "tech_debt",
            "api_contract",
        ]
        for cat in coding_cats:
            result = memory_save(
                content=f"Test memoria categoria {cat} coding mode alpha",
                category=cat,
                agent_id="test-coding",
            )
            assert result["id"] > 0, f"Categoria {cat} ha fallito"

    def test_memory_type_inferred_from_coding_category(self):
        """memory_type viene inferito automaticamente dalla category coding."""
        from kore_memory.repository import get_memory
        from kore_memory.mcp_server import memory_save
        # runbook → procedural
        result = memory_save(
            content="Runbook per deploy in produzione: step 1, 2, 3",
            category="runbook",
            agent_id="test-coding",
        )
        mem = get_memory(result["id"], agent_id="test-coding")
        assert mem is not None
        assert mem.memory_type == "procedural"
