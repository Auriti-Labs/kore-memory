"""
Kore — Ranking Engine v1.1 (Wave 2)
Score composito calcolato a runtime durante il retrieval.

Formula default_v1:
    final_score = (
        similarity       * 0.45 +
        decay_score      * 0.25 +
        confidence       * 0.15 +
        task_relevance   * 0.10 +  # Wave 2: calcolato se task fornito
        freshness        * 0.05 +
        graph_centrality * 0.00    # Wave 3
    ) * conflict_penalty

Formula coding_v1 (ottimizzata per task di sviluppo software):
    final_score = (
        similarity       * 0.40 +
        decay_score      * 0.18 +
        confidence       * 0.15 +
        task_relevance   * 0.12 +
        importance_n     * 0.08 +
        graph_centrality * 0.05 +  # Wave 3: 0.0 fino a #028
        freshness        * 0.02
    ) * conflict_penalty

NOTA: score non è persistito in DB — è un campo runtime presente
      solo nella risposta API, calcolato a ogni retrieval.
"""

from __future__ import annotations

from datetime import UTC, datetime

from .models import MemoryRecord

# ── Profili di ranking ───────────────────────────────────────────────────────

# Pesi default_v1: bilanciati per uso generico
_DEFAULT_WEIGHTS: dict[str, float] = {
    "similarity": 0.45,
    "decay_score": 0.23,
    "confidence": 0.15,
    "importance_n": 0.00,
    "task_relevance": 0.10,
    "graph_centrality": 0.02,  # M1: weak signal from entity graph
    "freshness": 0.05,
}

# Pesi coding_v1: ottimizzati per sviluppo software (issue #019)
CODING_PROFILE: dict[str, float] = {
    "similarity": 0.40,
    "decay_score": 0.18,
    "confidence": 0.15,
    "importance_n": 0.08,
    "task_relevance": 0.12,
    "graph_centrality": 0.03,  # M1: weak signal from entity graph
    "freshness": 0.04,
}

_PROFILES: dict[str, dict[str, float]] = {
    "default": _DEFAULT_WEIGHTS,
    "default_v1": _DEFAULT_WEIGHTS,
    "coding": CODING_PROFILE,
    "coding_v1": CODING_PROFILE,
}

# Penalità per conflitto irrisolto
_CONFLICT_PENALTY = 0.60

# Finestra temporale freshness (365 giorni)
_FRESHNESS_WINDOW_DAYS = 365

RANKING_PROFILE = "default_v1"


# ── Score computation ────────────────────────────────────────────────────────


def compute_score(
    record: MemoryRecord,
    conflict_ids: set[int] | None = None,
    task: str = "",
    task_vec: list[float] | None = None,
    embedding_map: dict[int, list[float]] | None = None,
    ranking_profile: str = "default",
    explain: bool = False,
) -> float:
    """
    Calcola lo score composito per una memoria.

    Args:
        record: la memoria da valutare
        conflict_ids: IDs con conflitti irrisolti (per penalità)
        task: testo del task corrente (per task_relevance)
        task_vec: embedding pre-calcolato del task
        embedding_map: mappa id → embedding delle memorie nel result set
        ranking_profile: profilo pesi ("default" | "coding")
        explain: se True, popola record.explain con il breakdown

    Returns:
        float in [0.0, 1.0]
    """
    weights = _PROFILES.get(ranking_profile, _DEFAULT_WEIGHTS)

    similarity = _normalize_similarity(record.score)
    decay = float(record.decay_score or 1.0)
    confidence = float(record.confidence or 1.0)
    importance_n = (record.importance - 1) / 4.0
    freshness = _compute_freshness(record.created_at)
    task_rel = _compute_task_relevance(record, task, task_vec, embedding_map)
    graph_centrality = _get_graph_centrality(record.id)

    raw = (
        similarity * weights["similarity"]
        + decay * weights["decay_score"]
        + confidence * weights["confidence"]
        + importance_n * weights["importance_n"]
        + task_rel * weights["task_relevance"]
        + graph_centrality * weights["graph_centrality"]
        + freshness * weights["freshness"]
    )

    conflict_penalty = 1.0
    if conflict_ids and record.id in conflict_ids:
        raw *= _CONFLICT_PENALTY
        conflict_penalty = _CONFLICT_PENALTY

    final_score = round(min(max(raw, 0.0), 1.0), 6)

    if explain:
        record.explain = {
            "signals": {
                "similarity": round(similarity, 4),
                "decay": round(decay, 4),
                "confidence": round(confidence, 4),
                "importance": round(importance_n, 4),
                "task_relevance": round(task_rel, 4),
                "graph_centrality": round(graph_centrality, 4),
                "freshness": round(freshness, 4),
            },
            "penalties": {
                "conflict_penalty": round(conflict_penalty, 4),
            },
            "final_score": final_score,
            "ranking_profile": ranking_profile,
            "rank": 0,  # aggiornato da rank_results dopo il sort
        }

    return final_score


