"""
Tests for M2: Structured Memory + Readability.
Covers: structured extraction, privacy filter, integration, regression, backward compat.
"""

import json
import os
import uuid

import pytest

from kore_memory.database import get_connection, init_db
from kore_memory.models import MemorySaveRequest
from kore_memory.privacy import privacy_filter
from kore_memory.repository.memory import get_memory, save_memory, export_memories
from kore_memory.repository.search import search_memories
from kore_memory.structured import extract_structured


@pytest.fixture
def aid():
    return f"m2test-{uuid.uuid4().hex[:8]}"


# ── Structured Extraction ────────────────────────────────────────────────────


class TestStructuredExtraction:
    def test_extract_facts_from_assertive_sentences(self):
        content = "FastAPI uses Pydantic. Redis is fast. This is a note. SQLite stores data."
        facts, _, _ = extract_structured(content)
        assert facts is not None
        assert any("uses" in f.lower() or "is" in f.lower() or "stores" in f.lower() for f in facts)

    def test_extract_facts_max_20(self):
        sentences = [f"System uses component_{i} for processing." for i in range(30)]
        content = " ".join(sentences)
        facts, _, _ = extract_structured(content)
        assert facts is not None
        assert len(facts) <= 20

    def test_extract_facts_dedup(self):
        content = "FastAPI uses Pydantic. FastAPI uses Pydantic. SQLite is the backend."
        facts, _, _ = extract_structured(content)
        assert facts is not None
        pydantic_facts = [f for f in facts if "Pydantic" in f]
        assert len(pydantic_facts) == 1

    def test_extract_facts_max_length(self):
        long_sentence = "The system uses " + "x" * 250 + " for processing. Short fact here."
        facts, _, _ = extract_structured(long_sentence)
        if facts:
            for f in facts:
                assert len(f) <= 200

    def test_extract_concepts_frequency(self):
        content = "Authentication handles auth. Authentication is critical. Caching improves caching speed."
        _, concepts, _ = extract_structured(content)
        assert concepts is not None
        assert "authentication" in concepts

    def test_extract_concepts_no_stopwords(self):
        content = "There should always be something between these things. Another thing about nothing."
        _, concepts, _ = extract_structured(content)
        if concepts:
            for c in concepts:
                assert c not in {"there", "should", "always", "something", "between", "these", "things", "another", "about", "nothing"}

    def test_extract_concepts_max_15(self):
        words = [f"concept{i}" for i in range(30)]
        content = " ".join(f"{w} is important. {w} matters." for w in words)
        _, concepts, _ = extract_structured(content)
        assert concepts is not None
        assert len(concepts) <= 15

    def test_extract_narrative_top_sentences(self):
        content = "FastAPI uses Pydantic for validation. This is filler. SQLite stores all data. More filler here. The ranking engine supports signals."
        _, _, narrative = extract_structured(content)
        assert narrative is not None
        assert len(narrative) > 0

    def test_extract_narrative_max_500(self):
        sentences = [f"Sentence number {i} has important technical content about systems." for i in range(50)]
        content = " ".join(sentences)
        _, _, narrative = extract_structured(content)
        if narrative:
            assert len(narrative) <= 500

    def test_extract_short_content_returns_none(self):
        f, c, n = extract_structured("Too short")
        assert f is None and c is None and n is None

    def test_extract_single_sentence_returns_none(self):
        f, c, n = extract_structured("Only one sentence here without any period ending")
        assert f is None and c is None and n is None


# ── Privacy Filter ───────────────────────────────────────────────────────────


