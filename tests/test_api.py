"""
Kore — API tests
Fast, no-network, uses TestClient (ASGI in-process).
Auth: local-only mode enabled in tests (KORE_LOCAL_ONLY=1).
"""

import os

import pytest
from fastapi.testclient import TestClient

from kore_memory.main import app  # noqa: E402

# Header di default: namespace agent per test di isolamento
HEADERS = {"X-Agent-Id": "test-agent"}
OTHER_AGENT = {"X-Agent-Id": "other-agent"}

client = TestClient(app)


class TestHealth:
    def test_health_returns_ok(self):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"
        assert "semantic_search" in r.json()


class TestSave:
    def test_save_basic(self):
        r = client.post("/save", json={"content": "Juan works on Kore memory system", "category": "project"}, headers=HEADERS)
        assert r.status_code == 201
        data = r.json()
        assert "id" in data
        assert data["importance"] >= 1

    def test_save_auto_scores_credentials(self):
        r = client.post("/save", json={
            "content": "API token: sk-abc123 for production",
            "category": "general",
        }, headers=HEADERS)
        assert r.status_code == 201
        assert r.json()["importance"] == 5

    def test_save_rejects_blank(self):
        r = client.post("/save", json={"content": "   ", "category": "general"}, headers=HEADERS)
        assert r.status_code == 422

    def test_save_rejects_too_short(self):
        r = client.post("/save", json={"content": "hi", "category": "general"}, headers=HEADERS)
        assert r.status_code == 422


class TestAuth:
    def test_no_key_on_remote_fails(self):
        """When KORE_LOCAL_ONLY=0 and no key, should get 401."""
        os.environ["KORE_LOCAL_ONLY"] = "0"
        try:
            r = client.get("/search?q=test")
            assert r.status_code == 401
        finally:
            os.environ["KORE_LOCAL_ONLY"] = "1"

    def test_wrong_key_fails(self):
        os.environ["KORE_LOCAL_ONLY"] = "0"
        try:
            r = client.get("/search?q=test", headers={"X-Kore-Key": "wrong-key"})
            assert r.status_code == 403
        finally:
            os.environ["KORE_LOCAL_ONLY"] = "1"


class TestAgentIsolation:
    def setup_method(self):
        client.post("/save", json={"content": "Secret data for agent A only", "category": "general", "importance": 3}, headers=HEADERS)

    def test_other_agent_cannot_see_data(self):
        r = client.get("/search?q=Secret&semantic=false", headers=OTHER_AGENT)
        assert r.json()["total"] == 0

    def test_owner_agent_can_see_data(self):
        r = client.get("/search?q=Secret&semantic=false", headers=HEADERS)
        assert r.json()["total"] >= 1

    def test_other_agent_cannot_delete(self):
        save_r = client.post("/save", json={"content": "Memory to protect", "category": "general"}, headers=HEADERS)
        mid = save_r.json()["id"]
        del_r = client.delete(f"/memories/{mid}", headers=OTHER_AGENT)
        assert del_r.status_code == 404  # not found for other agent


class TestSearch:
    def setup_method(self):
        client.post("/save", json={"content": "CalcFast is a calculator website for Italian taxes", "category": "project", "importance": 3}, headers=HEADERS)
        client.post("/save", json={"content": "Betfair account has 40 euros for bot trading", "category": "finance", "importance": 4}, headers=HEADERS)

    def test_search_returns_results(self):
        r = client.get("/search?q=CalcFast&semantic=false", headers=HEADERS)
        assert r.status_code == 200
        assert r.json()["total"] >= 1

    def test_search_category_filter(self):
        r = client.get("/search?q=trading&category=finance&semantic=false", headers=HEADERS)
        assert r.status_code == 200
        for result in r.json()["results"]:
            assert result["category"] == "finance"


class TestDecay:
    def test_decay_run(self):
        r = client.post("/decay/run", headers=HEADERS)
        assert r.status_code == 200
        assert r.json()["updated"] >= 0


