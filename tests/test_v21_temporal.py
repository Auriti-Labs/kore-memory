"""
Kore — v2.1 Temporal Memory Layer tests
Copre: supersessione atomica, validità temporale, memoria history,
status/conditions derivati, campi Pydantic estesi (issues #001-#004).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from kore_memory.main import app
from kore_memory.models import MemoryRecord, MemorySaveRequest
from kore_memory.repository import get_memory, save_memory, search_memories, get_memory_history

HEADERS = {"X-Agent-Id": "test-v21"}
client = TestClient(app)


# ── Helper ───────────────────────────────────────────────────────────────────


def _save_api(content: str, **body) -> dict:
    """Salva via API e ritorna il JSON di risposta."""
    payload = {"content": content, **body}
    r = client.post("/save", json=payload, headers=HEADERS)
    assert r.status_code == 201, r.text
    return r.json()


def _save_repo(content: str, **kwargs) -> int:
    """Salva via repository e ritorna il row_id."""
    req = MemorySaveRequest(content=content, **kwargs)
    row_id, _imp, _conflicts = save_memory(req, agent_id="test-v21")
    return row_id


# ── Issue #002: Pydantic — campi estesi ─────────────────────────────────────


class TestMemorySaveRequestExtended:
    def test_save_with_confidence(self):
        """MemorySaveRequest accetta confidence 0.0-1.0."""
        req = MemorySaveRequest(content="Dati parzialmente verificati", confidence=0.7)
        assert req.confidence == 0.7

    def test_save_confidence_default_is_one(self):
        """confidence default = 1.0."""
        req = MemorySaveRequest(content="Dati certi")
        assert req.confidence == 1.0

    def test_save_confidence_bounds(self):
        """confidence fuori [0, 1] deve fallire."""
        with pytest.raises(Exception):
            MemorySaveRequest(content="Test", confidence=1.5)
        with pytest.raises(Exception):
            MemorySaveRequest(content="Test", confidence=-0.1)

    def test_save_with_valid_from_to(self):
        """MemorySaveRequest accetta valid_from e valid_to."""
        now = datetime.now(UTC)
        future = now + timedelta(days=30)
        req = MemorySaveRequest(content="Memoria con scadenza", valid_from=now, valid_to=future)
        assert req.valid_from is not None
        assert req.valid_to is not None

    def test_save_with_supersedes_id(self):
        """MemorySaveRequest accetta supersedes_id."""
        req = MemorySaveRequest(content="Versione aggiornata", supersedes_id=42)
        assert req.supersedes_id == 42

    def test_save_with_memory_type(self):
        """MemorySaveRequest accetta memory_type."""
        req = MemorySaveRequest(content="Procedura di deploy", memory_type="procedural")
        assert req.memory_type == "procedural"

    def test_save_with_provenance(self):
        """MemorySaveRequest accetta ProvenanceSchema."""
        req = MemorySaveRequest(
            content="Memoria con provenienza",
            provenance={"source_type": "file", "source_ref": "docs/adr-001.md"},
        )
        assert req.provenance is not None
        assert req.provenance.source_type == "file"

    def test_new_coding_categories(self):
        """Le categorie coding mode sono valide."""
        for cat in ("architectural_decision", "root_cause", "runbook", "regression_note", "tech_debt", "api_contract"):
            req = MemorySaveRequest(content=f"Memoria di tipo {cat}", category=cat)
            assert req.category == cat

    def test_memory_type_inferred_from_runbook(self):
        """runbook → memory_type = procedural (inferenza automatica)."""
        row_id = _save_repo("Runbook per deploy in produzione", category="runbook")
        record = get_memory(row_id, agent_id="test-v21")
        assert record is not None
        assert record.memory_type == "procedural"

    def test_memory_type_inferred_from_root_cause(self):
        """root_cause → memory_type = episodic (inferenza automatica)."""
        row_id = _save_repo("Bug causato da race condition nel pool", category="root_cause")
        record = get_memory(row_id, agent_id="test-v21")
        assert record is not None
        assert record.memory_type == "episodic"

    def test_memory_type_explicit_overrides_inference(self):
        """memory_type esplicito non viene sovrascritto dall'inferenza."""
        row_id = _save_repo("Nota generale con tipo esplicito", category="general", memory_type="procedural")
        record = get_memory(row_id, agent_id="test-v21")
        assert record is not None
        assert record.memory_type == "procedural"


