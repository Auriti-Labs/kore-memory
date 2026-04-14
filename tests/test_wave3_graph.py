"""
Kore — Wave 3 test: Graph tipizzato, Subgraph API, Hub Detection.
Issue #026, #027, #028.
"""

import pytest
from fastapi.testclient import TestClient

from kore_memory.main import app

HEADERS = {"X-Agent-Id": "wave3-test-agent"}
OTHER = {"X-Agent-Id": "wave3-other-agent"}

client = TestClient(app)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _save(content: str, category: str = "project") -> int:
    r = client.post("/save", json={"content": content, "category": category}, headers=HEADERS)
    assert r.status_code == 201
    return r.json()["id"]


def _relate(
    source_id: int,
    target_id: int,
    relation: str = "related",
    strength: float = 1.0,
    confidence: float = 1.0,
) -> dict:
    r = client.post(
        f"/memories/{source_id}/relations",
        json={"target_id": target_id, "relation": relation, "strength": strength, "confidence": confidence},
        headers=HEADERS,
    )
    assert r.status_code == 201, r.text
    return r.json()


# ── #026 — Relazioni tipizzate con peso e confidence ─────────────────────────


class TestTypedRelations:
    def test_relation_default_strength_confidence(self):
        """Relazione creata senza strength/confidence ha valori default 1.0."""
        a = _save("Relazione default strength A")
        b = _save("Relazione default strength B")
        r = client.post(
            f"/memories/{a}/relations",
            json={"target_id": b, "relation": "related"},
            headers=HEADERS,
        )
        assert r.status_code == 201
        rel = r.json()["relations"][0]
        assert rel["strength"] == 1.0
        assert rel["confidence"] == 1.0

    def test_relation_custom_strength_confidence(self):
        """Relazione con strength=0.7 e confidence=0.9 viene salvata correttamente."""
        a = _save("Nodo con strength custom A")
        b = _save("Nodo con strength custom B")
        data = _relate(a, b, relation="supports", strength=0.7, confidence=0.9)
        rel = data["relations"][0]
        assert abs(rel["strength"] - 0.7) < 0.001
        assert abs(rel["confidence"] - 0.9) < 0.001

    def test_relation_strength_clamped(self):
        """Strength e confidence sono clampati a [0.0, 1.0] dal modello Pydantic."""
        a = _save("Clamp test A")
        b = _save("Clamp test B")
        # Pydantic rifiuta valori fuori range
        r = client.post(
            f"/memories/{a}/relations",
            json={"target_id": b, "relation": "related", "strength": 2.0},
            headers=HEADERS,
        )
        assert r.status_code == 422  # validation error

    def test_relation_upsert_updates_strength(self):
        """Creare la stessa relazione due volte aggiorna strength/confidence."""
        a = _save("Upsert test A")
        b = _save("Upsert test B")
        _relate(a, b, relation="depends_on", strength=0.5, confidence=0.6)
        # Aggiorna con nuovi valori
        data = _relate(a, b, relation="depends_on", strength=0.9, confidence=0.8)
        # Deve esserci una sola relazione con i nuovi valori
        deps = [r for r in data["relations"] if r["relation"] == "depends_on"]
        assert len(deps) == 1
        assert abs(deps[0]["strength"] - 0.9) < 0.001
        assert abs(deps[0]["confidence"] - 0.8) < 0.001

    def test_get_relations_includes_strength_confidence(self):
        """GET /memories/{id}/relations include strength e confidence."""
        a = _save("Get relation strength test A")
        b = _save("Get relation strength test B")
        _relate(a, b, relation="contradicts", strength=0.3, confidence=0.95)
        r = client.get(f"/memories/{a}/relations", headers=HEADERS)
        assert r.status_code == 200
        rel = r.json()["relations"][0]
        assert "strength" in rel
        assert "confidence" in rel
        assert abs(rel["strength"] - 0.3) < 0.001

    def test_traverse_edges_include_strength(self):
        """GET /graph/traverse include strength e confidence negli edge."""
        a = _save("Traverse strength A")
        b = _save("Traverse strength B")
        _relate(a, b, relation="causes", strength=0.6, confidence=0.75)
        r = client.get(f"/graph/traverse?start_id={a}&depth=2", headers=HEADERS)
        assert r.status_code == 200
        edges = r.json()["edges"]
        assert len(edges) > 0
        edge = edges[0]
        assert "strength" in edge
        assert "confidence" in edge
        assert abs(edge["strength"] - 0.6) < 0.001

    def test_relation_type_canonical_values(self):
        """Valori canonici di relation type (supports, contradicts, depends_on, ecc.)."""
        a = _save("Canonical relation type A")
        b = _save("Canonical relation type B")
        for rel_type in ["supports", "contradicts", "depends_on", "implements", "references", "derives_from"]:
            c = _save(f"Node for {rel_type}")
            d = _save(f"Target for {rel_type}")
            data = _relate(c, d, relation=rel_type)
            rels = [r for r in data["relations"] if r["relation"] == rel_type]
            assert len(rels) >= 1

    def test_relations_sorted_by_strength_desc(self):
        """Le relazioni sono restituite ordinate per strength DESC."""
        a = _save("Sort by strength A")
        b = _save("Sort by strength B")
        c = _save("Sort by strength C")
        d = _save("Sort by strength D")
        _relate(a, b, relation="supports", strength=0.3)
        _relate(a, c, relation="related", strength=0.9)
        _relate(a, d, relation="contradicts", strength=0.6)
        r = client.get(f"/memories/{a}/relations", headers=HEADERS)
        rels = r.json()["relations"]
        strengths = [rel["strength"] for rel in rels]
        assert strengths == sorted(strengths, reverse=True)