class TestPrivacyFilter:
    def test_filter_bearer_token(self):
        result = privacy_filter("Use Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N for auth")
        assert "eyJhbGci" not in result
        assert "[REDACTED]" in result

    def test_filter_aws_key(self):
        result = privacy_filter("Key is AKIA1234567890ABCDEF")
        assert "AKIA1234567890ABCDEF" not in result
        assert "[AWS_KEY_REDACTED]" in result

    def test_filter_private_key(self):
        pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA\n-----END RSA PRIVATE KEY-----"
        result = privacy_filter(f"Key: {pem}")
        assert "MIIEpAIBAAKCAQEA" not in result
        assert "[PRIVATE_KEY_REDACTED]" in result

    def test_filter_connection_string(self):
        result = privacy_filter("Connect to postgresql://admin:s3cr3tP4ss@db.host/mydb")
        assert "s3cr3tP4ss" not in result
        assert "[REDACTED]" in result

    def test_filter_password_assignment(self):
        result = privacy_filter('password = "my_super_secret_123"')
        assert "my_super_secret_123" not in result
        assert "[REDACTED]" in result

    def test_filter_preserves_normal_content(self):
        content = "FastAPI uses SQLite for persistence. The ranking engine has 7 signals."
        assert privacy_filter(content) == content

    def test_filter_disabled_via_env(self):
        os.environ["KORE_PRIVACY_FILTER"] = "0"
        try:
            content = "Bearer eyJhbGciOiJIUzI1NiJ9.secret"
            assert privacy_filter(content) == content
        finally:
            os.environ["KORE_PRIVACY_FILTER"] = "1"


# ── Integration ──────────────────────────────────────────────────────────────


class TestM2Integration:
    def test_save_with_auto_extraction(self, aid):
        req = MemorySaveRequest(
            content="FastAPI uses Pydantic for validation. SQLite is the storage backend. The ranking engine supports 7 weighted signals.",
            category="project",
        )
        mid, _, _ = save_memory(req, agent_id=aid)
        mem = get_memory(mid, agent_id=aid)
        assert mem.facts is not None
        assert len(mem.facts) >= 2
        assert mem.concepts is not None
        assert mem.narrative is not None

    def test_save_with_explicit_facts(self, aid):
        req = MemorySaveRequest(
            content="Some long content about the project. It has multiple sentences. The system handles requests.",
            category="project",
            facts=["Explicit fact A", "Explicit fact B"],
        )
        mid, _, _ = save_memory(req, agent_id=aid)
        mem = get_memory(mid, agent_id=aid)
        assert mem.facts == ["Explicit fact A", "Explicit fact B"]

    def test_save_with_explicit_narrative(self, aid):
        req = MemorySaveRequest(
            content="Some content here. More content follows. The system is complex.",
            category="project",
            narrative="Custom narrative override",
        )
        mid, _, _ = save_memory(req, agent_id=aid)
        mem = get_memory(mid, agent_id=aid)
        assert mem.narrative == "Custom narrative override"

    def test_save_short_content_no_structured(self, aid):
        req = MemorySaveRequest(content="Short note", category="general")
        mid, _, _ = save_memory(req, agent_id=aid)
        mem = get_memory(mid, agent_id=aid)
        assert mem.facts is None
        assert mem.concepts is None
        assert mem.narrative is None

    def test_save_persists_metadata_json(self, aid):
        req = MemorySaveRequest(
            content="Memory with metadata for testing",
            category="project",
            metadata={"repo": "kore-memory", "version": "4.0"},
        )
        mid, _, _ = save_memory(req, agent_id=aid)
        mem = get_memory(mid, agent_id=aid)
        assert mem.metadata == {"repo": "kore-memory", "version": "4.0"}

    def test_get_memory_returns_structured_fields(self, aid):
        req = MemorySaveRequest(
            content="FastAPI uses Pydantic. SQLite is the backend. The system handles auth.",
            category="project",
            metadata={"key": "value"},
        )
        mid, _, _ = save_memory(req, agent_id=aid)
        mem = get_memory(mid, agent_id=aid)
        assert mem.title is not None
        assert mem.metadata == {"key": "value"}
        # facts/concepts/narrative may or may not be populated depending on content

    def test_search_finds_via_narrative(self, aid):
        req = MemorySaveRequest(
            content="The xylophone_unique_term is used for testing. It handles requests. The system processes data.",
            category="project",
        )
        mid, _, _ = save_memory(req, agent_id=aid)
        # narrative should contain xylophone_unique_term
        results, _, total, _ = search_memories("xylophone_unique_term", agent_id=aid, semantic=False)
        assert total >= 1

    def test_search_finds_via_title(self, aid):
        req = MemorySaveRequest(
            content="Zephyr_unique_keyword handles all requests. More details follow. The system is robust.",
            category="project",
        )
        save_memory(req, agent_id=aid)
        results, _, total, _ = search_memories("Zephyr_unique_keyword", agent_id=aid, semantic=False)
        assert total >= 1

    def test_privacy_filter_on_save(self, aid):
        req = MemorySaveRequest(
            content="Use Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.secret for auth. The API connects to postgresql://admin:p4ssw0rd@db.host/mydb. It handles requests.",
            category="project",
        )
        mid, _, _ = save_memory(req, agent_id=aid)
        mem = get_memory(mid, agent_id=aid)
        # Content must be filtered
        assert "eyJhbGci" not in mem.content
        assert "p4ssw0rd" not in mem.content
        # Title derived from filtered content
        if mem.title:
            assert "eyJhbGci" not in mem.title
        # Narrative derived from filtered content
        if mem.narrative:
            assert "eyJhbGci" not in mem.narrative
            assert "p4ssw0rd" not in mem.narrative
        # Facts derived from filtered content
        if mem.facts:
            for fact in mem.facts:
                assert "eyJhbGci" not in fact
                assert "p4ssw0rd" not in fact

    def test_privacy_filter_disabled(self, aid):
        os.environ["KORE_PRIVACY_FILTER"] = "0"
        try:
            req = MemorySaveRequest(
                content="Bearer eyJhbGciOiJIUzI1NiJ9.secret_token_here is the key. More text follows. System handles it.",
                category="project",
            )
            mid, _, _ = save_memory(req, agent_id=aid)
            mem = get_memory(mid, agent_id=aid)
            assert "secret_token_here" in mem.content
        finally:
            os.environ["KORE_PRIVACY_FILTER"] = "1"