# ── Issue #004: Supersessione atomica ────────────────────────────────────────


class TestSupersession:
    def test_supersedes_invalidates_predecessor(self):
        """save con supersedes_id invalida atomicamente il predecessore."""
        # v1
        v1_id = _save_repo("API endpoint: POST /save — accetta importance 1-5")
        # v2 sostituisce v1
        v2_id = _save_repo(
            "API endpoint: POST /save — accetta importance 1-5 o None (auto)",
            supersedes_id=v1_id,
        )
        # v1 deve essere invalidata
        v1 = get_memory(v1_id, agent_id="test-v21")
        assert v1 is not None  # ancora esistente nel DB
        assert v1.status == "superseded"

    def test_superseded_excluded_from_search_by_default(self):
        """Memorie superseded non compaiono nei risultati di ricerca default."""
        old_content = "Architettura monolitica versione legacy kore sistema vecchio"
        new_content = "Architettura microservizi versione corrente kore sistema nuovo"
        old_id = _save_repo(old_content)
        _save_repo(new_content, supersedes_id=old_id)

        results, _, _ = search_memories("architettura kore sistema", agent_id="test-v21", semantic=False)
        result_ids = [r.id for r in results]
        assert old_id not in result_ids

    def test_supersession_preserves_new_memory(self):
        """Il salvataggio con supersedes_id crea la nuova memoria correttamente."""
        old_id = _save_repo("Vecchia decisione database: MySQL")
        new_id = _save_repo("Nuova decisione database: PostgreSQL", supersedes_id=old_id, category="decision")
        new_mem = get_memory(new_id, agent_id="test-v21")
        assert new_mem is not None
        assert new_mem.status == "active"
        assert new_mem.supersedes_id == old_id

    def test_supersession_via_api(self):
        """POST /save con supersedes_id ritorna superseded_id nel response."""
        old = _save_api("Configurazione vecchia: timeout=30s")
        old_id = old["id"]
        new = _save_api("Configurazione aggiornata: timeout=60s", supersedes_id=old_id)
        assert new["superseded_id"] == old_id

    def test_double_supersession_chain(self):
        """v3 → v2 → v1: v2 e v1 entrambe invalidate."""
        v1 = _save_repo("Versione 1 della configurazione")
        v2 = _save_repo("Versione 2 della configurazione", supersedes_id=v1)
        v3 = _save_repo("Versione 3 della configurazione", supersedes_id=v2)

        assert get_memory(v1, agent_id="test-v21").status == "superseded"
        assert get_memory(v2, agent_id="test-v21").status == "superseded"
        assert get_memory(v3, agent_id="test-v21").status == "active"

    def test_supersession_wrong_agent_no_effect(self):
        """Un agente non può invalidare memorie di un altro agente."""
        req = MemorySaveRequest(content="Memoria agente A")
        id_a, _, _ = save_memory(req, agent_id="test-v21-a")

        # Agente B prova a supersedere la memoria di A
        req2 = MemorySaveRequest(content="Tentativo di supersessione cross-agent", supersedes_id=id_a)
        save_memory(req2, agent_id="test-v21-b")

        # La memoria di A deve rimanere active
        mem_a = get_memory(id_a, agent_id="test-v21-a")
        assert mem_a is not None
        assert mem_a.status == "active"


# ── Issue #003: Filtro validità temporale ────────────────────────────────────