class TestCompress:
    def test_compress_run(self):
        r = client.post("/compress", headers=HEADERS)
        assert r.status_code == 200
        assert "clusters_found" in r.json()


class TestTimeline:
    def test_timeline(self):
        r = client.get("/timeline?subject=CalcFast", headers=HEADERS)
        assert r.status_code == 200
        results = r.json()["results"]
        if len(results) > 1:
            dates = [r["created_at"] for r in results]
            assert dates == sorted(dates)


class TestDelete:
    def test_delete_existing(self):
        save_r = client.post("/save", json={"content": "Temporary memory to delete", "category": "general"}, headers=HEADERS)
        mid = save_r.json()["id"]
        del_r = client.delete(f"/memories/{mid}", headers=HEADERS)
        assert del_r.status_code == 204

    def test_delete_nonexistent(self):
        r = client.delete("/memories/999999", headers=HEADERS)
        assert r.status_code == 404


# ── P3: Batch API ────────────────────────────────────────────────────────────

class TestBatchSave:
    def setup_method(self):
        from kore_memory.main import _rate_buckets
        _rate_buckets.clear()

    def test_batch_save_multiple(self):
        """Salva 3 memorie in un'unica richiesta batch."""
        payload = {
            "memories": [
                {"content": "Batch memory one", "category": "general"},
                {"content": "Batch memory two", "category": "project", "importance": 3},
                {"content": "Batch memory three", "category": "finance"},
            ]
        }
        r = client.post("/save/batch", json=payload, headers=HEADERS)
        assert r.status_code == 201
        data = r.json()
        assert data["total"] == 3
        assert len(data["saved"]) == 3
        # Ogni elemento ha id e importance
        for item in data["saved"]:
            assert "id" in item
            assert item["importance"] >= 1

    def test_batch_save_single(self):
        """Batch con una sola memoria — deve funzionare."""
        r = client.post("/save/batch", json={
            "memories": [{"content": "Single batch item", "category": "general"}]
        }, headers=HEADERS)
        assert r.status_code == 201
        assert r.json()["total"] == 1

    def test_batch_save_empty_rejected(self):
        """Batch vuoto — rifiutato da validazione."""
        r = client.post("/save/batch", json={"memories": []}, headers=HEADERS)
        assert r.status_code == 422

    def test_batch_save_invalid_content(self):
        """Batch con contenuto troppo corto — rifiutato."""
        r = client.post("/save/batch", json={
            "memories": [{"content": "ab", "category": "general"}]
        }, headers=HEADERS)
        assert r.status_code == 422

    def test_batch_parity_with_single_save(self):
        """Batch save must populate the same structured fields as single save."""
        from kore_memory.database import get_connection

        content = "PostgreSQL is better than MySQL for JSONB support and advanced queries in our backend architecture"
        # Single save
        r1 = client.post("/save", json={"content": content, "category": "decision"}, headers=HEADERS)
        single_id = r1.json()["id"]
        # Batch save
        r2 = client.post("/save/batch", json={
            "memories": [{"content": content + " — batch copy", "category": "decision"}]
        }, headers=HEADERS)
        batch_id = r2.json()["saved"][0]["id"]

        with get_connection() as conn:
            cols = "title, content_hash, memory_type, facts_json, concepts_json, narrative, metadata_json"
            single = dict(conn.execute(f"SELECT {cols} FROM memories WHERE id = ?", (single_id,)).fetchone())
            batch = dict(conn.execute(f"SELECT {cols} FROM memories WHERE id = ?", (batch_id,)).fetchone())

        # Both must have title and content_hash populated
        assert single["title"] is not None, "single save missing title"
        assert batch["title"] is not None, "batch save missing title"
        assert single["content_hash"] is not None, "single save missing content_hash"
        assert batch["content_hash"] is not None, "batch save missing content_hash"
        # Both must have same memory_type inference
        assert single["memory_type"] == batch["memory_type"]


# ── P3: Tag system ───────────────────────────────────────────────────────────

