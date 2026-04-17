"""
Tests for M3a: Session Consolidation.
Covers: aggregation helpers, consolidate_session gates, integration, regression.
"""

import json
import os
import uuid

import pytest

from kore_memory.consolidation import (
    _aggregate_concepts,
    _aggregate_facts,
    _aggregate_narrative,
    _sentence_merge,
    consolidate_agent,
    consolidate_session,
)
from kore_memory.database import get_connection
from kore_memory.models import MemorySaveRequest
from kore_memory.repository.memory import get_memory, save_memory
from kore_memory.repository.search import search_memories
from kore_memory.repository.sessions import create_session, end_session


@pytest.fixture
def aid():
    return f"m3a-{uuid.uuid4().hex[:8]}"


@pytest.fixture(autouse=True)
def _no_conflict_sync():
    """Disable conflict sync in tests to avoid false conflicts on similar content."""
    import kore_memory.config as cfg
    old = cfg.CONFLICT_SYNC
    cfg.CONFLICT_SYNC = False
    yield
    cfg.CONFLICT_SYNC = old


_DIVERSE_CONTENTS = [
    "FastAPI uses Pydantic for request validation. It handles HTTP routing. The framework supports async.",
    "PostgreSQL stores relational data efficiently. It supports JSONB columns. Indexing improves query speed.",
    "Docker containers isolate applications. Kubernetes orchestrates deployment. Helm manages charts.",
    "JWT tokens handle authentication securely. OAuth2 provides authorization flows. CORS manages origins.",
    "Redis caches frequently accessed data. Memcached is an alternative. Cache invalidation is critical.",
    "React renders UI components efficiently. Virtual DOM optimizes updates. Hooks manage component state.",
    "SQLite uses WAL mode for concurrency. FTS5 enables full-text search. Triggers sync derived data.",
]


def _save_in_session(content, session_id, agent_id, **kwargs):
    """Helper: save a memory linked to a session."""
    req = MemorySaveRequest(content=content, category=kwargs.get("category", "project"), **kwargs)
    mid, _, _ = save_memory(req, agent_id=agent_id, session_id=session_id)
    return mid


# ── Unit: Aggregation Helpers ────────────────────────────────────────────────


class TestAggregation:
    def test_aggregate_facts_dedup(self):
        candidates = [
            {"facts_json": json.dumps(["FastAPI uses Pydantic", "SQLite is the backend"])},
            {"facts_json": json.dumps(["FastAPI uses Pydantic", "Docker handles deployment"])},
            {"facts_json": json.dumps(["Redis caches data"])},
        ]
        facts = _aggregate_facts(candidates)
        assert facts is not None
        assert len(facts) == 4
        assert sum(1 for f in facts if "Pydantic" in f) == 1  # deduped

    def test_aggregate_facts_max_20(self):
        candidates = [{"facts_json": json.dumps([f"Fact number {i}" for i in range(25)])}]
        facts = _aggregate_facts(candidates)
        assert len(facts) == 20

    def test_aggregate_facts_none_when_empty(self):
        assert _aggregate_facts([{"facts_json": None}, {"content": "no facts"}]) is None

    def test_aggregate_concepts_dedup_sorted(self):
        candidates = [
            {"concepts_json": json.dumps(["fastapi", "sqlite", "docker"])},
            {"concepts_json": json.dumps(["fastapi", "redis", "sqlite"])},
            {"concepts_json": json.dumps(["fastapi", "pydantic"])},
        ]
        concepts = _aggregate_concepts(candidates)
        assert concepts is not None
        assert concepts[0] == "fastapi"  # highest frequency (3)
        assert len(set(concepts)) == len(concepts)  # no dupes

    def test_aggregate_concepts_max_15(self):
        candidates = [{"concepts_json": json.dumps([f"concept{i}" for i in range(20)])}]
        concepts = _aggregate_concepts(candidates)
        assert len(concepts) == 15

    def test_aggregate_narrative_from_top_facts(self):
        candidates = [
            {"facts_json": json.dumps(["Low importance fact"]), "importance": 1},
            {"facts_json": json.dumps(["High importance fact"]), "importance": 5},
            {"facts_json": json.dumps(["Medium importance fact"]), "importance": 3},
        ]
        narrative = _aggregate_narrative(candidates)
        assert narrative is not None
        assert "High importance fact" in narrative

    def test_aggregate_narrative_none_when_no_facts(self):
        assert _aggregate_narrative([{"facts_json": None}]) is None

    def test_sentence_merge(self):
        candidates = [
            {"content": "First sentence. Second sentence."},
            {"content": "Second sentence. Third sentence."},
        ]
        merged = _sentence_merge(candidates)
        assert "First sentence" in merged
        assert "Third sentence" in merged
        assert merged.count("Second sentence") == 1  # deduped


# ── Integration: consolidate_session gates ───────────────────────────────────


