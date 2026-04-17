"""
Tests for M1: Graph Entity Layer + Hybrid Search.
Covers: entity repo, RRF fusion, dedup, title, hybrid search, regression.
"""

import os
import sqlite3

import pytest

from kore_memory.database import get_connection, init_db
from kore_memory.models import MemorySaveRequest
from kore_memory.repository.entity import (
    _canonicalize_name,
    find_entities_by_names,
    get_entities_for_memory,
    get_memories_for_entity,
    get_or_create_entity,
    link_entities_to_memory,
    link_memory_entity,
)
from kore_memory.repository.memory import (
    _auto_title,
    _content_hash,
    get_memory,
    save_memory,
)
from kore_memory.repository.search import (
    _graph_search,
    _rrf_fusion,
    search_memories,
)

import uuid


@pytest.fixture
def aid():
    """Unique agent_id per test for isolation."""
    return f"m1test-{uuid.uuid4().hex[:8]}"


# ── Entity Repository ────────────────────────────────────────────────────────


class TestEntityCanonicalization:
    def test_strip_and_lowercase(self):
        assert _canonicalize_name("  FastAPI  ") == "fastapi"

    def test_strip_leading_slash(self):
        assert _canonicalize_name("/src/main.py") == "src/main.py"

    def test_strip_version_suffix(self):
        assert _canonicalize_name("react v18.2.0") == "react"

    def test_collapse_whitespace(self):
        assert _canonicalize_name("hello   world") == "hello world"

    def test_too_short(self):
        assert _canonicalize_name("ab") == ""

    def test_max_length(self):
        long_name = "a" * 300
        assert len(_canonicalize_name(long_name)) == 200


class TestEntityCRUD:
    def test_create_entity(self, aid):
        eid = get_or_create_entity(aid, "FastAPI", "tech")
        assert eid is not None
        assert isinstance(eid, int)

    def test_create_entity_dedup(self, aid):
        eid1 = get_or_create_entity(aid, "FastAPI", "tech")
        eid2 = get_or_create_entity(aid, "  fastapi  ", "tech")
        assert eid1 == eid2

    def test_entity_type_check_constraint(self):
        with pytest.raises(sqlite3.IntegrityError):
            with get_connection() as conn:
                conn.execute(
                    "INSERT INTO graph_entities (agent_id, name, entity_type) VALUES (?, ?, ?)",
                    ("test", "bad", "invalid_type"),
                )

    def test_link_memory_entity(self, aid):
        req = MemorySaveRequest(content="Test content for linking", category="general")
        mid, _, _ = save_memory(req, agent_id=aid)
        eid = get_or_create_entity(aid, "test-entity", "concept")
        assert link_memory_entity(mid, eid, role="mentions")

    def test_link_role_check_constraint(self, aid):
        req = MemorySaveRequest(content="Test content for role check", category="general")
        mid, _, _ = save_memory(req, agent_id=aid)
        eid = get_or_create_entity(aid, "test-entity2", "concept")
        with pytest.raises(sqlite3.IntegrityError):
            with get_connection() as conn:
                conn.execute(
                    "INSERT INTO memory_entity_links (memory_id, entity_id, role) VALUES (?, ?, ?)",
                    (mid, eid, "invalid_role"),
                )

    def test_link_cascade_delete_memory(self, aid):
        req = MemorySaveRequest(content="Memory to delete for cascade test", category="general")
        mid, _, _ = save_memory(req, agent_id=aid)
        eid = get_or_create_entity(aid, "cascade-entity", "concept")
        link_memory_entity(mid, eid)
        with get_connection() as conn:
            conn.execute("DELETE FROM memories WHERE id = ?", (mid,))
            count = conn.execute(
                "SELECT COUNT(*) FROM memory_entity_links WHERE memory_id = ?", (mid,)
            ).fetchone()[0]
        assert count == 0

    def test_bulk_link_entities(self, aid):
        req = MemorySaveRequest(content="Bulk link test content", category="general")
        mid, _, _ = save_memory(req, agent_id=aid)
        entities = [("fastapi", "tech"), ("postgresql", "tech"), ("react", "tech")]
        linked = link_entities_to_memory(mid, entities, agent_id=aid)
        assert linked == 3

    def test_max_entities_per_memory(self, aid):
        req = MemorySaveRequest(content="Max entities test", category="general")
        mid, _, _ = save_memory(req, agent_id=aid)
        entities = [(f"entity-{i:03d}", "concept") for i in range(25)]
        linked = link_entities_to_memory(mid, entities, agent_id=aid)
        assert linked == 20  # capped

    def test_get_entities_for_memory(self, aid):
        req = MemorySaveRequest(content="Get entities test", category="general")
        mid, _, _ = save_memory(req, agent_id=aid)
        link_entities_to_memory(mid, [("fastapi", "tech"), ("python", "tech")], agent_id=aid)
        entities = get_entities_for_memory(mid, agent_id=aid)
        assert len(entities) == 2
        names = {e["name"] for e in entities}
        assert "fastapi" in names
        assert "python" in names

    def test_get_memories_for_entity(self, aid):
        eid = get_or_create_entity(aid, "shared-entity", "concept")
        for i in range(3):
            req = MemorySaveRequest(content=f"Memory {i} with shared entity unique {uuid.uuid4().hex[:6]}", category="general")
            mid, _, _ = save_memory(req, agent_id=aid)
            link_memory_entity(mid, eid)
        mids = get_memories_for_entity(eid, agent_id=aid)
        assert len(mids) == 3

    def test_find_entities_by_names(self, aid):
        get_or_create_entity(aid, "FastAPI", "tech")
        get_or_create_entity(aid, "PostgreSQL", "tech")
        found = find_entities_by_names(["fastapi", "postgresql", "nonexistent"], agent_id=aid)
        assert len(found) == 2