class TestTags:
    def _create_memory(self) -> int:
        r = client.post("/save", json={"content": "Memory for tag testing purposes", "category": "general"}, headers=HEADERS)
        return r.json()["id"]

    def test_add_tags(self):
        """Aggiunge tag a una memoria."""
        mid = self._create_memory()
        r = client.post(f"/memories/{mid}/tags", json={"tags": ["python", "test"]}, headers=HEADERS)
        assert r.status_code == 201
        data = r.json()
        assert data["count"] == 2
        assert "python" in data["tags"]
        assert "test" in data["tags"]

    def test_get_tags(self):
        """Legge i tag di una memoria."""
        mid = self._create_memory()
        client.post(f"/memories/{mid}/tags", json={"tags": ["alpha", "beta"]}, headers=HEADERS)
        r = client.get(f"/memories/{mid}/tags", headers=HEADERS)
        assert r.status_code == 200
        assert "alpha" in r.json()["tags"]
        assert "beta" in r.json()["tags"]

    def test_remove_tags(self):
        """Rimuove un tag specifico."""
        mid = self._create_memory()
        client.post(f"/memories/{mid}/tags", json={"tags": ["keep", "remove"]}, headers=HEADERS)
        r = client.request("DELETE", f"/memories/{mid}/tags", json={"tags": ["remove"]}, headers=HEADERS)
        assert r.status_code == 200
        assert "keep" in r.json()["tags"]
        assert "remove" not in r.json()["tags"]

    def test_search_by_tag(self):
        """Cerca memorie per tag."""
        mid = self._create_memory()
        client.post(f"/memories/{mid}/tags", json={"tags": ["unique-tag-xyz"]}, headers=HEADERS)
        r = client.get("/tags/unique-tag-xyz/memories", headers=HEADERS)
        assert r.status_code == 200
        assert r.json()["total"] >= 1
        ids = [m["id"] for m in r.json()["results"]]
        assert mid in ids

    def test_tags_normalized_lowercase(self):
        """I tag vengono normalizzati in lowercase."""
        mid = self._create_memory()
        client.post(f"/memories/{mid}/tags", json={"tags": ["UPPER", "MiXeD"]}, headers=HEADERS)
        r = client.get(f"/memories/{mid}/tags", headers=HEADERS)
        tags = r.json()["tags"]
        assert "upper" in tags
        assert "mixed" in tags

    def test_tags_scoped_to_agent(self):
        """Un altro agente non può aggiungere tag a memorie altrui."""
        mid = self._create_memory()
        r = client.post(f"/memories/{mid}/tags", json={"tags": ["intruder"]}, headers=OTHER_AGENT)
        assert r.status_code == 201
        assert r.json()["count"] == 0  # nessun tag aggiunto — memoria non appartiene all'agente

    def test_duplicate_tags_ignored(self):
        """Tag duplicati vengono ignorati (INSERT OR IGNORE)."""
        mid = self._create_memory()
        client.post(f"/memories/{mid}/tags", json={"tags": ["dup"]}, headers=HEADERS)
        client.post(f"/memories/{mid}/tags", json={"tags": ["dup"]}, headers=HEADERS)
        r = client.get(f"/memories/{mid}/tags", headers=HEADERS)
        assert r.json()["tags"].count("dup") == 1


# ── P3: Relazioni ────────────────────────────────────────────────────────────