class TestValidityFiltering:
    def test_expired_valid_to_excluded_from_search(self):
        """Memorie con valid_to nel passato non compaiono nella ricerca default."""
        past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        req = MemorySaveRequest(
            content="Offerta speciale kore scaduta ieri promo", valid_to=past
        )
        expired_id, _, _ = save_memory(req, agent_id="test-v21")

        results, _, _ = search_memories("offerta speciale kore promo scaduta", agent_id="test-v21", semantic=False)
        result_ids = [r.id for r in results]
        assert expired_id not in result_ids

    def test_future_valid_to_included_in_search(self):
        """Memorie con valid_to nel futuro compaiono nella ricerca normale."""
        future = (datetime.now(UTC) + timedelta(days=30)).isoformat()
        req = MemorySaveRequest(
            content="Offerta valida ancora per un mese kore promo attiva", valid_to=future
        )
        active_id, _, _ = save_memory(req, agent_id="test-v21")

        results, _, _ = search_memories("offerta valida kore promo attiva", agent_id="test-v21", semantic=False)
        result_ids = [r.id for r in results]
        assert active_id in result_ids

    def test_include_historical_includes_expired(self):
        """include_historical=True include memorie con valid_to scaduto."""
        past = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
        req = MemorySaveRequest(
            content="Storico kore archivio memoria antica include historical flag test", valid_to=past
        )
        expired_id, _, _ = save_memory(req, agent_id="test-v21")

        results, _, _ = search_memories(
            "storico kore archivio memoria antica historical",
            agent_id="test-v21",
            semantic=False,
            include_historical=True,
        )
        result_ids = [r.id for r in results]
        assert expired_id in result_ids

    def test_no_valid_to_always_included(self):
        """Memorie senza valid_to compaiono sempre (nessuna scadenza)."""
        req = MemorySaveRequest(content="Memoria permanente kore senza scadenza sempre valida")
        perm_id, _, _ = save_memory(req, agent_id="test-v21")

        results, _, _ = search_memories("memoria permanente kore senza scadenza sempre valida", agent_id="test-v21", semantic=False)
        result_ids = [r.id for r in results]
        assert perm_id in result_ids


# ── GET /memories/{id}/history ───────────────────────────────────────────────


class TestMemoryHistoryEndpoint:
    def test_history_single_node(self):
        """History di una memoria senza predecessori ritorna solo sé stessa."""
        mid = _save_repo("Memoria senza storia")
        history = get_memory_history(mid, agent_id="test-v21")
        assert len(history) == 1
        assert history[0].id == mid

    def test_history_chain_chronological(self):
        """History di una catena v3→v2→v1 ritorna [v1, v2, v3] in ordine created_at."""
        v1 = _save_repo("Config v1: porta 8080")
        v2 = _save_repo("Config v2: porta 8765", supersedes_id=v1)
        v3 = _save_repo("Config v3: porta 8765 con TLS", supersedes_id=v2)

        history = get_memory_history(v3, agent_id="test-v21")
        assert len(history) == 3
        ids = [h.id for h in history]
        assert ids == [v1, v2, v3]

    def test_history_api_endpoint(self):
        """GET /memories/{id}/history ritorna la catena via HTTP come lista."""
        v1 = _save_repo("Endpoint v1: ritorna lista")
        v2 = _save_repo("Endpoint v2: ritorna lista paginata", supersedes_id=v1)

        r = client.get(f"/memories/{v2}/history", headers=HEADERS)
        assert r.status_code == 200
        data = r.json()  # lista diretta di MemoryRecord
        assert isinstance(data, list)
        assert len(data) == 2
        ids = [h["id"] for h in data]
        assert v1 in ids
        assert v2 in ids

    def test_history_endpoint_agent_isolation(self):
        """GET /memories/{id}/history con agent_id diverso ritorna 404 (memory non trovata)."""
        mid = _save_repo("Memoria di test-v21 privata isolamento agente")
        r = client.get(f"/memories/{mid}/history", headers={"X-Agent-Id": "test-v21-intruder"})
        assert r.status_code == 404

    def test_history_nonexistent_id_returns_empty(self):
        """History di un ID inesistente ritorna lista vuota."""
        history = get_memory_history(999999, agent_id="test-v21")
        assert history == []


# ── Status e Conditions derivati ─────────────────────────────────────────────