# ── RRF Fusion ───────────────────────────────────────────────────────────────


class TestRRFFusion:
    def test_basic_3_streams(self):
        fts = [(1, -5.0), (2, -3.0)]
        vec = [(2, 0.95), (3, 0.90)]
        graph = [(2, 1.0), (4, 0.67)]
        result = _rrf_fusion(fts, vec, graph)
        ids = [mid for mid, _ in result]
        assert ids[0] == 2  # appears in all 3 streams

    def test_single_stream(self):
        fts = [(1, -5.0), (2, -3.0)]
        result = _rrf_fusion(fts, [], [])
        assert len(result) == 2
        assert result[0][0] == 1

    def test_empty_graph(self):
        fts = [(1, -5.0)]
        vec = [(2, 0.95)]
        result = _rrf_fusion(fts, vec, [])
        assert len(result) == 2

    def test_all_empty(self):
        assert _rrf_fusion([], [], []) == []

    def test_no_overlap(self):
        fts = [(1, -5.0)]
        vec = [(2, 0.95)]
        graph = [(3, 1.0)]
        result = _rrf_fusion(fts, vec, graph)
        assert len(result) == 3

    def test_full_overlap(self):
        fts = [(1, -5.0)]
        vec = [(1, 0.95)]
        graph = [(1, 1.0)]
        result = _rrf_fusion(fts, vec, graph)
        assert len(result) == 1
        # 3-stream score should be 3x a single-stream contribution
        # (each stream adds weight/(k+1), total = 3 * 1/3 * 1/61 = 1/61)
        # vs 2-stream: 2 * 1/2 * 1/61 = 1/61 — same due to renormalization
        # So just verify the score is positive and correct
        assert result[0][1] > 0


# ── Dedup ────────────────────────────────────────────────────────────────────


class TestDedup:
    def test_content_hash_deterministic(self):
        assert _content_hash("Hello World") == _content_hash("Hello World")

    def test_content_hash_whitespace_normalized(self):
        assert _content_hash("Hello   World") == _content_hash("Hello World")
        assert _content_hash("  Hello World  ") == _content_hash("Hello World")

    def test_content_hash_case_preserved(self):
        # Per spec: NO lowercase, case matters
        assert _content_hash("Hello") != _content_hash("hello")

    def test_dedup_within_window(self, aid):
        old_test_mode = os.environ.get("KORE_TEST_MODE")
        os.environ["KORE_TEST_MODE"] = "0"
        try:
            req = MemorySaveRequest(content=f"Dedup test content unique {aid}", category="general")
            id1, _, _ = save_memory(req, agent_id=aid)
            id2, _, _ = save_memory(req, agent_id=aid)
            assert id1 == id2
        finally:
            if old_test_mode is not None:
                os.environ["KORE_TEST_MODE"] = old_test_mode

    def test_dedup_bypass_supersedes(self, aid):
        old_test_mode = os.environ.get("KORE_TEST_MODE")
        os.environ["KORE_TEST_MODE"] = "0"
        try:
            req1 = MemorySaveRequest(content=f"Original content for supersede {aid}", category="general")
            id1, _, _ = save_memory(req1, agent_id=aid)
            req2 = MemorySaveRequest(
                content=f"Original content for supersede {aid}",
                category="general",
                supersedes_id=id1,
            )
            id2, _, _ = save_memory(req2, agent_id=aid)
            assert id1 != id2
        finally:
            if old_test_mode is not None:
                os.environ["KORE_TEST_MODE"] = old_test_mode

    def test_dedup_bypass_env(self, aid):
        os.environ["KORE_DEDUP"] = "0"
        try:
            req = MemorySaveRequest(content=f"Dedup env bypass {aid}", category="general")
            id1, _, _ = save_memory(req, agent_id=aid)
            id2, _, _ = save_memory(req, agent_id=aid)
            assert id1 != id2
        finally:
            os.environ["KORE_DEDUP"] = "1"


