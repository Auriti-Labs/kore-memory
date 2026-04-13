"""
Kore — Ranking Engine v1 (baseline_v1)
Score composito calcolato a runtime durante il retrieval.

Formula completa:
    final_score = (
        similarity       * 0.35 +
        decay_score      * 0.20 +
        confidence       * 0.15 +
        importance_n     * 0.10 +
        task_relevance   * 0.10 +  # rimandato a Wave 2
        graph_centrality * 0.05 +  # rimandato a Wave 3
        freshness        * 0.05
    ) * conflict_penalty

Wave 1 implementa: similarity, decay_score, confidence, importance_n, freshness.
task_relevance e graph_centrality sono 0.0 in Wave 1.

NOTA: score non è persistito in DB — è un campo runtime presente
      solo nella risposta API, calcolato a ogni retrieval.
"""

from __future__ import annotations

from datetime import UTC, datetime

from .models import MemoryRecord

# Pesi del profilo default_v1
# NOTE: importance_n NON è incluso come segnale di search — l'importance è una qualità
# assoluta della memoria, non di rilevanza alla query specifica. Includerla nel ranking
# di search farebbe scalare memorie "importanti" su memorie perfettamente rilevanti.
# task_relevance (Wave 2) e graph_centrality (Wave 3) sono 0.0 in Wave 1.
_WEIGHTS = {
    "similarity": 0.50,  # segnale principale: pertinenza alla query
    "decay_score": 0.25,  # memoria non dimenticata
    "confidence": 0.20,  # attendibilità del contenuto
    "importance_n": 0.00,  # non influenza il ranking di search (v2.1)
    "task_relevance": 0.00,  # Wave 2: sempre 0.0 in Wave 1
    "graph_centrality": 0.00,  # Wave 3: sempre 0.0 in Wave 1
    "freshness": 0.05,  # quanto è recente la memoria
}

# Penalità per conflitto irrisolto (memory_b_id ha conflitti non risolti)
_CONFLICT_PENALTY = 0.60

# Finestra temporale per il calcolo della freshness (365 giorni)
_FRESHNESS_WINDOW_DAYS = 365

RANKING_PROFILE = "default_v1"


def compute_score(record: MemoryRecord, conflict_ids: set[int] | None = None) -> float:
    """
    Calcola lo score composito baseline_v1 per una memoria.

    Args:
        record: la memoria da valutare
        conflict_ids: insieme di memory IDs con conflitti irrisolti

    Returns:
        float in [0.0, 1.0]
    """
    similarity = _normalize_similarity(record.score)
    decay = float(record.decay_score or 1.0)
    confidence = float(record.confidence or 1.0)
    importance_n = (record.importance - 1) / 4.0  # normalizza da [1,5] a [0,1]
    freshness = _compute_freshness(record.created_at)

    raw = (
        similarity * _WEIGHTS["similarity"]
        + decay * _WEIGHTS["decay_score"]
        + confidence * _WEIGHTS["confidence"]
        + importance_n * _WEIGHTS["importance_n"]
        + 0.0 * _WEIGHTS["task_relevance"]  # Wave 2
        + 0.0 * _WEIGHTS["graph_centrality"]  # Wave 3
        + freshness * _WEIGHTS["freshness"]
    )

    if conflict_ids and record.id in conflict_ids:
        raw *= _CONFLICT_PENALTY

    return round(min(max(raw, 0.0), 1.0), 6)


def rank_results(
    results: list[MemoryRecord],
    conflict_ids: set[int] | None = None,
) -> list[MemoryRecord]:
    """
    Ordina una lista di MemoryRecord per score composito decrescente.
    Aggiorna il campo `score` di ogni record con il valore calcolato.

    Args:
        results: lista di MemoryRecord già filtrata
        conflict_ids: IDs con conflitti irrisolti (per penalità)

    Returns:
        Lista ordinata per score desc, con record.score aggiornato.
    """
    for record in results:
        record.score = compute_score(record, conflict_ids)

    results.sort(key=lambda r: r.score or 0.0, reverse=True)
    return results


def _normalize_similarity(score: float | None) -> float:
    """
    Normalizza il punteggio di similarità/FTS a [0, 1].

    Per FTS5 (score < 0): tutte le memorie che passano il filtro FTS5 sono
    già state selezionate come rilevanti dalla query. Il rank BM25 di SQLite
    non è un buon proxy di rilevanza relativa (penalizza i documenti con
    keyword rare). Restituisce 1.0 per tutti i match FTS5 — il ranking
    composito differenzia tramite decay, confidence e freshness.

    Per cosine similarity (score in [0, 1]): usa il valore direttamente.
    """
    if score is None:
        return 0.5  # senza query → assume rilevanza media

    if score < 0:
        # FTS5: il match garantisce rilevanza, tratta tutti come rilevanti
        return 1.0

    return min(1.0, float(score))


def _compute_freshness(created_at) -> float:
    """
    Calcola la freshness come decadimento lineare da 1.0 (appena creato)
    a 0.0 (più vecchio di _FRESHNESS_WINDOW_DAYS giorni).

    Args:
        created_at: stringa ISO o datetime

    Returns:
        float in [0.0, 1.0]
    """
    if created_at is None:
        return 0.5

    try:
        if isinstance(created_at, str):
            # Supporta formato ISO e formato SQLite
            created_at = created_at.replace("T", " ").split("+")[0].split(".")[0]
            dt = datetime.fromisoformat(created_at)
        else:
            dt = created_at

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)

        age_days = (datetime.now(UTC) - dt).total_seconds() / 86400
        return max(0.0, 1.0 - age_days / _FRESHNESS_WINDOW_DAYS)
    except Exception:
        return 0.5
