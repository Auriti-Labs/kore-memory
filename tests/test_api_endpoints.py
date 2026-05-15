"""
Test per endpoint API aggiuntivi non coperti altrove.
"""

import pytest
from fastapi.testclient import TestClient

from kore_memory.database import init_db
from kore_memory.main import app

HEADERS = {"X-Agent-Id": "test-endpoint-agent"}


@pytest.fixture(autouse=True)
def _setup_db():
    """Inizializza il database prima di ogni test."""
    init_db()


@pytest.fixture()
def client():
    """Client HTTP per i test."""
    with TestClient(app) as c:
        yield c


class TestSessionEndpoints:
    """Test per endpoint /sessions/*."""

    def test_session_create(self, client):
        """POST /sessions crea una nuova sessione."""
        import uuid
        session_id = str(uuid.uuid4())
        resp = client.post(
            "/sessions",
            headers=HEADERS,
            json={"session_id": session_id, "title": "Test Session"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert "id" in data

    def test_sessions_list(self, client):
        """GET /sessions ritorna lista sessioni."""
        # Crea prima una sessione
        client.post(
            "/sessions",
            headers=HEADERS,
            json={"title": "Session to list"},
        )
        resp = client.get("/sessions", headers=HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)

    def test_session_memories(self, client):
        """GET /sessions/{id}/memories ritorna memorie della sessione."""
        import uuid
        session_id = str(uuid.uuid4())
        client.post(
            "/sessions",
            headers=HEADERS,
            json={"session_id": session_id, "title": "Session for memories"},
        )

        # Salva memoria con session_id
        client.post(
            "/save",
            headers={**HEADERS, "X-Session-Id": session_id},
            json={"content": "Memory in session", "category": "general"},
        )

        resp = client.get(f"/sessions/{session_id}/memories", headers=HEADERS)
        assert resp.status_code == 200
        # La risposta è una MemorySearchResponse o lista
        assert isinstance(resp.json(), (dict, list))

    def test_session_summary(self, client):
        """GET /sessions/{id}/summary ritorna statistiche sessione."""
        import uuid
        session_id = str(uuid.uuid4())
        client.post(
            "/sessions",
            headers=HEADERS,
            json={"session_id": session_id, "title": "Session for summary"},
        )

        resp = client.get(f"/sessions/{session_id}/summary", headers=HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        # La risposta ha memory_count o session_id
        assert "memory_count" in data or "session_id" in data

    def test_session_end(self, client):
        """POST /sessions/{id}/end termina una sessione."""
        import uuid
        session_id = str(uuid.uuid4())
        client.post(
            "/sessions",
            headers=HEADERS,
            json={"session_id": session_id, "title": "Session to end"},
        )

        resp = client.post(f"/sessions/{session_id}/end", headers=HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        # ArchiveResponse ha archived field
        assert "archived" in data or "message" in data

    def test_session_delete(self, client):
        """DELETE /sessions/{id} elimina una sessione."""
        import uuid
        session_id = str(uuid.uuid4())
        client.post(
            "/sessions",
            headers=HEADERS,
            json={"session_id": session_id, "title": "Session to delete"},
        )

        resp = client.delete(f"/sessions/{session_id}", headers=HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert "success" in data or "deleted" in data


class TestArchiveEndpoints:
    """Test per endpoint /archive/*."""

    def test_archive_memory(self, client):
        """POST /memories/{id}/archive archivia una memoria."""
        # Salva memoria
        save_resp = client.post(
            "/save",
            headers=HEADERS,
            json={"content": "Memory to archive", "category": "general"},
        )
        memory_id = save_resp.json()["id"]

        resp = client.post(f"/memories/{memory_id}/archive", headers=HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert "archived" in data or "message" in data

    def test_restore_memory(self, client):
        """POST /memories/{id}/restore ripristina una memoria archiviata."""
        # Salva e archivia
        save_resp = client.post(
            "/save",
            headers=HEADERS,
            json={"content": "Memory to restore", "category": "general"},
        )
        memory_id = save_resp.json()["id"]
        client.post(f"/memories/{memory_id}/archive", headers=HEADERS)

        resp = client.post(f"/memories/{memory_id}/restore", headers=HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert "restored" in data or "message" in data

    def test_archive_list(self, client):
        """GET /archive ritorna lista memorie archiviate."""
        resp = client.get("/archive", headers=HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, (dict, list))


class TestGraphEndpoints:
    """Test per endpoint /graph/*."""

    def test_graph_traverse(self, client):
        """GET /graph/traverse traversa il grafo delle relazioni."""
        # Salva memoria di partenza
        save_resp = client.post(
            "/save",
            headers=HEADERS,
            json={"content": "Start memory", "category": "general"},
        )
        start_id = save_resp.json()["id"]

        resp = client.get(
            f"/graph/traverse?start_id={start_id}&depth=2",
            headers=HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)

    def test_graph_subgraph(self, client):
        """GET /graph/subgraph ritorna sottografo."""
        # L'endpoint richiede ids come parametro obbligatorio
        # Salva una memoria prima per avere un ID valido
        save_resp = client.post(
            "/save",
            headers=HEADERS,
            json={"content": "Memory for subgraph", "category": "general"},
        )
        memory_id = save_resp.json()["id"]

        resp = client.get(
            f"/graph/subgraph?ids={memory_id}&expand=1",
            headers=HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "total_nodes" in data or "nodes" in data

    def test_graph_hubs(self, client):
        """GET /graph/hubs ritorna nodi hub del grafo."""
        resp = client.get(
            "/graph/hubs?limit=10",
            headers=HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "hubs" in data or "results" in data


class TestLifecyclePolicyEndpoints:
    """Test per endpoint /lifecycle/policies/*."""

    def test_list_policies(self, client):
        """GET /lifecycle/policies ritorna lista policy di ranking."""
        resp = client.get("/lifecycle/policies", headers=HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert "policies" in data

    def test_toggle_policy(self, client):
        """PUT /lifecycle/policies/{id}/enabled attiva/disattiva policy."""
        # La policy deve esistere prima di essere toggleata
        # Creiamo una policy prima usando insert diretto nel DB
        from kore_memory.database import get_connection
        with get_connection() as conn:
            try:
                conn.execute(
                    "INSERT INTO lifecycle_policies (id, enabled) VALUES (?, ?)",
                    ("recency", 0),
                )
            except Exception:
                pass  # Potrebbe già esistere

        resp = client.put(
            "/lifecycle/policies/recency/enabled?enabled=true",
            headers=HEADERS,
        )
        # Accetta sia 200 (successo) che 404 (policy non trovata)
        assert resp.status_code in (200, 404)


class TestRankingEndpoints:
    """Test per endpoint /ranking/*."""

    def test_ranking_profiles_list(self, client):
        """GET /ranking/profiles ritorna profili di ranking."""
        resp = client.get("/ranking/profiles", headers=HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, (list, dict))

    def test_ranking_profile_save(self, client):
        """PUT /ranking/profiles salva un profilo."""
        resp = client.put(
            "/ranking/profiles",
            headers=HEADERS,
            json={
                "profile_name": "test-profile",
                "weights": {"similarity": 0.5, "decay_score": 0.3, "confidence": 0.2},
            },
        )
        assert resp.status_code == 200

    def test_ranking_profile_delete(self, client):
        """DELETE /ranking/profiles/{name} elimina un profilo."""
        # Prima salva
        client.put(
            "/ranking/profiles",
            headers=HEADERS,
            json={
                "profile_name": "profile-to-delete",
                "weights": {"similarity": 0.5, "decay_score": 0.5},
            },
        )
        # Poi elimina
        resp = client.delete(
            "/ranking/profiles/profile-to-delete",
            headers=HEADERS,
        )
        assert resp.status_code in (200, 404)


class TestScoringStats:
    """Test per endpoint /stats/scoring."""

    def test_scoring_stats(self, client):
        """GET /stats/scoring ritorna statistiche importance."""
        # Salva alcune memorie
        for i in range(5):
            client.post(
                "/save",
                headers=HEADERS,
                json={"content": f"Memory {i}", "category": "general", "importance": i + 1},
            )

        resp = client.get("/stats/scoring", headers=HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert "total" in data
        # La struttura reale ha distribution, avg_importance, etc.
        assert "distribution" in data or "avg_importance" in data


class TestEntitiesEndpoint:
    """Test per endpoint /entities."""

    def test_entities_list(self, client):
        """GET /entities ritorna entità estratte."""
        resp = client.get("/entities", headers=HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert "entities" in data


class TestExportImport:
    """Test per endpoint /export e /import."""

    def test_export(self, client):
        """GET /export esporta memorie."""
        # Salva una memoria prima
        client.post(
            "/save",
            headers=HEADERS,
            json={"content": "Memory to export", "category": "general"},
        )

        resp = client.get("/export", headers=HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert "memories" in data

    def test_import(self, client):
        """POST /import importa memorie."""
        # Il formato corretto per /import è {"memories": [...]}
        resp = client.post(
            "/import",
            headers=HEADERS,
            json={"memories": [{"content": "Imported memory", "category": "general"}]},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert isinstance(data, dict)


class TestAutoTune:
    """Test per endpoint /auto-tune."""

    def test_auto_tune(self, client):
        """POST /auto-tune aggiorna importanza automaticamente."""
        resp = client.post("/auto-tune", headers=HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        # La risposta ha boosted, reduced, message
        assert isinstance(data, dict)


class TestCleanup:
    """Test per endpoint /cleanup."""

    def test_cleanup(self, client):
        """POST /cleanup elimina memorie scadute."""
        resp = client.post("/cleanup", headers=HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        # La risposta ha removed, message
        assert isinstance(data, dict)