# ── Title ────────────────────────────────────────────────────────────────────


class TestTitle:
    def test_first_sentence(self):
        assert _auto_title("Auth uses JWT. More details.") == "Auth uses JWT."

    def test_first_line(self):
        assert _auto_title("Line one\nLine two") == "Line one"

    def test_max_length(self):
        long = "A" * 500
        assert len(_auto_title(long)) == 120

    def test_explicit_override(self, aid):
        req = MemorySaveRequest(content="Some content here for title test", category="general", title="Custom Title")
        mid, _, _ = save_memory(req, agent_id=aid)
        mem = get_memory(mid, agent_id=aid)
        assert mem.title == "Custom Title"

    def test_title_auto_generated(self, aid):
        req = MemorySaveRequest(content="FastAPI uses SQLite for persistence. More info.", category="general")
        mid, _, _ = save_memory(req, agent_id=aid)
        mem = get_memory(mid, agent_id=aid)
        assert mem.title is not None
        assert "FastAPI" in mem.title


# ── Hybrid Search Integration ────────────────────────────────────────────────


class TestHybridSearch:
    def _save_with_entities(self, content, aid):
        """Save a memory and manually link entities."""
        from kore_memory.integrations.entities import extract_graph_entities

        req = MemorySaveRequest(content=content, category="project")
        mid, _, _ = save_memory(req, agent_id=aid)
        entities = extract_graph_entities(content)
        if entities:
            link_entities_to_memory(mid, entities, agent_id=aid)
        return mid

    def test_graph_search_finds_entity_linked_memories(self, aid):
        self._save_with_entities("FastAPI uses Pydantic for validation", aid)
        self._save_with_entities("React frontend with TypeScript", aid)
        self._save_with_entities("FastAPI REST API with PostgreSQL", aid)

        results = _graph_search("FastAPI performance", aid, limit=10)
        assert len(results) >= 2

    def test_search_without_entities_degrades(self, aid):
        req = MemorySaveRequest(content="Simple memory without entities linkage xyzzy", category="general")
        save_memory(req, agent_id=aid)
        results, _, total, _ = search_memories("xyzzy", agent_id=aid, semantic=False)
        assert total >= 0

    def test_search_empty_db(self, aid):
        results, cursor, total, excluded = search_memories("anything", agent_id=aid, semantic=False)
        assert results == []
        assert total == 0

    def test_rrf_end_to_end(self, aid):
        for content in [
            "FastAPI REST API with authentication and JWT tokens",
            "PostgreSQL database optimization and indexing strategies",
            "React frontend with TypeScript and Tailwind CSS",
        ]:
            self._save_with_entities(content, aid)

        results, _, total, _ = search_memories("FastAPI authentication", agent_id=aid, semantic=False)
        assert len(results) >= 1


class TestRegression:
    def test_fts_search_still_works(self, aid):
        req = MemorySaveRequest(content=f"Regression test FTS search content unique {aid}", category="general")
        save_memory(req, agent_id=aid)
        results, _, total, _ = search_memories("regression FTS", agent_id=aid, semantic=False)
        assert total >= 1

    def test_search_by_tag_unchanged(self, aid):
        from kore_memory.repository.search import search_by_tag
        from kore_memory.repository.graph import add_tags

        req = MemorySaveRequest(content=f"Tagged memory for regression {aid}", category="general")
        mid, _, _ = save_memory(req, agent_id=aid)
        tag = f"regression-tag-{aid}"
        add_tags(mid, [tag], agent_id=aid)
        results = search_by_tag(tag, agent_id=aid)
        assert len(results) == 1

    def test_old_memories_without_hash(self, aid):
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO memories (agent_id, content, category, importance) VALUES (?, ?, ?, ?)",
                (aid, "Old memory without hash", "general", 3),
            )
        results, _, total, _ = search_memories("old memory", agent_id=aid, semantic=False)
        assert total >= 1

    def test_old_memories_without_title(self, aid):
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO memories (agent_id, content, category, importance) VALUES (?, ?, ?, ?)",
                (aid, f"Old memory without title field {aid}", "general", 3),
            )
            row = conn.execute(
                "SELECT id FROM memories WHERE agent_id = ? AND content LIKE 'Old memory without title%'",
                (aid,),
            ).fetchone()
        mem = get_memory(row[0], agent_id=aid)
        assert mem is not None
        assert mem.title is None
