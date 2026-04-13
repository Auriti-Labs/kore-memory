"""
Test per i fix della v1.1.0 — Stability release.
Verifica: archived leak (export, search_by_tag, count), emit audit,
handler dedup, VectorIndex thread-safety, compression depth limit,
PRAGMA SQLite.
"""

import threading

from fastapi.testclient import TestClient

from kore_memory import events
from kore_memory.database import get_connection
from kore_memory.main import app
from kore_memory.repository import (
    _count_active_memories,
    add_tags,
    archive_memory,
    export_memories,
    restore_memory,
    run_decay_pass,
    save_memory,
    search_by_tag,
)

HEADERS = {"X-Agent-Id": "test-v11"}
client = TestClient(app)

# ── Helper ──────────────────────────────────────────────────────────────────

def _save(content: str, **kwargs) -> int:
    """Salva una memoria e ritorna l'id."""
    from kore_memory.models import MemorySaveRequest
    req = MemorySaveRequest(content=content, **kwargs)
    mid, _, _conflicts = save_memory(req, agent_id="test-v11")
    return mid


def _cleanup_agent():
    """Pulisce tutte le memorie dell'agente test-v11."""
    with get_connection() as conn:
        conn.execute("DELETE FROM memory_tags WHERE memory_id IN (SELECT id FROM memories WHERE agent_id = 'test-v11')")
        conn.execute("DELETE FROM memories WHERE agent_id = 'test-v11'")
        conn.execute("DELETE FROM event_logs WHERE agent_id = 'test-v11'")


# ── BUG: export_memories include memorie archiviate ─────────────────────────


class TestExportArchivedLeak:
    def setup_method(self):
        _cleanup_agent()

    def test_export_excludes_archived(self):
        """export_memories() NON deve includere memorie archiviate."""
        mid1 = _save("Memoria attiva per export test")
        mid2 = _save("Memoria da archiviare per export test")
        archive_memory(mid2, agent_id="test-v11")

        exported = export_memories(agent_id="test-v11")
        exported_ids = {m["id"] for m in exported}

        assert mid1 in exported_ids, "Memoria attiva mancante dall'export"
        assert mid2 not in exported_ids, "Memoria archiviata trovata nell'export — BUG"

    def test_export_api_excludes_archived(self):
        """L'endpoint /export NON deve includere memorie archiviate."""
        mid1 = _save("API export memoria attiva")
        mid2 = _save("API export memoria archiviata")
        archive_memory(mid2, agent_id="test-v11")

        r = client.get("/export", headers=HEADERS)
        assert r.status_code == 200
        exported_ids = {m["id"] for m in r.json()["memories"]}

        assert mid1 in exported_ids
        assert mid2 not in exported_ids


# ── BUG: search_by_tag include memorie archiviate ──────────────────────────


class TestSearchByTagArchivedLeak:
    def setup_method(self):
        _cleanup_agent()

    def test_search_by_tag_excludes_archived(self):
        """search_by_tag() NON deve ritornare memorie archiviate."""
        mid1 = _save("Memoria taggata attiva")
        mid2 = _save("Memoria taggata archiviata")
        add_tags(mid1, ["v11-test"], agent_id="test-v11")
        add_tags(mid2, ["v11-test"], agent_id="test-v11")
        archive_memory(mid2, agent_id="test-v11")

        results = search_by_tag("v11-test", agent_id="test-v11")
        result_ids = {r.id for r in results}

        assert mid1 in result_ids, "Memoria attiva taggata mancante"
        assert mid2 not in result_ids, "Memoria archiviata trovata in search_by_tag — BUG"


# ── BUG: _count_active_memories conta archiviate ──────────────────────────


class TestCountActiveArchivedLeak:
    def setup_method(self):
        _cleanup_agent()

    def test_count_excludes_archived(self):
        """_count_active_memories() NON deve contare memorie archiviate."""
        _save("Conteggio contaxyz memoria attiva")
        mid2 = _save("Conteggio contaxyz memoria archiviata")
        archive_memory(mid2, agent_id="test-v11")

        # Cerca con termine presente in entrambe le memorie
        count = _count_active_memories("contaxyz", None, "test-v11")
        # Solo la memoria attiva deve essere contata
        assert count == 1, f"Atteso 1 (solo attiva), ottenuto {count}"


# ── BUG: eventi audit mai emessi ───────────────────────────────────────────


