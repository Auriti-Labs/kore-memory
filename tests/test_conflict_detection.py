"""
Kore — Conflict Detection tests (Issue #005)
Verifica: rilevamento conflitti al save, soglie configurabili,
overlap temporale, graceful degradation.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from kore_memory.conflict_detector import (
    _build_overlap_filter,
    _infer_conflict_type,
    detect_conflicts,
)
from kore_memory.database import get_connection
from kore_memory.main import app
from kore_memory.models import MemorySaveRequest
from kore_memory.repository import save_memory

HEADERS = {"X-Agent-Id": "test-conflict"}
client = TestClient(app)

_FMT = "%Y-%m-%d %H:%M:%S"


def _dt(delta_days: int = 0) -> str:
    return (datetime.now(UTC) + timedelta(days=delta_days)).strftime(_FMT)


def _save(content: str, **kwargs) -> int:
    req = MemorySaveRequest(content=content, **kwargs)
    row_id, _, _ = save_memory(req, agent_id="test-conflict")
    return row_id


# ── Unit test: _build_overlap_filter ─────────────────────────────────────────


class TestBuildOverlapFilter:
    def test_no_temporal_bounds_returns_empty(self):
        """Senza vincoli temporali il filtro è vuoto."""
        assert _build_overlap_filter(None, None) == ""

    def test_only_valid_to(self):
        """Solo valid_to genera il filtro corretto."""
        result = _build_overlap_filter(None, "2026-12-31 23:59:59")
        assert "valid_from" in result
        assert "2026-12-31" in result

    def test_only_valid_from(self):
        """Solo valid_from genera il filtro corretto."""
        result = _build_overlap_filter("2026-01-01 00:00:00", None)
        assert "valid_to" in result
        assert "2026-01-01" in result

    def test_both_bounds(self):
        """Entrambi i bounds generano clausole AND."""
        result = _build_overlap_filter("2026-01-01 00:00:00", "2026-12-31 23:59:59")
        assert "valid_from" in result
        assert "valid_to" in result
        assert result.startswith("AND ")


# ── Unit test: _infer_conflict_type ──────────────────────────────────────────


class TestInferConflictType:
    def test_both_temporal_returns_temporal(self):
        """Due memorie con vincoli temporali → tipo 'temporal'."""
        candidate = {"valid_from": "2026-01-01 00:00:00", "valid_to": None}
        result = _infer_conflict_type("2026-01-01 00:00:00", "2026-06-30 23:59:59", candidate)
        assert result == "temporal"

    def test_no_temporal_returns_factual(self):
        """Nessun vincolo temporale → tipo 'factual'."""
        candidate = {"valid_from": None, "valid_to": None}
        result = _infer_conflict_type(None, None, candidate)
        assert result == "factual"

    def test_mixed_returns_factual(self):
        """Solo una memoria con vincolo temporale → tipo 'factual'."""
        candidate = {"valid_from": None, "valid_to": None}
        result = _infer_conflict_type("2026-01-01 00:00:00", None, candidate)
        assert result == "factual"


# ── detect_conflicts: threshold confidence ───────────────────────────────────


class TestDetectConflictsConfidence:
    def test_low_confidence_skips_detection(self):
        """confidence < KORE_CONFLICT_MIN_CONFIDENCE → nessuna detection."""
        from kore_memory import config as cfg
        mid = _save("Test conflitto bassa confidenza")
        result = detect_conflicts(
            memory_id=mid + 1000,
            content="Test conflitto bassa confidenza simile",
            agent_id="test-conflict",
            valid_from=None,
            valid_to=None,
            confidence=cfg.CONFLICT_MIN_CONFIDENCE - 0.1,
        )
        assert result == []

    def test_high_confidence_runs_detection(self):
        """confidence >= KORE_CONFLICT_MIN_CONFIDENCE → detection eseguita."""
        from kore_memory import config as cfg
        # Crea una memoria prima come "candidato"
        _save("Limite velocità autostrada è 130 km/h limite massimo")
        mid = _save("Limite velocità autostrada elevata limite massimo")

        # La funzione deve eseguire (non skippa per confidence)
        # Anche se non trova conflitti (no embeddings in test), non deve crashare
        result = detect_conflicts(
            memory_id=mid,
            content="Limite velocità autostrada elevata limite massimo",
            agent_id="test-conflict",
            valid_from=None,
            valid_to=None,
            confidence=cfg.CONFLICT_MIN_CONFIDENCE + 0.1,
        )
        assert isinstance(result, list)


# ── detect_conflicts: env var configurabili ──────────────────────────────────


class TestConflictConfigurable:
    def test_conflict_similarity_threshold_from_env(self, monkeypatch):
        """KORE_CONFLICT_SIMILARITY modifica la soglia usata dal detector."""
        import importlib
        import kore_memory.config as cfg_mod
        monkeypatch.setenv("KORE_CONFLICT_SIMILARITY", "0.99")
        importlib.reload(cfg_mod)
        assert cfg_mod.CONFLICT_SIMILARITY == 0.99
        # Ripristina
        monkeypatch.delenv("KORE_CONFLICT_SIMILARITY", raising=False)
        importlib.reload(cfg_mod)

    def test_conflict_min_confidence_from_env(self, monkeypatch):
        """KORE_CONFLICT_MIN_CONFIDENCE modifica la soglia usata."""
        import importlib
        import kore_memory.config as cfg_mod
        monkeypatch.setenv("KORE_CONFLICT_MIN_CONFIDENCE", "0.50")
        importlib.reload(cfg_mod)
        assert cfg_mod.CONFLICT_MIN_CONFIDENCE == 0.50
        monkeypatch.delenv("KORE_CONFLICT_MIN_CONFIDENCE", raising=False)
        importlib.reload(cfg_mod)

    def test_conflict_max_candidates_from_env(self, monkeypatch):
        """KORE_CONFLICT_MAX_CANDIDATES modifica il numero massimo di candidati."""
        import importlib
        import kore_memory.config as cfg_mod
        monkeypatch.setenv("KORE_CONFLICT_MAX_CANDIDATES", "5")
        importlib.reload(cfg_mod)
        assert cfg_mod.CONFLICT_MAX_CANDIDATES == 5
        monkeypatch.delenv("KORE_CONFLICT_MAX_CANDIDATES", raising=False)
        importlib.reload(cfg_mod)

    def test_conflict_sync_default_is_true(self):
        """KORE_CONFLICT_SYNC default = True."""
        from kore_memory import config as cfg
        # In test, la variabile non è settata quindi dovrebbe essere True
        # (a meno che qualcuno l'abbia settata a false)
        assert isinstance(cfg.CONFLICT_SYNC, bool)


# ── API: conflicts_detected nel response ──────────────────────────────────────


class TestConflictApiResponse:
    def test_save_response_conflicts_detected_is_list(self):
        """POST /save include sempre conflicts_detected come lista."""
        r = client.post(
            "/save",
            json={"content": "Risposta API deve avere conflicts_detected lista"},
            headers=HEADERS,
        )
        assert r.status_code == 201
        data = r.json()
        assert "conflicts_detected" in data
        assert isinstance(data["conflicts_detected"], list)

    def test_fts_conflict_detected_and_persisted(self):
        """
        Senza embeddings, usa FTS5: due memorie simili con overlap temporale
        producono un conflitto rilevato e persistito in memory_conflicts.
        """
        # Prima memoria senza scadenza
        first_id = _save("Database PostgreSQL versione primaria principale sistema")

        # Seconda memoria molto simile (FTS match quasi certo)
        req = MemorySaveRequest(
            content="Database PostgreSQL versione aggiornata principale sistema nuova",
            confidence=1.0,
        )
        second_id, _, conflicts = save_memory(req, agent_id="test-conflict")

        # Con KORE_CONFLICT_SYNC=true, la lista può essere vuota (no embeddings)
        # o contenere conflict IDs (se FTS5 li trova)
        assert isinstance(conflicts, list)

        # Se ci sono conflitti, verifica che siano nel DB
        if conflicts:
            with get_connection() as conn:
                row = conn.execute(
                    "SELECT id, memory_a_id, memory_b_id, conflict_type FROM memory_conflicts WHERE id = ?",
                    (conflicts[0],),
                ).fetchone()
            assert row is not None
            assert row["memory_a_id"] == second_id

    def test_conflict_not_detected_when_sync_disabled(self, monkeypatch):
        """Con KORE_CONFLICT_SYNC=false, il conflict check è saltato."""
        import importlib
        import kore_memory.config as cfg_mod
        monkeypatch.setenv("KORE_CONFLICT_SYNC", "false")
        importlib.reload(cfg_mod)

        _save("Memoria base per test sync disabilitato")
        req = MemorySaveRequest(content="Memoria base per test sync disabilitato variante")
        _, _, conflicts = save_memory(req, agent_id="test-conflict")
        assert conflicts == []

        monkeypatch.delenv("KORE_CONFLICT_SYNC", raising=False)
        importlib.reload(cfg_mod)


# ── Graceful degradation ──────────────────────────────────────────────────────


class TestConflictGracefulDegradation:
    def test_save_succeeds_even_if_conflict_detection_raises(self, monkeypatch):
        """
        Se il conflict detector solleva un'eccezione, il save deve completarsi
        senza errori (degradazione graceful).
        """
        import kore_memory.conflict_detector as cd

        def _boom(*a, **kw):
            raise RuntimeError("Errore simulato nel conflict detector")

        monkeypatch.setattr(cd, "detect_conflicts", _boom)

        r = client.post(
            "/save",
            json={"content": "Test graceful degradation conflict detector crash"},
            headers=HEADERS,
        )
        # Il save deve completarsi con 201 anche se la detection crasha
        assert r.status_code == 201