class TestConsolidateSession:
    def _setup_session(self, aid, n_memories=5, end=True, structured=True):
        """Create a session with N memories, optionally ended."""
        sid = f"sess-{uuid.uuid4().hex[:8]}"
        create_session(sid, agent_id=aid, title="Test Session")
        for i in range(n_memories):
            if structured:
                content = _DIVERSE_CONTENTS[i % len(_DIVERSE_CONTENTS)]
            else:
                content = f"Short mem {i} xyz"
            _save_in_session(content, sid, aid)
        if end:
            with get_connection() as conn:
                conn.execute(
                    "UPDATE sessions SET ended_at = datetime('now') WHERE id = ?", (sid,)
                )
        return sid

    def test_creates_episodic(self, aid):
        sid = self._setup_session(aid)
        result = consolidate_session(sid, aid)
        assert result["consolidated"] is True
        assert "episodic_id" in result
        mem = get_memory(result["episodic_id"], agent_id=aid)
        assert mem is not None
        assert mem.memory_type == "episodic"

    def test_sources_marked_compressed(self, aid):
        sid = self._setup_session(aid)
        result = consolidate_session(sid, aid)
        eid = result["episodic_id"]
        with get_connection() as conn:
            compressed = conn.execute(
                "SELECT COUNT(*) FROM memories WHERE session_id = ? AND compressed_into = ?",
                (sid, eid),
            ).fetchone()[0]
        assert compressed == 5

    def test_metadata_tracks_sources(self, aid):
        sid = self._setup_session(aid)
        result = consolidate_session(sid, aid)
        mem = get_memory(result["episodic_id"], agent_id=aid)
        assert mem.metadata is not None
        assert "source_memory_ids" in mem.metadata
        assert mem.metadata["consolidation_type"] == "session_to_episodic"
        assert len(mem.metadata["source_memory_ids"]) == 5

    def test_provenance_tracks_session(self, aid):
        sid = self._setup_session(aid)
        result = consolidate_session(sid, aid)
        mem = get_memory(result["episodic_id"], agent_id=aid)
        assert mem.provenance is not None
        assert mem.provenance["source_ref"] == f"session:{sid}"
        assert mem.provenance["source_type"] == "consolidation"

    def test_session_not_ended_skip(self, aid):
        sid = self._setup_session(aid, end=False)
        result = consolidate_session(sid, aid)
        assert result["consolidated"] is False
        assert result["skipped"] == "session_not_ended"

    def test_too_few_memories_skip(self, aid):
        sid = self._setup_session(aid, n_memories=2)
        result = consolidate_session(sid, aid)
        assert result["consolidated"] is False
        assert result["skipped"] == "too_few_candidates"

    def test_idempotent(self, aid):
        sid = self._setup_session(aid)
        r1 = consolidate_session(sid, aid)
        assert r1["consolidated"] is True
        r2 = consolidate_session(sid, aid)
        assert r2["consolidated"] is False
        assert r2["skipped"] == "already_consolidated"

    def test_conflicted_excluded_rest_consolidated(self, aid):
        sid = self._setup_session(aid, n_memories=5)
        # Get memory IDs in this session
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT id FROM memories WHERE session_id = ? AND agent_id = ? AND compressed_into IS NULL",
                (sid, aid),
            ).fetchall()
        mem_ids = [r[0] for r in rows]
        # Create an unresolved conflict on the first memory
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO memory_conflicts (id, memory_a_id, memory_b_id, agent_id) VALUES (?, ?, ?, ?)",
                (f"conflict-{uuid.uuid4().hex[:8]}", mem_ids[0], mem_ids[1], aid),
            )
        result = consolidate_session(sid, aid)
        assert result["consolidated"] is True
        assert result["excluded_conflicted"] == 2  # both sides of conflict
        assert result["sources_compressed"] == 3  # 5 - 2

    def test_all_conflicted_skip(self, aid):
        sid = self._setup_session(aid, n_memories=3)
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT id FROM memories WHERE session_id = ? AND agent_id = ? AND compressed_into IS NULL",
                (sid, aid),
            ).fetchall()
        mem_ids = [r[0] for r in rows]
        # Conflict all 3
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO memory_conflicts (id, memory_a_id, memory_b_id, agent_id) VALUES (?, ?, ?, ?)",
                (f"c-{uuid.uuid4().hex[:8]}", mem_ids[0], mem_ids[1], aid),
            )
            conn.execute(
                "INSERT INTO memory_conflicts (id, memory_a_id, memory_b_id, agent_id) VALUES (?, ?, ?, ?)",
                (f"c-{uuid.uuid4().hex[:8]}", mem_ids[1], mem_ids[2], aid),
            )
        result = consolidate_session(sid, aid)
        assert result["consolidated"] is False
        assert result["skipped"] == "too_few_candidates"

    def test_low_confidence_skip(self, aid):
        sid = f"sess-{uuid.uuid4().hex[:8]}"
        create_session(sid, agent_id=aid)
        for i in range(4):
            req = MemorySaveRequest(
                content=f"Low confidence memory {i} about systems. It handles data. The process runs.",
                category="general",
                confidence=0.3,
            )
            save_memory(req, agent_id=aid, session_id=sid)
        with get_connection() as conn:
            conn.execute("UPDATE sessions SET ended_at = datetime('now') WHERE id = ?", (sid,))
        result = consolidate_session(sid, aid)
        assert result["consolidated"] is False
        assert result["skipped"] == "low_confidence"

    def test_no_structured_fallback_merge(self, aid):
        sid = f"sess-{uuid.uuid4().hex[:8]}"
        create_session(sid, agent_id=aid)
        # Insert pre-M2 style memories (no structured fields)
        with get_connection() as conn:
            for i in range(4):
                conn.execute(
                    "INSERT INTO memories (agent_id, content, category, importance, session_id) VALUES (?,?,?,?,?)",
                    (aid, f"Pre-M2 memory {i} about topic alpha.", "general", 3, sid),
                )
            conn.execute("UPDATE sessions SET ended_at = datetime('now') WHERE id = ?", (sid,))
        result = consolidate_session(sid, aid)
        assert result["consolidated"] is True
        mem = get_memory(result["episodic_id"], agent_id=aid)
        assert mem.facts is None  # no fabricated facts
        assert "Pre-M2 memory" in mem.content

    def test_episodic_searchable(self, aid):
        sid = self._setup_session(aid)
        result = consolidate_session(sid, aid)
        mem = get_memory(result["episodic_id"], agent_id=aid)
        # Search for a word that must be in the episodic content
        search_term = mem.content.split()[0]  # first word of episodic content
        results, _, total, _ = search_memories(search_term, agent_id=aid, semantic=False)
        ids = [r.id for r in results]
        assert result["episodic_id"] in ids

    def test_sources_excluded_from_search(self, aid):
        sid = self._setup_session(aid)
        # Get source IDs before consolidation
        with get_connection() as conn:
            source_rows = conn.execute(
                "SELECT id FROM memories WHERE session_id = ? AND agent_id = ?", (sid, aid)
            ).fetchall()
        source_ids = {r[0] for r in source_rows}
        consolidate_session(sid, aid)
        results, _, _, _ = search_memories("FastAPI", agent_id=aid, semantic=False)
        result_ids = {r.id for r in results}
        # Sources should not appear in search (compressed_into IS NOT NULL)
        assert not (source_ids & result_ids)

    def test_end_session_auto_consolidates(self, aid):
        sid = f"sess-{uuid.uuid4().hex[:8]}"
        create_session(sid, agent_id=aid, title="Auto Test")
        for i in range(4):
            _save_in_session(_DIVERSE_CONTENTS[i], sid, aid)
        # end_session triggers auto-consolidation
        end_session(sid, agent_id=aid)
        # Check episodic was created
        with get_connection() as conn:
            row = conn.execute(
                "SELECT id FROM memories WHERE agent_id = ? AND memory_type = 'episodic'"
                " AND provenance LIKE ?",
                (aid, f'%"source_ref": "session:{sid}"%'),
            ).fetchone()
        assert row is not None

    def test_consolidate_agent_multiple_sessions(self, aid):
        for _ in range(3):
            sid = self._setup_session(aid)
        result = consolidate_agent(aid)
        assert result["sessions_consolidated"] == 3