class TestRelations:
    def _create_two_memories(self) -> tuple[int, int]:
        r1 = client.post("/save", json={"content": "Source memory for relation test", "category": "general"}, headers=HEADERS)
        r2 = client.post("/save", json={"content": "Target memory for relation test", "category": "general"}, headers=HEADERS)
        return r1.json()["id"], r2.json()["id"]

    def test_add_relation(self):
        """Crea una relazione tra due memorie."""
        src, tgt = self._create_two_memories()
        r = client.post(f"/memories/{src}/relations", json={"target_id": tgt, "relation": "depends_on"}, headers=HEADERS)
        assert r.status_code == 201
        data = r.json()
        assert data["total"] >= 1

    def test_get_relations(self):
        """Legge le relazioni di una memoria."""
        src, tgt = self._create_two_memories()
        client.post(f"/memories/{src}/relations", json={"target_id": tgt, "relation": "related"}, headers=HEADERS)
        r = client.get(f"/memories/{src}/relations", headers=HEADERS)
        assert r.status_code == 200
        assert r.json()["total"] >= 1
        # Verifica che la relazione contenga i dati attesi
        rel = r.json()["relations"][0]
        assert "relation" in rel
        assert "related_content" in rel

    def test_relation_bidirectional_visibility(self):
        """La relazione è visibile da entrambi i lati."""
        src, tgt = self._create_two_memories()
        client.post(f"/memories/{src}/relations", json={"target_id": tgt}, headers=HEADERS)
        # Visibile dal target
        r = client.get(f"/memories/{tgt}/relations", headers=HEADERS)
        assert r.json()["total"] >= 1

    def test_relation_cross_agent_rejected(self):
        """Non si può creare relazione con memoria di un altro agente."""
        r1 = client.post("/save", json={"content": "Agent A memory for cross relation", "category": "general"}, headers=HEADERS)
        r2 = client.post("/save", json={"content": "Agent B memory for cross relation", "category": "general"}, headers=OTHER_AGENT)
        src = r1.json()["id"]
        tgt = r2.json()["id"]
        # L'agente A prova a collegare la sua memoria a quella dell'agente B
        r = client.post(f"/memories/{src}/relations", json={"target_id": tgt}, headers=HEADERS)
        assert r.status_code == 201
        # La relazione non viene creata (count < 2 nella verifica)
        assert r.json()["total"] == 0

    def test_relation_default_type(self):
        """Il tipo di relazione predefinito è 'related'."""
        src, tgt = self._create_two_memories()
        client.post(f"/memories/{src}/relations", json={"target_id": tgt}, headers=HEADERS)
        r = client.get(f"/memories/{src}/relations", headers=HEADERS)
        assert r.json()["relations"][0]["relation"] == "related"


# ── P3: TTL / Cleanup ────────────────────────────────────────────────────────

class TestTTL:
    def setup_method(self):
        """Resetta rate limiter per evitare 429 nei test TTL."""
        from kore_memory.main import _rate_buckets
        _rate_buckets.clear()

    def test_save_with_ttl(self):
        """Salva una memoria con TTL — deve essere accettata."""
        r = client.post("/save", json={
            "content": "Expiring memory with TTL",
            "category": "general",
            "ttl_hours": 24,
        }, headers=HEADERS)
        assert r.status_code == 201
        assert "id" in r.json()

    def test_save_without_ttl(self):
        """Salva senza TTL — nessun expires_at impostato."""
        r = client.post("/save", json={
            "content": "Permanent memory without TTL",
            "category": "general",
        }, headers=HEADERS)
        assert r.status_code == 201

    def test_ttl_validation_min(self):
        """TTL < 1 ora — rifiutato."""
        r = client.post("/save", json={
            "content": "Invalid TTL memory",
            "category": "general",
            "ttl_hours": 0,
        }, headers=HEADERS)
        assert r.status_code == 422

    def test_ttl_validation_max(self):
        """TTL > 8760 ore (1 anno) — rifiutato."""
        r = client.post("/save", json={
            "content": "Invalid TTL memory too long",
            "category": "general",
            "ttl_hours": 9999,
        }, headers=HEADERS)
        assert r.status_code == 422

    def test_cleanup_endpoint(self):
        """L'endpoint /cleanup risponde correttamente."""
        r = client.post("/cleanup", headers=HEADERS)
        assert r.status_code == 200
        data = r.json()
        assert "removed" in data
        assert data["removed"] >= 0

    def test_expired_memory_not_in_search(self):
        """Memorie con expires_at nel passato non appaiono nella ricerca."""
        from kore_memory.database import get_connection

        # Inserisci direttamente una memoria già scaduta
        with get_connection() as conn:
            conn.execute(
                """INSERT INTO memories (agent_id, content, category, importance, expires_at)
                   VALUES (?, ?, ?, ?, datetime('now', '-1 hour'))""",
                ("test-agent", "Expired secret data for TTL test", "general", 3),
            )

        r = client.get("/search?q=Expired+secret+data+for+TTL&semantic=false", headers=HEADERS)
        # La memoria scaduta non deve apparire
        for result in r.json()["results"]:
            assert "Expired secret data for TTL test" not in result["content"]

    def test_cleanup_removes_expired(self):
        """Il cleanup rimuove effettivamente le memorie scadute."""
        from kore_memory.database import get_connection

        # Inserisci memoria già scaduta
        with get_connection() as conn:
            conn.execute(
                """INSERT INTO memories (agent_id, content, category, importance, expires_at)
                   VALUES (?, ?, ?, ?, datetime('now', '-2 hours'))""",
                ("test-agent", "Cleanup target memory", "general", 1),
            )

        r = client.post("/cleanup", headers=HEADERS)
        assert r.status_code == 200
        assert r.json()["removed"] >= 1

    def test_non_expired_memory_survives_cleanup(self):
        """Memorie con TTL futuro sopravvivono al cleanup."""
        r = client.post("/save", json={
            "content": "Future TTL memory should survive cleanup",
            "category": "general",
            "ttl_hours": 8760,  # 1 anno
        }, headers=HEADERS)
        mid = r.json()["id"]

        client.post("/cleanup", headers=HEADERS)

        # La memoria deve ancora esistere — verifico con search
        sr = client.get("/search?q=Future+TTL+memory+should+survive&semantic=false", headers=HEADERS)
        ids = [m["id"] for m in sr.json()["results"]]
        assert mid in ids