# ── #027 — Graph: Subgraph API ────────────────────────────────────────────────


class TestSubgraph:
    def test_subgraph_basic(self):
        """Estrae il sottografo tra 3 nodi connessi."""
        a = _save("Subgraph node A")
        b = _save("Subgraph node B")
        c = _save("Subgraph node C")
        _relate(a, b, relation="related")
        _relate(b, c, relation="depends_on")
        r = client.get(f"/graph/subgraph?ids={a},{b},{c}", headers=HEADERS)
        assert r.status_code == 200
        data = r.json()
        node_ids = {n["id"] for n in data["nodes"]}
        assert a in node_ids
        assert b in node_ids
        assert c in node_ids
        assert data["total_nodes"] == 3
        assert data["total_edges"] >= 2

    def test_subgraph_single_node(self):
        """Un solo nodo senza relazioni — zero edges."""
        a = _save("Subgraph single node")
        r = client.get(f"/graph/subgraph?ids={a}", headers=HEADERS)
        assert r.status_code == 200
        data = r.json()
        assert data["total_nodes"] == 1
        assert data["total_edges"] == 0

    def test_subgraph_excludes_cross_agent(self):
        """Nodi di altri agenti non vengono inclusi nel sottografo."""
        a = _save("Subgraph cross-agent A")
        r_other = client.post("/save", json={"content": "Other agent node", "category": "general"}, headers=OTHER)
        other_id = r_other.json()["id"]
        r = client.get(f"/graph/subgraph?ids={a},{other_id}", headers=HEADERS)
        assert r.status_code == 200
        data = r.json()
        node_ids = {n["id"] for n in data["nodes"]}
        assert a in node_ids
        assert other_id not in node_ids

    def test_subgraph_expand_depth(self):
        """expand=1 aggiunge i vicini diretti dei nodi seed."""
        a = _save("Subgraph expand seed A")
        b = _save("Subgraph expand neighbor B")
        c = _save("Subgraph expand deep C")
        _relate(a, b, relation="related")
        _relate(b, c, relation="related")
        # Senza expand: solo A
        r0 = client.get(f"/graph/subgraph?ids={a}&expand=0", headers=HEADERS)
        assert r0.json()["total_nodes"] == 1
        # Con expand=1: A + B (vicino diretto)
        r1 = client.get(f"/graph/subgraph?ids={a}&expand=1", headers=HEADERS)
        node_ids1 = {n["id"] for n in r1.json()["nodes"]}
        assert a in node_ids1
        assert b in node_ids1

    def test_subgraph_empty_ids(self):
        """IDs vuoti o invalidi restituiscono subgraph vuoto."""
        r = client.get("/graph/subgraph?ids=abc", headers=HEADERS)
        assert r.status_code == 200
        assert r.json()["total_nodes"] == 0

    def test_subgraph_edges_include_strength(self):
        """Gli archi del subgraph includono strength e confidence."""
        a = _save("Subgraph edge strength A")
        b = _save("Subgraph edge strength B")
        _relate(a, b, relation="supports", strength=0.55, confidence=0.88)
        r = client.get(f"/graph/subgraph?ids={a},{b}", headers=HEADERS)
        assert r.status_code == 200
        edges = r.json()["edges"]
        assert len(edges) == 1
        assert abs(edges[0]["strength"] - 0.55) < 0.001


