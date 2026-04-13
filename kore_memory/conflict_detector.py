"""
Kore — Conflict Detection
Rileva potenziali conflitti al salvataggio di una nuova memoria.

Un conflitto è rilevato quando esiste una memoria semanticamente simile
(similarity > KORE_CONFLICT_SIMILARITY) con periodo temporale sovrapposto.

Tipi di conflitto (v1):
- factual   — stessa entità, valori diversi, periodi sovrapposti
- temporal  — claim opposto sullo stesso topic nello stesso periodo
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from . import config as _cfg
from .database import get_connection


def detect_conflicts(
    memory_id: int,
    content: str,
    agent_id: str,
    valid_from: str | None,
    valid_to: str | None,
    confidence: float,
) -> list[str]:
    """
    Rileva conflitti per una memoria appena salvata.

    Viene eseguito solo se `confidence >= KORE_CONFLICT_MIN_CONFIDENCE`.
    I conflitti trovati vengono inseriti in `memory_conflicts` e i loro ID
    sono ritornati come lista (lista vuota = nessun conflitto).

    Args:
        memory_id: ID della memoria appena inserita
        content: testo della memoria
        agent_id: namespace dell'agente
        valid_from: inizio validità (formato SQLite o None)
        valid_to: fine validità (formato SQLite o None)
        confidence: livello di confidenza della nuova memoria

    Returns:
        Lista di conflict IDs creati (es. ["c-abc123", "c-def456"])
    """
    # Salta il conflict check se confidence è troppo bassa
    if confidence < _cfg.CONFLICT_MIN_CONFIDENCE:
        return []

    candidates = _find_candidates(memory_id, content, agent_id, valid_from, valid_to)
    if not candidates:
        return []

    conflict_ids = _persist_conflicts(memory_id, candidates, agent_id, valid_from, valid_to)
    return conflict_ids


def _find_candidates(
    memory_id: int,
    content: str,
    agent_id: str,
    valid_from: str | None,
    valid_to: str | None,
) -> list[dict]:
    """
    Trova memorie candidate al conflitto tramite similarità semantica o FTS5.

    Restituisce lista di dict con: id, content, valid_from, valid_to.
    """
    from .repository.memory import _embeddings_available

    if _embeddings_available():
        return _semantic_candidates(memory_id, content, agent_id, valid_from, valid_to)
    return _fts_candidates(memory_id, content, agent_id, valid_from, valid_to)


def _semantic_candidates(
    memory_id: int,
    content: str,
    agent_id: str,
    valid_from: str | None,
    valid_to: str | None,
) -> list[dict]:
    """Trova candidati via similarità coseno con il vector index."""
    try:
        from .embedder import embed_query
        from .vector_index import get_index

        query_vec = embed_query(content)
        index = get_index()
        top_ids = index.search(query_vec, agent_id, limit=_cfg.CONFLICT_MAX_CANDIDATES + 1)
    except Exception:
        return []

    # Filtra sé stessa e applica soglia similarità
    candidates = [(mid, score) for mid, score in top_ids if mid != memory_id and score >= _cfg.CONFLICT_SIMILARITY]
    if not candidates:
        return []

    candidate_ids = [mid for mid, _ in candidates]
    overlap_filter = _build_overlap_filter(valid_from, valid_to)

    with get_connection() as conn:
        placeholders = ",".join("?" for _ in candidate_ids)
        rows = conn.execute(
            f"""
            SELECT id, content, valid_from, valid_to
            FROM memories
            WHERE id IN ({placeholders})
              AND agent_id = ?
              AND invalidated_at IS NULL
              AND archived_at IS NULL
              AND compressed_into IS NULL
              {overlap_filter}
            """,
            candidate_ids + [agent_id],
        ).fetchall()

    return [dict(r) for r in rows]


def _fts_candidates(
    memory_id: int,
    content: str,
    agent_id: str,
    valid_from: str | None,
    valid_to: str | None,
) -> list[dict]:
    """Trova candidati via FTS5 come fallback senza embeddings."""
    from .repository.search import _sanitize_fts_query

    safe_query = _sanitize_fts_query(content)
    if not safe_query:
        return []

    overlap_filter = _build_overlap_filter(valid_from, valid_to)

    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT m.id, m.content, m.valid_from, m.valid_to
            FROM memories_fts
            JOIN memories m ON memories_fts.rowid = m.id
            WHERE memories_fts MATCH ?
              AND m.id != ?
              AND m.agent_id = ?
              AND m.invalidated_at IS NULL
              AND m.archived_at IS NULL
              AND m.compressed_into IS NULL
              {overlap_filter}
            LIMIT ?
            """,
            (safe_query, memory_id, agent_id, _cfg.CONFLICT_MAX_CANDIDATES),
        ).fetchall()

    return [dict(r) for r in rows]


def _build_overlap_filter(valid_from: str | None, valid_to: str | None) -> str:
    """
    Costruisce il filtro SQL per periodo sovrapposto.

    Due periodi [A_from, A_to] e [B_from, B_to] si sovrappongono se:
    NOT (A_to < B_from OR A_from > B_to)
    Trattando NULL come infinito nei due estremi.
    """
    # Memoria senza alcun vincolo temporale: può collidere con qualsiasi memoria
    if valid_from is None and valid_to is None:
        return ""

    clauses = []
    if valid_to:
        # La nuova memoria finisce prima di valid_to, la candidata deve iniziare prima
        clauses.append(f"(valid_from IS NULL OR valid_from < '{valid_to}')")
    if valid_from:
        # La nuova memoria inizia dopo valid_from, la candidata deve finire dopo
        clauses.append(f"(valid_to IS NULL OR valid_to > '{valid_from}')")

    return "AND " + " AND ".join(clauses) if clauses else ""


def _persist_conflicts(
    memory_id: int,
    candidates: list[dict],
    agent_id: str,
    valid_from: str | None,
    valid_to: str | None,
) -> list[str]:
    """
    Inserisce i conflitti rilevati in `memory_conflicts`.
    Restituisce la lista di conflict ID creati.
    """
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
    conflict_ids = []

    with get_connection() as conn:
        for candidate in candidates:
            conflict_id = f"c-{uuid.uuid4().hex[:12]}"
            conflict_type = _infer_conflict_type(valid_from, valid_to, candidate)
            try:
                conn.execute(
                    """
                    INSERT INTO memory_conflicts
                        (id, memory_a_id, memory_b_id, conflict_type, detected_at, agent_id)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (conflict_id, memory_id, candidate["id"], conflict_type, now, agent_id),
                )
                conflict_ids.append(conflict_id)
            except Exception:
                pass  # degradazione graceful se la tabella non esiste ancora

    return conflict_ids


def _infer_conflict_type(
    valid_from: str | None,
    valid_to: str | None,
    candidate: dict,
) -> str:
    """
    Inferisce il tipo di conflitto in base alla sovrapposizione temporale.

    - temporal: entrambe le memorie hanno vincoli temporali sovrapposti
    - factual: una o entrambe senza vincoli temporali (claim generico)
    """
    has_temporal = valid_from or valid_to
    candidate_temporal = candidate.get("valid_from") or candidate.get("valid_to")
    if has_temporal and candidate_temporal:
        return "temporal"
    return "factual"