# ── P3/P2: Export / Import ────────────────────────────────────────────────────

class TestExportImport:
    def test_export_returns_memories(self):
        """L'export restituisce le memorie dell'agente."""
        # Assicura che ci sia almeno una memoria
        client.post("/save", json={"content": "Export test memory data", "category": "project"}, headers=HEADERS)
        r = client.get("/export", headers=HEADERS)
        assert r.status_code == 200
        data = r.json()
        assert data["total"] >= 1
        assert len(data["memories"]) == data["total"]

    def test_export_scoped_to_agent(self):
        """L'export non include memorie di altri agenti."""
        agent_c = {"X-Agent-Id": "export-agent-c"}
        client.post("/save", json={"content": "Agent C exclusive export data", "category": "general"}, headers=agent_c)

        r = client.get("/export", headers=OTHER_AGENT)
        for mem in r.json()["memories"]:
            assert "Agent C exclusive" not in mem["content"]

    def test_import_memories(self):
        """L'import salva le memorie correttamente."""
        payload = {
            "memories": [
                {"content": "Imported memory alpha", "category": "general", "importance": 2},
                {"content": "Imported memory beta", "category": "project", "importance": 4},
            ]
        }
        r = client.post("/import", json=payload, headers=HEADERS)
        assert r.status_code == 201
        assert r.json()["imported"] == 2

    def test_import_skips_invalid(self):
        """L'import salta record con contenuto troppo corto."""
        payload = {
            "memories": [
                {"content": "ab", "category": "general"},  # troppo corto — saltato
                {"content": "Valid imported memory content", "category": "general"},
            ]
        }
        r = client.post("/import", json=payload, headers=HEADERS)
        assert r.status_code == 201
        assert r.json()["imported"] == 1

    def test_export_excludes_expired(self):
        """L'export non include memorie con TTL scaduto."""
        from kore_memory.database import get_connection

        with get_connection() as conn:
            conn.execute(
                """INSERT INTO memories (agent_id, content, category, importance, expires_at)
                   VALUES (?, ?, ?, ?, datetime('now', '-1 hour'))""",
                ("test-agent", "Expired export exclusion test", "general", 1),
            )

        r = client.get("/export", headers=HEADERS)
        for mem in r.json()["memories"]:
            assert "Expired export exclusion test" not in mem["content"]