# ── #028 — Graph: Hub Detection + Degree Centrality ──────────────────────────


class TestHubDetection:
    def test_hubs_basic(self):
        """Il nodo con più connessioni ha degree maggiore."""
        hub = _save("Hub node: highly connected")
        spoke1 = _save("Spoke 1 connected to hub")
        spoke2 = _save("Spoke 2 connected to hub")
        spoke3 = _save("Spoke 3 connected to hub")
        _relate(hub, spoke1)
        _relate(hub, spoke2)
        _relate(hub, spoke3)
        r = client.get("/graph/hubs?limit=10&min_degree=1", headers=HEADERS)
        assert r.status_code == 200
        data = r.json()
        assert data["total"] > 0
        hubs = data["hubs"]
        # L'hub deve essere tra i top (degree >= 3)
        hub_entry = next((h for h in hubs if h["id"] == hub), None)
        assert hub_entry is not None
        assert hub_entry["degree"] >= 3

    def test_hubs_response_fields(self):
        """La risposta include tutti i campi attesi."""
        a = _save("Hub fields test A")
        b = _save("Hub fields test B")
        _relate(a, b)
        r = client.get("/graph/hubs?limit=5", headers=HEADERS)
        assert r.status_code == 200
        hubs = r.json()["hubs"]
        if hubs:
            h = hubs[0]
            for field in ["id", "content", "category", "degree", "in_degree", "out_degree",
                          "avg_strength", "degree_centrality"]:
                assert field in h, f"Campo mancante: {field}"

    def test_hubs_sorted_by_degree(self):
        """I hub sono restituiti in ordine decrescente di degree."""
        r = client.get("/graph/hubs?limit=20", headers=HEADERS)
        assert r.status_code == 200
        degrees = [h["degree"] for h in r.json()["hubs"]]
        assert degrees == sorted(degrees, reverse=True)

    def test_hubs_degree_centrality_normalized(self):
        """degree_centrality è nel range [0.0, 1.0]."""
        r = client.get("/graph/hubs?limit=20", headers=HEADERS)
        for h in r.json()["hubs"]:
            assert 0.0 <= h["degree_centrality"] <= 1.0

    def test_hubs_min_degree_filter(self):
        """min_degree filtra i nodi con grado insufficiente."""
        # Nodo isolato: non deve apparire con min_degree=1
        iso = _save("Isolated hub test node")
        r = client.get(f"/graph/hubs?min_degree=1&limit=100", headers=HEADERS)
        hub_ids = {h["id"] for h in r.json()["hubs"]}
        # Il nodo isolato (degree=0) non deve apparire
        assert iso not in hub_ids

    def test_hubs_in_out_degree_consistent(self):
        """in_degree + out_degree == degree."""
        r = client.get("/graph/hubs?limit=20", headers=HEADERS)
        for h in r.json()["hubs"]:
            assert h["in_degree"] + h["out_degree"] == h["degree"]

    def test_hubs_limit_respected(self):
        """limit parametro è rispettato."""
        r = client.get("/graph/hubs?limit=3", headers=HEADERS)
        assert len(r.json()["hubs"]) <= 3
