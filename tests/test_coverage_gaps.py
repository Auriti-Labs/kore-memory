"""
Test mirati per coprire branch non raggiunti dagli altri file di test.
Focus su: repository/memory.py, repository/search.py, mcp_server.py
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

# Salta i test MCP se il pacchetto 'mcp' non è installato (dipendenza opzionale)
try:
    import mcp as _mcp_mod  # noqa: F401

    _HAS_MCP = True
except ImportError:
    _HAS_MCP = False

HEADERS = {"X-Agent-Id": "cov-agent"}


@pytest.fixture
def client():
    from kore_memory.main import app

    return TestClient(app)


# ── GET /agents ─────────────────────────────────────────────────────────────

class TestListAgents:
    def test_get_agents_returns_list(self, client):
        """GET /agents restituisce lista di agenti."""
        # Prima inserisce una memoria per avere almeno un agente
        client.post("/save", json={"content": "memoria agente cov"}, headers=HEADERS)
        r = client.get("/agents", headers=HEADERS)
        assert r.status_code == 200
        data = r.json()
        assert "agents" in data
        assert isinstance(data["agents"], list)

    def test_get_agents_includes_current_agent(self, client):
        """L'agente corrente compare nella lista dopo aver salvato."""
        client.post("/save", json={"content": "test agents list coverage"}, headers=HEADERS)
        r = client.get("/agents", headers=HEADERS)
        assert r.status_code == 200
        agent_ids = [a["agent_id"] for a in r.json()["agents"]]
        assert "cov-agent" in agent_ids

    def test_get_agents_includes_memory_count(self, client):
        """Ogni agente ha memory_count e last_active."""
        client.post("/save", json={"content": "conta memorie agente"}, headers=HEADERS)
        r = client.get("/agents", headers=HEADERS)
        for agent in r.json()["agents"]:
            assert "memory_count" in agent
            assert "last_active" in agent


# ── GET /health (stats via health) ─────────────────────────────────────────

class TestHealthStats:
    def test_health_returns_stats(self, client):
        """GET /health include statistiche base."""
        r = client.get("/health")
        assert r.status_code == 200
        data = r.json()
        assert "status" in data
        assert data["status"] == "ok"

    def test_health_includes_version(self, client):
        """GET /health include la versione corrente."""
        r = client.get("/health")
        assert "version" in r.json()


# ── repository/memory.py: branch update_memory senza contenuto ──────────────

class TestUpdateMemoryBranches:
    def test_update_memory_only_category(self, client):
        """update_memory con solo category (senza content) → nessuna rigenerazione embedding."""
        r = client.post("/save", json={"content": "memoria da aggiornare categoria"}, headers=HEADERS)
        mem_id = r.json()["id"]

        # Aggiorna solo la categoria, senza content
        r2 = client.put(
            f"/memories/{mem_id}",
            json={"category": "runbook"},
            headers=HEADERS,
        )
        assert r2.status_code == 200

    def test_update_memory_no_fields_returns_true(self, client):
        """update_memory senza campi aggiorna updated_at ma non rompe nulla."""
        r = client.post("/save", json={"content": "memoria aggiornamento vuoto"}, headers=HEADERS)
        mem_id = r.json()["id"]

        # Patch senza campi significativi
        r2 = client.put(f"/memories/{mem_id}", json={}, headers=HEADERS)
        # 422 se validazione fallisce, 200 se update vuoto gestito
        assert r2.status_code in (200, 422)

    def test_update_nonexistent_memory_returns_false(self, client):
        """update_memory su ID inesistente → 404."""
        r = client.put(
            "/memories/999999",
            json={"content": "non esiste"},
            headers=HEADERS,
        )
        assert r.status_code == 404


# ── repository/memory.py: get_stats senza agent_id ──────────────────────────

class TestGetStatsNoAgent:
    def test_get_stats_global(self, client):
        """GET /health restituisce total_memories senza filtro agent_id."""
        # Inserisce memorie da due agenti diversi
        client.post("/save", json={"content": "stats agente A"}, headers={"X-Agent-Id": "stats-a"})
        client.post("/save", json={"content": "stats agente B"}, headers={"X-Agent-Id": "stats-b"})
        r = client.get("/health")
        assert r.status_code == 200


# ── repository/search.py: branch search senza risultati ─────────────────────