# ── P2: Pagination ────────────────────────────────────────────────────────────

class TestPagination:
    def setup_method(self):
        """Crea abbastanza memorie per testare la paginazione."""
        from kore_memory.main import _rate_buckets
        _rate_buckets.clear()
        for i in range(6):
            client.post("/save", json={
                "content": f"Pagination test item number {i} with unique marker PGNX",
                "category": "general",
                "importance": 3,
            }, headers=HEADERS)

    def test_search_pagination_offset(self):
        """La ricerca con offset salta i primi risultati."""
        r_full = client.get("/search?q=PGNX&limit=10&semantic=false", headers=HEADERS)
        r_offset = client.get("/search?q=PGNX&limit=3&offset=2&semantic=false", headers=HEADERS)
        assert r_offset.status_code == 200
        data = r_offset.json()
        assert data["offset"] == 2
        assert len(data["results"]) <= 3

    def test_search_has_more_flag(self):
        """has_more è True quando ci sono più risultati oltre la pagina."""
        r = client.get("/search?q=PGNX&limit=2&offset=0&semantic=false", headers=HEADERS)
        data = r.json()
        if data["total"] > 2:
            assert data["has_more"] is True

    def test_timeline_pagination(self):
        """La timeline supporta offset."""
        r = client.get("/timeline?subject=PGNX&limit=2&offset=1", headers=HEADERS)
        assert r.status_code == 200
        assert r.json()["offset"] == 1


# ── Archive (soft-delete) ─────────────────────────────────────────────────────

class TestArchive:
    """Verifica il workflow di archiviazione e ripristino memorie."""

    def _save(self, content: str = "Memoria da archiviare per test archivio") -> int:
        """Helper: salva una memoria e restituisce l'id."""
        r = client.post("/save", json={"content": content, "category": "general", "importance": 3}, headers=HEADERS)
        assert r.status_code == 201
        return r.json()["id"]

    def test_archive_memory(self):
        """Archivia una memoria esistente — risposta 200 con success=True."""
        mid = self._save()
        r = client.post(f"/memories/{mid}/archive", headers=HEADERS)
        assert r.status_code == 200
        data = r.json()
        assert data["success"] is True

    def test_archive_not_found(self):
        """Tentativo di archiviare una memoria inesistente — deve rispondere 404."""
        r = client.post("/memories/999999999/archive", headers=HEADERS)
        assert r.status_code == 404

    def test_restore_memory(self):
        """Archivia poi ripristina una memoria — risposta 200 con success=True."""
        mid = self._save("Memoria archiviata e poi ripristinata nel test")
        # Archivia
        arch = client.post(f"/memories/{mid}/archive", headers=HEADERS)
        assert arch.status_code == 200
        # Ripristina
        rest = client.post(f"/memories/{mid}/restore", headers=HEADERS)
        assert rest.status_code == 200
        assert rest.json()["success"] is True

    def test_restore_not_archived(self):
        """Tentativo di ripristinare una memoria non archiviata — deve rispondere 404."""
        mid = self._save("Memoria attiva non archiviata da ripristinare")
        # La memoria non è archiviata, il restore deve fallire
        r = client.post(f"/memories/{mid}/restore", headers=HEADERS)
        assert r.status_code == 404

    def test_archived_not_in_search(self):
        """Una memoria archiviata non deve apparire nei risultati di ricerca."""
        contenuto = "Contenuto univoco archiviato ARCHTEST99"
        mid = self._save(contenuto)
        # Verifica che sia trovabile prima dell'archivio
        r_before = client.get("/search?q=ARCHTEST99&semantic=false", headers=HEADERS)
        ids_before = [m["id"] for m in r_before.json()["results"]]
        assert mid in ids_before
        # Archivia
        client.post(f"/memories/{mid}/archive", headers=HEADERS)
        # Dopo l'archivio non deve più comparire nella ricerca
        r_after = client.get("/search?q=ARCHTEST99&semantic=false", headers=HEADERS)
        ids_after = [m["id"] for m in r_after.json()["results"]]
        assert mid not in ids_after

    def test_archive_list(self):
        """Una memoria archiviata deve comparire in GET /archive."""
        mid = self._save("Memoria da listare nell archivio ARCHLIST01")
        client.post(f"/memories/{mid}/archive", headers=HEADERS)
        r = client.get("/archive", headers=HEADERS)
        assert r.status_code == 200
        ids = [m["id"] for m in r.json()["results"]]
        assert mid in ids