# ── Regression ───────────────────────────────────────────────────────────────


class TestM2Regression:
    def test_pre_m2_memories_readable(self, aid):
        """Memories with NULL on all M2 fields should produce valid MemoryRecord."""
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO memories (agent_id, content, category, importance) VALUES (?, ?, ?, ?)",
                (aid, "Old memory without M2 fields", "general", 3),
            )
            row = conn.execute(
                "SELECT id FROM memories WHERE agent_id = ? AND content LIKE 'Old memory without M2%'",
                (aid,),
            ).fetchone()
        mem = get_memory(row[0], agent_id=aid)
        assert mem is not None
        assert mem.facts is None
        assert mem.concepts is None
        assert mem.narrative is None
        assert mem.metadata is None

    def test_fts_search_unchanged(self, aid):
        req = MemorySaveRequest(content=f"FTS regression test unique content {aid}", category="general")
        save_memory(req, agent_id=aid)
        results, _, total, _ = search_memories("FTS regression", agent_id=aid, semantic=False)
        assert total >= 1

    def test_rrf_fusion_unchanged(self, aid):
        req = MemorySaveRequest(
            content="RRF regression test with FastAPI and PostgreSQL. The system uses Docker. It handles requests.",
            category="project",
        )
        save_memory(req, agent_id=aid)
        results, _, total, _ = search_memories("FastAPI Docker", agent_id=aid, semantic=False)
        assert total >= 1

    def test_dedup_unchanged(self, aid):
        old_test_mode = os.environ.get("KORE_TEST_MODE")
        os.environ["KORE_TEST_MODE"] = "0"
        try:
            req = MemorySaveRequest(content=f"Dedup regression test {aid}", category="general")
            id1, _, _ = save_memory(req, agent_id=aid)
            id2, _, _ = save_memory(req, agent_id=aid)
            assert id1 == id2
        finally:
            if old_test_mode is not None:
                os.environ["KORE_TEST_MODE"] = old_test_mode

    def test_entity_extraction_unchanged(self, aid):
        """Entity extraction still works alongside structured extraction."""
        from kore_memory.integrations.entities import extract_graph_entities
        entities = extract_graph_entities("FastAPI uses PostgreSQL for storage")
        assert len(entities) >= 2

    def test_export_includes_new_fields(self, aid):
        req = MemorySaveRequest(
            content="Export test with structured fields. The system uses FastAPI. It handles auth.",
            category="project",
            metadata={"test": True},
        )
        save_memory(req, agent_id=aid)
        exported = export_memories(agent_id=aid)
        assert len(exported) >= 1
        assert "facts_json" in exported[0]
        assert "metadata_json" in exported[0]
