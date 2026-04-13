"""
Kore — Ranking Engine v1 tests (Issue #006)
Verifica: score composito, ordinamento corretto, ranking_profile nel response,
segnali FTS vs semantic, freshness, conflict penalty.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from kore_memory.database import get_connection
from kore_memory.main import app
from kore_memory.models import MemoryRecord, MemorySaveRequest
from kore_memory.ranking import (
    RANKING_PROFILE,
    _compute_freshness,
    _normalize_similarity,
    compute_score,
    rank_results,
)
from kore_memory.repository import save_memory

HEADERS = {"X-Agent-Id": "test-ranking"}
client = TestClient(app)


def _save(content: str, **kwargs) -> int:
    req = MemorySaveRequest(content=content, **kwargs)
    row_id, _, _ = save_memory(req, agent_id="test-ranking")
    return row_id


def _make_record(**kwargs) -> MemoryRecord:
    """Crea un MemoryRecord minimale per i test unitari."""
    defaults = {
        "id": 1,
        "content": "Test content",
        "category": "general",
        "importance": 3,
        "decay_score": 1.0,
        "score": None,
        "confidence": 1.0,
        "created_at": datetime.now(UTC).isoformat(),
        "updated_at": datetime.now(UTC).isoformat(),
    }
    defaults.update(kwargs)
    return MemoryRecord(**defaults)


# ── Unit: _normalize_similarity ──────────────────────────────────────────────


class TestNormalizeSimilarity:
    def test_none_returns_neutral(self):
        """score=None → 0.5 (rilevanza neutra)."""
        assert _normalize_similarity(None) == 0.5

    def test_fts5_negative_returns_one(self):
        """FTS5 score negativo → 1.0 (tutti i match FTS sono rilevanti)."""
        assert _normalize_similarity(-0.73) == 1.0
        assert _normalize_similarity(-16.39) == 1.0
        assert _normalize_similarity(-0.001) == 1.0

    def test_cosine_similarity_normalized(self):
        """Cosine score in [0,1] viene passato direttamente."""
        assert _normalize_similarity(0.85) == pytest.approx(0.85)
        assert _normalize_similarity(1.0) == pytest.approx(1.0)
        assert _normalize_similarity(0.0) == pytest.approx(0.0)

    def test_cosine_above_one_capped(self):
        """Score > 1.0 viene capped a 1.0."""
        assert _normalize_similarity(1.5) == pytest.approx(1.0)


# ── Unit: _compute_freshness ─────────────────────────────────────────────────


class TestComputeFreshness:
    def test_just_created_is_one(self):
        """Memoria appena creata → freshness ≈ 1.0."""
        now = datetime.now(UTC).isoformat()
        assert _compute_freshness(now) == pytest.approx(1.0, abs=0.01)

    def test_one_year_ago_is_zero(self):
        """Memoria di 365 giorni → freshness ≈ 0.0."""
        old = (datetime.now(UTC) - timedelta(days=365)).isoformat()
        assert _compute_freshness(old) == pytest.approx(0.0, abs=0.01)

    def test_midpoint(self):
        """Memoria di ~182 giorni → freshness ≈ 0.5."""
        mid = (datetime.now(UTC) - timedelta(days=182)).isoformat()
        assert _compute_freshness(mid) == pytest.approx(0.5, abs=0.05)

    def test_none_returns_neutral(self):
        """created_at=None → 0.5."""
        assert _compute_freshness(None) == pytest.approx(0.5)

    def test_sqlite_format(self):
        """Formato SQLite ('YYYY-MM-DD HH:MM:SS') viene parsato correttamente."""
        now_sqlite = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
        assert _compute_freshness(now_sqlite) == pytest.approx(1.0, abs=0.01)


# ── Unit: compute_score ───────────────────────────────────────────────────────


class TestComputeScore:
    def test_perfect_record_score(self):
        """Record con tutti i segnali ottimali → score alto."""
        record = _make_record(
            score=0.95,      # cosine similarity alta
            decay_score=1.0,
            confidence=1.0,
        )
        score = compute_score(record)
        assert score > 0.90, f"Expected > 0.90, got {score}"

    def test_fts_record_score_depends_on_decay_freshness(self):
        """Record FTS5 (score negativo): score dipende da decay e freshness."""
        record_new = _make_record(score=-0.73, decay_score=1.0)
        record_old = _make_record(
            score=-0.73, decay_score=0.5,
            created_at=(datetime.now(UTC) - timedelta(days=200)).isoformat(),
            updated_at=(datetime.now(UTC) - timedelta(days=200)).isoformat(),
        )
        assert compute_score(record_new) > compute_score(record_old)

    def test_score_in_range(self):
        """Score finale è sempre in [0.0, 1.0]."""
        for decay in [0.0, 0.5, 1.0]:
            for confidence in [0.0, 0.5, 1.0]:
                record = _make_record(score=None, decay_score=decay, confidence=confidence)
                s = compute_score(record)
                assert 0.0 <= s <= 1.0

    def test_conflict_penalty_applied(self):
        """Memory con conflitto irrisolto riceve penalità × 0.60."""
        record = _make_record(id=99, score=0.9, decay_score=1.0)
        score_clean = compute_score(record, conflict_ids=None)
        score_conflict = compute_score(record, conflict_ids={99})
        assert score_conflict == pytest.approx(score_clean * 0.60, abs=0.001)


# ── Unit: rank_results ────────────────────────────────────────────────────────


class TestRankResults:
    def test_updates_score_field(self):
        """rank_results aggiorna il campo score di ogni MemoryRecord."""
        records = [_make_record(id=i, score=-0.5) for i in range(3)]
        for r in records:
            r.score = None
        result = rank_results(records)
        for r in result:
            assert r.score is not None

    def test_sorted_by_score_desc(self):
        """I record sono ordinati per score decrescente."""
        records = [
            _make_record(id=1, decay_score=0.3, confidence=0.4),
            _make_record(id=2, decay_score=1.0, confidence=1.0),
            _make_record(id=3, decay_score=0.7, confidence=0.7),
        ]
        result = rank_results(records)
        scores = [r.score for r in result]
        assert scores == sorted(scores, reverse=True)

    def test_higher_decay_scores_higher(self):
        """A parità di altri segnali, memoria con decay più alto vince."""
        r_high = _make_record(id=1, decay_score=1.0, score=1.0)
        r_low = _make_record(id=2, decay_score=0.3, score=1.0)
        result = rank_results([r_low, r_high])
        assert result[0].id == 1

    def test_fts_new_beats_old_same_content(self):
        """Per FTS5 (sim=1.0 per tutti), memoria più recente batte quella più vecchia."""
        r_new = _make_record(id=10, score=-0.7, decay_score=1.0)
        r_old = _make_record(
            id=5, score=-0.7, decay_score=1.0,
            created_at=(datetime.now(UTC) - timedelta(days=100)).isoformat(),
            updated_at=(datetime.now(UTC) - timedelta(days=100)).isoformat(),
        )
        result = rank_results([r_old, r_new])
        assert result[0].id == 10  # più recente vince


# ── Integration: ranking_profile nel response ─────────────────────────────────


class TestRankingProfileInResponse:
    def test_search_response_has_ranking_profile(self):
        """GET /search include ranking_profile nel response."""
        _save("Memoria per test ranking profile risposta API")
        r = client.get("/search?q=ranking+profile&semantic=false", headers=HEADERS)
        assert r.status_code == 200
        data = r.json()
        assert "ranking_profile" in data
        assert data["ranking_profile"] == RANKING_PROFILE

    def test_ranking_profile_is_default_v1(self):
        """ranking_profile deve essere 'default_v1'."""
        assert RANKING_PROFILE == "default_v1"

    def test_score_not_in_db(self):
        """score non esiste come colonna nel DB (è un campo runtime)."""
        with get_connection() as conn:
            pragma_rows = conn.execute("PRAGMA table_info(memories)").fetchall()
        columns = {r["name"] for r in pragma_rows}
        assert "score" not in columns


# ── Integration: ordinamento atteso ───────────────────────────────────────────


class TestSearchOrdering:
    def test_recent_memory_appears_in_results(self):
        """Memoria appena creata deve apparire nei risultati della ricerca."""
        mid = _save("Contenuto specifico per test ordinamento ranking engine v21")
        r = client.get("/search?q=contenuto+specifico+ordinamento+ranking&semantic=false", headers=HEADERS)
        assert r.status_code == 200
        ids = [m["id"] for m in r.json()["results"]]
        assert mid in ids

    def test_high_decay_beats_low_decay(self):
        """A parità di similarity FTS5, memoria con decay più alto compare prima."""
        content_base = "Xkq9z decay comparison ranking test memoria v21"
        id_high = _save(content_base + " high")
        id_low = _save(content_base + " low")

        # Abbassa il decay per id_low
        with get_connection() as conn:
            conn.execute(
                "UPDATE memories SET decay_score = 0.3 WHERE id = ?", (id_low,)
            )

        r = client.get(
            f"/search?q={content_base.replace(' ', '+')}&semantic=false&limit=10",
            headers=HEADERS,
        )
        results = r.json()["results"]
        ids = [m["id"] for m in results]
        if id_high in ids and id_low in ids:
            # id_high deve apparire prima di id_low
            assert ids.index(id_high) < ids.index(id_low)