# ── Cursor-based pagination ───────────────────────────────────────────────────

class TestCursorPagination:
    """Verifica la paginazione basata su cursore opaco (base64)."""

    # Marcatore univoco per isolare le memorie di questa classe
    _MARKER = "CURSORPAGTEST"

    def setup_method(self):
        """Crea 5 memorie con marcatore univoco prima di ogni test."""
        from kore_memory.main import _rate_buckets
        _rate_buckets.clear()
        for i in range(5):
            client.post("/save", json={
                "content": f"Memoria per cursor pagination numero {i} marker {self._MARKER}",
                "category": "general",
                "importance": 3,
            }, headers=HEADERS)

    def test_cursor_pagination_search(self):
        """Ricerca con limit=2 poi usa il cursore — la seconda pagina deve avere risultati diversi."""
        # Prima pagina
        r1 = client.get(f"/search?q={self._MARKER}&limit=2&semantic=false", headers=HEADERS)
        assert r1.status_code == 200
        data1 = r1.json()
        assert len(data1["results"]) == 2
        cursor = data1.get("cursor")
        # Se ci sono più risultati il cursore non deve essere None
        assert cursor is not None

        # Seconda pagina usando il cursore
        r2 = client.get(f"/search?q={self._MARKER}&limit=2&cursor={cursor}&semantic=false", headers=HEADERS)
        assert r2.status_code == 200
        data2 = r2.json()
        # Gli id della seconda pagina devono essere diversi da quelli della prima
        ids1 = {m["id"] for m in data1["results"]}
        ids2 = {m["id"] for m in data2["results"]}
        assert ids1.isdisjoint(ids2), "Le pagine non devono contenere le stesse memorie"

    def test_invalid_cursor(self):
        """Un cursore non valido (stringa arbitraria non base64 decodificabile) deve rispondere 400."""
        r = client.get("/search?q=test&cursor=NON_VALIDO_!!!&semantic=false", headers=HEADERS)
        assert r.status_code == 400

    def test_has_more_flag(self):
        """Con 5 memorie e limit=2 has_more deve essere True; sull'ultima pagina False."""
        # Prima pagina — deve avere has_more=True
        r1 = client.get(f"/search?q={self._MARKER}&limit=2&semantic=false", headers=HEADERS)
        assert r1.status_code == 200
        assert r1.json()["has_more"] is True

        # Scorri fino all'esaurimento dei risultati
        cursor = r1.json().get("cursor")
        last_has_more = True
        iterations = 0
        while cursor and iterations < 10:
            rn = client.get(f"/search?q={self._MARKER}&limit=2&cursor={cursor}&semantic=false", headers=HEADERS)
            assert rn.status_code == 200
            last_has_more = rn.json()["has_more"]
            cursor = rn.json().get("cursor")
            iterations += 1

        # L'ultima pagina deve avere has_more=False
        assert last_has_more is False


# ── Rate limiting ─────────────────────────────────────────────────────────────