def rank_results(
    results: list[MemoryRecord],
    conflict_ids: set[int] | None = None,
    task: str = "",
    task_vec: list[float] | None = None,
    embedding_map: dict[int, list[float]] | None = None,
    ranking_profile: str = "default",
    explain: bool = False,
) -> list[MemoryRecord]:
    """
    Ordina una lista di MemoryRecord per score composito decrescente.
    Aggiorna record.score e, se explain=True, record.explain.

    Returns:
        Lista ordinata per score desc.
    """
    for record in results:
        record.score = compute_score(
            record,
            conflict_ids=conflict_ids,
            task=task,
            task_vec=task_vec,
            embedding_map=embedding_map,
            ranking_profile=ranking_profile,
            explain=explain,
        )

    results.sort(key=lambda r: r.score or 0.0, reverse=True)

    # Aggiorna rank dopo il sort (1-based)
    if explain:
        for i, record in enumerate(results):
            if record.explain:
                record.explain["rank"] = i + 1

    return results


# ── Segnali helper ───────────────────────────────────────────────────────────


def _compute_task_relevance(
    record: MemoryRecord,
    task: str,
    task_vec: list[float] | None,
    embedding_map: dict[int, list[float]] | None,
) -> float:
    """
    Calcola la rilevanza della memoria rispetto al task corrente.

    - Se task non fornito → 0.5 (neutro)
    - Se embedding disponibile → cosine_similarity(task_vec, mem_embedding)
    - Fallback → keyword overlap normalizzato
    """
    if not task or not task.strip():
        return 0.5

    # Prova cosine similarity con embedding
    if task_vec and embedding_map and record.id in embedding_map:
        return _cosine_similarity(task_vec, embedding_map[record.id])

    # Fallback: keyword overlap normalizzato
    return _keyword_overlap(task, record.content)


def _keyword_overlap(task: str, content: str) -> float:
    """
    Overlap normalizzato tra parole del task e contenuto della memoria.
    Restituisce [0.0, 1.0]. Ignora stopwords corte (len < 3).
    """
    task_tokens = {w.lower() for w in task.split() if len(w) >= 3}
    if not task_tokens:
        return 0.5

    content_lower = content.lower()
    hits = sum(1 for t in task_tokens if t in content_lower)
    return min(1.0, hits / len(task_tokens))


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Dot product di vettori normalizzati = cosine similarity."""
    try:
        import numpy as np

        return float(np.dot(a, b))
    except ImportError:
        # Fallback puro Python (più lento ma funzionale)
        dot = sum(x * y for x, y in zip(a, b))
        return min(1.0, max(0.0, dot))


def _normalize_similarity(score: float | None) -> float:
    """
    Normalizza il punteggio di similarità a [0, 1].

    FTS5 (score < 0): tutti i match sono considerati rilevanti → 1.0
    Cosine similarity (score in [0, 1]): usa il valore direttamente
    None: assume rilevanza media (0.5)
    """
    if score is None:
        return 0.5
    if score < 0:
        return 1.0
    return min(1.0, float(score))


def _compute_freshness(created_at) -> float:
    """
    Freshness come decadimento lineare da 1.0 (appena creato)
    a 0.0 (più vecchio di _FRESHNESS_WINDOW_DAYS giorni).
    """
    if created_at is None:
        return 0.5
    try:
        if isinstance(created_at, str):
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


# ── M1: Graph centrality signal ──────────────────────────────────────────────

_centrality_cache: dict[int, float] = {}
_centrality_cache_ts: float = 0.0


def _get_graph_centrality(memory_id: int) -> float:
    """
    Get normalized degree centrality for a memory. Cached for 60s.
    Returns 0.0 if memory has no relations (graceful degradation).
    """
    import time

    global _centrality_cache, _centrality_cache_ts
    now = time.monotonic()
    if now - _centrality_cache_ts > 60:
        _centrality_cache = {}
        _centrality_cache_ts = now

    if memory_id in _centrality_cache:
        return _centrality_cache[memory_id]

    try:
        from .database import get_connection

        with get_connection() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS degree FROM memory_relations
                WHERE source_id = ? OR target_id = ?
                """,
                (memory_id, memory_id),
            ).fetchone()
            degree = row[0] if row else 0
            # Simple normalization: cap at 20 relations → 1.0
            centrality = min(1.0, degree / 20.0)
    except Exception:
        centrality = 0.0

    _centrality_cache[memory_id] = centrality
    return centrality