# ── Regression ───────────────────────────────────────────────────────────────


class TestM3aRegression:
    def test_compressor_still_works(self, aid):
        """Existing compressor is not broken."""
        from kore_memory.compressor import run_compression
        result = run_compression(agent_id=aid)
        assert result.clusters_found >= 0  # no crash

    def test_search_unchanged(self, aid):
        req = MemorySaveRequest(content=f"M3a regression search test {aid}", category="general")
        save_memory(req, agent_id=aid)
        _, _, total, _ = search_memories("M3a regression", agent_id=aid, semantic=False)
        assert total >= 1

    def test_existing_sessions_api_unchanged(self, aid):
        from kore_memory.repository.sessions import list_sessions, get_session_summary
        sid = f"sess-{uuid.uuid4().hex[:8]}"
        create_session(sid, agent_id=aid, title="Regression")
        sessions = list_sessions(agent_id=aid)
        assert any(s["id"] == sid for s in sessions)
        summary = get_session_summary(sid, agent_id=aid)
        assert summary["session_id"] == sid

    def test_pre_m3a_memories_unaffected(self, aid):
        """Memories without session_id are not touched by consolidation."""
        req = MemorySaveRequest(content="Standalone memory no session", category="general")
        mid, _, _ = save_memory(req, agent_id=aid)
        result = consolidate_agent(aid)
        mem = get_memory(mid, agent_id=aid)
        assert mem is not None
        assert mem.content == "Standalone memory no session"