class TestStatusAndConditions:
    def test_active_memory_status_is_active(self):
        """Memoria appena salvata ha status='active'."""
        mid = _save_repo("Memoria attiva fresca appena creata")
        record = get_memory(mid, agent_id="test-v21")
        assert record.status == "active"
        assert record.conditions == []

    def test_superseded_memory_status(self):
        """Memoria invalidata via supersedes_id ha status='superseded'."""
        old = _save_repo("Vecchia versione API contract deprecato")
        _save_repo("Nuova versione API contract attuale", supersedes_id=old)
        record = get_memory(old, agent_id="test-v21")
        assert record.status == "superseded"

    def test_forgotten_is_condition_not_status(self):
        """decay_score < 0.05 produce conditions=['forgotten'] ma status rimane 'active'."""
        from kore_memory.database import get_connection
        mid = _save_repo("Memoria che verrà dimenticata decay test")
        # Forza il decay_score sotto soglia direttamente nel DB
        with get_connection() as conn:
            conn.execute(
                "UPDATE memories SET decay_score = 0.01 WHERE id = ? AND agent_id = ?",
                (mid, "test-v21"),
            )
        record = get_memory(mid, agent_id="test-v21")
        assert record.status == "active"  # status NON cambia
        assert "forgotten" in record.conditions

    def test_fading_condition(self):
        """decay_score in [0.05, 0.30) produce conditions=['fading']."""
        from kore_memory.database import get_connection
        mid = _save_repo("Memoria che sta sbiadendo fading test")
        with get_connection() as conn:
            conn.execute(
                "UPDATE memories SET decay_score = 0.15 WHERE id = ? AND agent_id = ?",
                (mid, "test-v21"),
            )
        record = get_memory(mid, agent_id="test-v21")
        assert "fading" in record.conditions
        assert "forgotten" not in record.conditions

    def test_low_confidence_condition(self):
        """confidence < 0.50 produce conditions=['low_confidence']."""
        mid = _save_repo("Dato incerto da verificare", confidence=0.3)
        record = get_memory(mid, agent_id="test-v21")
        assert "low_confidence" in record.conditions

    def test_high_confidence_no_condition(self):
        """confidence >= 0.50 non produce low_confidence."""
        mid = _save_repo("Dato verificato con certezza", confidence=0.9)
        record = get_memory(mid, agent_id="test-v21")
        assert "low_confidence" not in record.conditions

    def test_multiple_conditions_can_coexist(self):
        """forgotten e low_confidence possono coesistere."""
        from kore_memory.database import get_connection
        mid = _save_repo("Memoria incerta dimenticata multi-condition test", confidence=0.3)
        with get_connection() as conn:
            conn.execute(
                "UPDATE memories SET decay_score = 0.01 WHERE id = ? AND agent_id = ?",
                (mid, "test-v21"),
            )
        record = get_memory(mid, agent_id="test-v21")
        assert record.status == "active"
        assert "forgotten" in record.conditions
        assert "low_confidence" in record.conditions

    def test_stale_condition_within_7_days(self):
        """valid_to entro 7 giorni → condition 'stale'."""
        soon = (datetime.now(UTC) + timedelta(days=3)).isoformat()
        mid = _save_repo("Promozione in scadenza tra 3 giorni stale test", valid_to=soon)
        record = get_memory(mid, agent_id="test-v21")
        assert "stale" in record.conditions

    def test_no_stale_if_far_future(self):
        """valid_to tra 30 giorni NON produce stale."""
        far = (datetime.now(UTC) + timedelta(days=30)).isoformat()
        mid = _save_repo("Promozione lontana no stale test", valid_to=far)
        record = get_memory(mid, agent_id="test-v21")
        assert "stale" not in record.conditions


# ── API: response MemorySaveResponse esteso ──────────────────────────────────


class TestSaveResponseExtended:
    def test_save_response_has_conflicts_detected(self):
        """POST /save ritorna conflicts_detected (lista vuota per ora)."""
        resp = _save_api("Test response structure v21 conflitti")
        assert "conflicts_detected" in resp
        assert isinstance(resp["conflicts_detected"], list)

    def test_save_response_superseded_id_none_if_not_provided(self):
        """POST /save senza supersedes_id ritorna superseded_id=null."""
        resp = _save_api("Test response superseded_id null")
        assert resp.get("superseded_id") is None

    def test_save_response_superseded_id_when_provided(self):
        """POST /save con supersedes_id ritorna superseded_id valorizzato."""
        first = _save_api("Prima versione da sostituire v21 response test")
        first_id = first["id"]
        second = _save_api("Seconda versione sostitutiva v21 response test", supersedes_id=first_id)
        assert second["superseded_id"] == first_id

    def test_save_with_confidence_persisted(self):
        """confidence salvata viene recuperata corretta via GET /memories/{id}."""
        resp = _save_api("Dato con confidence 0.75 persistenza test", confidence=0.75)
        mid = resp["id"]
        r = client.get(f"/memories/{mid}", headers=HEADERS)
        assert r.status_code == 200
        assert r.json()["confidence"] == pytest.approx(0.75, abs=0.01)

    def test_save_with_category_architectural_decision(self):
        """category=architectural_decision accettata dall'API."""
        resp = _save_api(
            "Usiamo FastAPI invece di Django per la leggerezza",
            category="architectural_decision",
        )
        assert resp["id"] > 0