class TestAuditEventEmission:
    def setup_method(self):
        _cleanup_agent()
        events.clear()
        self._captured: list[tuple[str, dict]] = []

        def _capture(event: str, data: dict):
            self._captured.append((event, data))

        # Registra handler per catturare gli eventi
        events.on(events.MEMORY_ARCHIVED, _capture)
        events.on(events.MEMORY_RESTORED, _capture)
        events.on(events.MEMORY_DECAYED, _capture)
        events.on(events.MEMORY_COMPRESSED, _capture)

    def teardown_method(self):
        events.clear()

    def test_archive_emits_event(self):
        """archive_memory() deve emettere MEMORY_ARCHIVED."""
        mid = _save("Test emissione evento archive")
        archive_memory(mid, agent_id="test-v11")

        archived_events = [(e, d) for e, d in self._captured if e == events.MEMORY_ARCHIVED]
        assert len(archived_events) == 1
        assert archived_events[0][1]["id"] == mid

    def test_restore_emits_event(self):
        """restore_memory() deve emettere MEMORY_RESTORED."""
        mid = _save("Test emissione evento restore")
        archive_memory(mid, agent_id="test-v11")
        self._captured.clear()  # Ignora evento archive

        restore_memory(mid, agent_id="test-v11")

        restored_events = [(e, d) for e, d in self._captured if e == events.MEMORY_RESTORED]
        assert len(restored_events) == 1
        assert restored_events[0][1]["id"] == mid

    def test_decay_emits_event(self):
        """run_decay_pass() deve emettere MEMORY_DECAYED."""
        _save("Test emissione evento decay", importance=3)

        run_decay_pass(agent_id="test-v11")

        decayed_events = [(e, d) for e, d in self._captured if e == events.MEMORY_DECAYED]
        assert len(decayed_events) == 1
        assert decayed_events[0][1]["updated"] >= 1


# ── FIX: handler deduplication ──────────────────────────────────────────────


class TestHandlerDedup:
    def setup_method(self):
        events.clear()

    def teardown_method(self):
        events.clear()

    def test_duplicate_handler_ignored(self):
        """Registrare lo stesso handler due volte non duplica le chiamate."""
        call_count = [0]

        def _counter(event: str, data: dict):
            call_count[0] += 1

        events.on("test.event", _counter)
        events.on("test.event", _counter)  # duplicato — deve essere ignorato

        events.emit("test.event", {"test": True})
        assert call_count[0] == 1, f"Handler chiamato {call_count[0]} volte, atteso 1"


# ── PERF: PRAGMA SQLite ──────────────────────────────────────────────────────


class TestSQLitePragmas:
    def test_synchronous_normal(self):
        """Le connessioni devono usare PRAGMA synchronous=NORMAL."""
        with get_connection() as conn:
            result = conn.execute("PRAGMA synchronous").fetchone()
            # NORMAL = 1
            assert result[0] == 1, f"synchronous atteso 1 (NORMAL), ottenuto {result[0]}"

    def test_temp_store_memory(self):
        """Le connessioni devono usare PRAGMA temp_store=MEMORY."""
        with get_connection() as conn:
            result = conn.execute("PRAGMA temp_store").fetchone()
            # MEMORY = 2
            assert result[0] == 2, f"temp_store atteso 2 (MEMORY), ottenuto {result[0]}"

    def test_mmap_size(self):
        """Le connessioni devono avere mmap_size > 0."""
        with get_connection() as conn:
            result = conn.execute("PRAGMA mmap_size").fetchone()
            assert result[0] > 0, f"mmap_size atteso > 0, ottenuto {result[0]}"

    def test_cache_size(self):
        """Le connessioni devono avere cache_size negativo (KB)."""
        with get_connection() as conn:
            result = conn.execute("PRAGMA cache_size").fetchone()
            assert result[0] < 0, f"cache_size atteso negativo (KB), ottenuto {result[0]}"


# ── PERF: indice composito ───────────────────────────────────────────────────


class TestCompositeIndex:
    def test_idx_agent_decay_active_exists(self):
        """L'indice composito idx_agent_decay_active deve esistere."""
        with get_connection() as conn:
            indexes = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_agent_decay_active'"
            ).fetchall()
            assert len(indexes) == 1, "Indice composito idx_agent_decay_active mancante"


# ── VectorIndex thread-safety ────────────────────────────────────────────────


class TestVectorIndexThreadSafety:
    def test_concurrent_invalidate_and_load(self):
        """Invalidate e load_vectors concorrenti non devono crashare."""
        from kore_memory.vector_index import VectorIndex

        idx = VectorIndex()
        errors: list[Exception] = []

        def _invalidate():
            try:
                for _ in range(100):
                    idx.invalidate("test-agent")
            except Exception as e:
                errors.append(e)

        def _load():
            try:
                for _ in range(100):
                    idx.load_vectors("test-agent")
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=_invalidate),
            threading.Thread(target=_load),
            threading.Thread(target=_invalidate),
            threading.Thread(target=_load),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors, f"Errori durante accesso concorrente: {errors}"