class TestRateLimit:
    """Verifica che il rate limiter risponda 429 quando il limite viene superato."""

    def setup_method(self):
        """Resetta i bucket per garantire uno stato pulito prima di ogni test."""
        from kore_memory.main import _rate_buckets
        _rate_buckets.clear()

    def test_rate_limit_exceeded(self):
        """Supera il limite di /decay/run (5 req/ora) — la sesta deve restituire 429."""
        # Il limite configurato è 5 richieste per ora
        for _ in range(5):
            r = client.post("/decay/run", headers=HEADERS)
            assert r.status_code == 200
        # La sesta richiesta deve essere rifiutata
        r_exceeded = client.post("/decay/run", headers=HEADERS)
        assert r_exceeded.status_code == 429

    def test_rate_limit_different_paths(self):
        """Path diversi hanno bucket di rate limit indipendenti."""
        from kore_memory.main import _rate_buckets
        _rate_buckets.clear()

        # Esaurisci il limite di /decay/run (5 req/ora)
        for _ in range(5):
            client.post("/decay/run", headers=HEADERS)

        # /cleanup ha un bucket separato (10 req/ora) — la prima chiamata deve riuscire
        # anche se /decay/run è esaurito, perché i bucket sono per-path
        r_cleanup = client.post("/cleanup", headers=HEADERS)
        assert r_cleanup.status_code == 200, (
            "Il rate limit di /cleanup deve essere indipendente da /decay/run"
        )


# ── Update memory — correttezza del campo importance ─────────────────────────

class TestUpdateMemory:
    """Verifica che PUT /memories/{id} restituisca il valore di importance reale dal DB."""

    def _save_with_importance(self, importance: int) -> int:
        """Helper: salva una memoria con importance esplicita e restituisce l'id."""
        r = client.post("/save", json={
            "content": "Memoria per test aggiornamento importance valore",
            "category": "general",
            "importance": importance,
        }, headers=HEADERS)
        assert r.status_code == 201
        return r.json()["id"]

    def test_update_returns_real_importance(self):
        """Aggiorna solo il contenuto — la risposta deve contenere importance=3 (non 0)."""
        mid = self._save_with_importance(3)
        r = client.put(f"/memories/{mid}", json={
            "content": "Contenuto aggiornato senza modificare importance",
        }, headers=HEADERS)
        assert r.status_code == 200
        data = r.json()
        # Il campo importance deve riflettere il valore salvato nel DB, non 0
        assert data["importance"] == 3, (
            f"importance atteso=3, ottenuto={data['importance']}. "
            "L'endpoint non deve restituire 0 quando importance non è nel payload."
        )

    def test_update_importance_explicit(self):
        """Aggiorna importance a 5 — la risposta deve confermare importance=5."""
        mid = self._save_with_importance(2)
        r = client.put(f"/memories/{mid}", json={
            "importance": 5,
        }, headers=HEADERS)
        assert r.status_code == 200
        assert r.json()["importance"] == 5


# ── Auto-scoring importance ───────────────────────────────────────────────────

class TestAutoScore:
    """Verifica che importance=None attivi l'auto-scoring, mentre importance=1 esplicito venga rispettato."""

    def test_save_without_importance_auto_scores(self):
        """Senza campo importance il server deve assegnare un punteggio >= 1 automaticamente."""
        # Contenuto neutro nella categoria "general" — baseline 1, auto-score atteso >= 1
        r = client.post("/save", json={
            "content": "Informazione generica senza importanza esplicita per test auto score",
            "category": "general",
        }, headers=HEADERS)
        assert r.status_code == 201
        data = r.json()
        assert "importance" in data
        # L'auto-scorer deve sempre restituire un valore valido nell'intervallo 1–5
        assert 1 <= data["importance"] <= 5

    def test_save_with_importance_1_explicit(self):
        """Con importance=1 esplicito il server deve conservare 1, senza sovrascrivere con auto-score."""
        r = client.post("/save", json={
            # Contenuto che senza vincolo di importance potrebbe ricevere un punteggio più alto
            "content": "Decisione importante urgente priorità massima progetto critico",
            "category": "decision",
            "importance": 1,
        }, headers=HEADERS)
        assert r.status_code == 201
        data = r.json()
        # importance=1 esplicito deve essere rispettato: l'auto-scorer non deve intervenire
        assert data["importance"] == 1, (
            f"importance atteso=1 (esplicito), ottenuto={data['importance']}. "
            "Un importance esplicito non deve essere sovrascritto dall'auto-scorer."
        )