class TestSearchBranches:
    def test_search_wildcard_q_star(self, client):
        """Ricerca q=* → branch LIKE wildcard in _count_active_memories."""
        # Inserisce memoria per avere risultati
        client.post("/save", json={"content": "memoria per wildcard star"}, headers=HEADERS)
        r = client.get(
            "/search",
            params={"q": "*", "limit": 10, "semantic": "false"},
            headers=HEADERS,
        )
        assert r.status_code == 200
        data = r.json()
        assert "results" in data
        assert data["total"] >= 1

    def test_search_empty_result(self, client):
        """Ricerca FTS su termine rarissimo → lista vuota, no crash."""
        r = client.get(
            "/search",
            params={"q": "xyzzy_nonexistent_term_99999", "semantic": "false"},
            headers=HEADERS,
        )
        assert r.status_code == 200
        assert r.json()["results"] == []

    def test_search_tag_empty_result(self, client):
        """GET /tags/<tag>/memories su tag inesistente → lista vuota."""
        r = client.get("/tags/NONEXISTENT-TAG-XYZ/memories", headers=HEADERS)
        assert r.status_code == 200
        assert r.json()["results"] == []

    def test_search_with_cursor_pagination(self, client):
        """Ricerca con cursor non nullo copre il branch cursor in _fts_search."""
        # Prima salva più memorie per avere un cursore
        for i in range(3):
            client.post(
                "/save",
                json={"content": f"memoria paginazione cursore test {i}"},
                headers=HEADERS,
            )
        # Prima pagina senza cursor
        r1 = client.get(
            "/search",
            params={"q": "memoria paginazione cursore", "limit": 2},
            headers=HEADERS,
        )
        assert r1.status_code == 200
        data = r1.json()
        # Se c'è un next_cursor, usa la seconda pagina
        if data.get("next_cursor"):
            r2 = client.get(
                "/search",
                params={
                    "q": "memoria paginazione cursore",
                    "limit": 2,
                    "cursor": data["next_cursor"],
                },
                headers=HEADERS,
            )
            assert r2.status_code == 200


# ── mcp_server.py: _get_or_create_session double-check locking ───────────────

@pytest.mark.skipif(not _HAS_MCP, reason="mcp package not installed (optional dependency)")
class TestMcpSessionDoubleLocking:
    def test_double_check_locking_same_agent(self):
        """_get_or_create_session: seconda chiamata stesso agente → stesso session_id."""
        from kore_memory.mcp_server import _agent_sessions, _get_or_create_session

        # Pulisce stato per agente test
        _agent_sessions.pop("cov-test-locking", None)

        sid1 = _get_or_create_session("cov-test-locking")
        sid2 = _get_or_create_session("cov-test-locking")
        assert sid1 == sid2

        # Cleanup
        _agent_sessions.pop("cov-test-locking", None)

    def test_close_all_sessions_does_not_raise(self):
        """_close_all_sessions non solleva eccezioni anche con sessioni esistenti."""
        from kore_memory.mcp_server import _agent_sessions, _close_all_sessions, memory_save

        # Crea una sessione reale
        memory_save(content="test close sessions", agent_id="close-test-agent")
        assert "close-test-agent" in _agent_sessions

        # Deve completare senza errori
        _close_all_sessions()
        # Cleanup
        _agent_sessions.pop("close-test-agent", None)


# ── mcp_server.py: memory_save_decision con repo ────────────────────────────

@pytest.mark.skipif(not _HAS_MCP, reason="mcp package not installed (optional dependency)")
class TestMcpDecisionRepo:
    def test_save_decision_with_conflicts_detected_key(self):
        """memory_save_decision restituisce conflicts_detected."""
        from kore_memory.mcp_server import memory_save_decision

        result = memory_save_decision(
            content="Usiamo FastAPI per le API REST",
            rationale="Performance e async native",
            alternatives_considered="Flask, Django, Starlette",
            agent_id="cov-agent",
        )
        assert "conflicts_detected" in result
        assert isinstance(result["conflicts_detected"], list)

    def test_save_decision_empty_optional_fields(self):
        """memory_save_decision con soli campi obbligatori."""
        from kore_memory.mcp_server import memory_save_decision

        result = memory_save_decision(
            content="Usiamo SQLite come database principale",
            agent_id="cov-agent",
        )
        assert result["id"] > 0
        assert result["category"] == "architectural_decision"
