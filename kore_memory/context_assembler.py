"""
Kore — Context Assembly Engine (Wave 2, issue #017)

Costruisce un context package strutturato dato un task e un budget in token.

Context Assembly Contract (6 invarianti non violabili):
1. DETERMINISTIC UNDER FIXED CONFIG — stessi input + KB + pesi = output quasi identico
2. TOKEN-BUDGET BOUNDED — budget_tokens_used ≤ budget_tokens_requested SEMPRE
3. CONFLICT-AWARE BY DEFAULT — conflitti critici surfaced in conflicts[]
4. CONSERVATIVE ON LOW CONFIDENCE — confidence < 0.5 escluse di default
5. EXPLAINABLE WHEN REQUESTED — explain=true → score breakdown per memoria
6. NO SILENT DEGRADATION — embedder non disponibile → degraded=True nella response
"""

from __future__ import annotations

from .models import (
    ContextAssembleRequest,
    ContextAssembleResponse,
    ContextMemoryItem,
)


def assemble_context(
    req: ContextAssembleRequest,
    agent_id: str = "default",
) -> ContextAssembleResponse:
    """
    Assembla un context package rispettando tutti e 6 gli invarianti del Contract.

    Failure behavior:
    - Embedder non disponibile → fallback FTS5, degraded=True
    - budget_tokens ≤ 0 → validato da Pydantic (ge=1)
    - KB vuota → 200 OK con total_memories=0 e memories=[]
    - Conflitti irrisolti → package restituito con conflicts[] popolato

    Args:
        req: ContextAssembleRequest validato da Pydantic
        agent_id: namespace dell'agente

    Returns:
        ContextAssembleResponse con memoria e metadati
    """
    from .repository.memory import _embeddings_available
    from .repository.search import (
        search_memories,
    )

    degraded = not _embeddings_available()

    # Fetch candidati: fino a budget * 3 memorie (conservativo), minimo 50
    fetch_limit = max(int(req.budget_tokens / 20), 50)
    category = req.categories[0] if len(req.categories) == 1 else None

    results, _cursor, total_count, _excluded = search_memories(
        query=req.task,
        limit=fetch_limit,
        category=category,
        semantic=not degraded,
        agent_id=agent_id,
        include_historical=False,
        include_forgotten=False,
        task=req.task,
        ranking_profile=req.ranking_profile,
        explain=req.explain,
    )

    # Filtra per confidence se include_low_confidence=False (invariante #4)
    if not req.include_low_confidence:
        results = [r for r in results if (r.confidence or 1.0) >= 0.5]

    # Filtra per categorie se multiple specificate
    if len(req.categories) > 1:
        allowed = set(req.categories)
        results = [r for r in results if r.category in allowed]

    # Budget greedy: aggiungi memorie finché budget non esaurito (invariante #2)
    selected: list[ContextMemoryItem] = []
    tokens_used = 0

    for record in results:
        tokens_est = _estimate_tokens(record.content)
        if tokens_used + tokens_est > req.budget_tokens:
            break  # stop: budget esaurito
        selected.append(
            ContextMemoryItem(
                id=record.id,
                content=record.content,
                category=record.category,
                importance=record.importance,
                decay_score=record.decay_score or 1.0,
                confidence=record.confidence or 1.0,
                score=record.score or 0.0,
                tokens_estimated=tokens_est,
                status=record.status,
                conditions=record.conditions,
                explain=record.explain if req.explain else None,
            )
        )
        tokens_used += tokens_est

    # Verifica invariante TOKEN-BUDGET BOUNDED
    if tokens_used > req.budget_tokens:
        raise ValueError(
            f"INVARIANTE VIOLATO: budget_tokens_used ({tokens_used}) > budget_tokens ({req.budget_tokens})"
        )

    # Carica conflitti tra le memorie selezionate (invariante #3)
    selected_ids = [m.id for m in selected]
    conflicts = _build_conflict_summary(selected_ids, agent_id) if selected_ids else []

    return ContextAssembleResponse(
        task=req.task,
        budget_tokens_requested=req.budget_tokens,
        budget_tokens_used=tokens_used,
        total_memories=len(selected),
        ranking_profile=req.ranking_profile,
        degraded=degraded,
        memories=selected,
        conflicts=conflicts,
    )


def _estimate_tokens(text: str) -> int:
    """
    Stima il numero di token nel testo.
    Formula: len(text) / 4 — approssimazione pratica per italiano/inglese.
    Invariante TOKEN-BUDGET BOUNDED dipende da questa stima.
    """
    return max(1, len(text) // 4)


def _build_conflict_summary(memory_ids: list[int], agent_id: str) -> list[dict]:
    """
    Carica conflitti irrisolti tra le memorie selezionate.
    Usato per popolare conflicts[] nella response (invariante #3).
    """
    if not memory_ids:
        return []

    from .database import get_connection

    placeholders = ",".join("?" for _ in memory_ids)
    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT id, memory_a_id, memory_b_id, conflict_type, detected_at
            FROM memory_conflicts
            WHERE resolved_at IS NULL
              AND agent_id = ?
              AND (
                  memory_a_id IN ({placeholders})
                  AND memory_b_id IN ({placeholders})
              )
            LIMIT 20
            """,
            [agent_id] + memory_ids + memory_ids,
        ).fetchall()

    return [
        {
            "conflict_id": row[0],
            "memory_a_id": row[1],
            "memory_b_id": row[2],
            "conflict_type": row[3],
            "detected_at": row[4],
        }
        for row in rows
    ]
