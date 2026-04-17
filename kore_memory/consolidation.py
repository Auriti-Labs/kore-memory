"""
Kore — Session Consolidation (M3a).
Promotes raw session memories into episodic summaries.
Zero LLM. Uses structured fields from M2 or falls back to sentence merge.
"""

from __future__ import annotations

import json
import re
from collections import Counter

from .database import get_connection
from .models import MemorySaveRequest, ProvenanceSchema


# ── Aggregation helpers ──────────────────────────────────────────────────────


def _aggregate_facts(candidates: list[dict], max_facts: int = 20) -> list[str] | None:
    """Union-dedup facts from all candidates, max 20."""
    seen: set[str] = set()
    facts: list[str] = []
    for m in candidates:
        raw = m.get("facts_json")
        if not raw:
            continue
        try:
            items = json.loads(raw) if isinstance(raw, str) else raw
        except (ValueError, TypeError):
            continue
        for f in items:
            key = f.strip().lower()
            if key not in seen:
                seen.add(key)
                facts.append(f.strip())
                if len(facts) >= max_facts:
                    return facts
    return facts or None


def _aggregate_concepts(candidates: list[dict], max_concepts: int = 15) -> list[str] | None:
    """Union-dedup concepts sorted by cross-memory frequency, max 15."""
    counter: Counter = Counter()
    for m in candidates:
        raw = m.get("concepts_json")
        if not raw:
            continue
        try:
            items = json.loads(raw) if isinstance(raw, str) else raw
        except (ValueError, TypeError):
            continue
        for c in items:
            counter[c.strip().lower()] += 1
    if not counter:
        return None
    return [c for c, _ in counter.most_common(max_concepts)]


def _aggregate_narrative(candidates: list[dict], max_len: int = 500) -> str | None:
    """Build narrative from top facts of highest-importance sources."""
    scored: list[tuple[int, str]] = []
    for m in candidates:
        raw = m.get("facts_json")
        if not raw:
            continue
        try:
            items = json.loads(raw) if isinstance(raw, str) else raw
        except (ValueError, TypeError):
            continue
        imp = m.get("importance", 1)
        for f in items:
            scored.append((imp, f.strip()))
    if not scored:
        return None
    scored.sort(key=lambda x: x[0], reverse=True)
    parts = []
    seen: set[str] = set()
    for _, f in scored[:3]:
        key = f.lower()
        if key not in seen:
            seen.add(key)
            parts.append(f)
    narrative = " ".join(parts)
    return narrative[:max_len] if narrative else None


def _sentence_merge(candidates: list[dict], max_len: int = 4000) -> str:
    """Fallback: merge content via sentence dedup (pre-M2 memories)."""
    seen: set[str] = set()
    parts: list[str] = []
    for m in candidates:
        for s in re.split(r"(?<=[.!?])\s+|\n+", m.get("content", "")):
            s = s.strip()
            if s and s.lower() not in seen:
                seen.add(s.lower())
                parts.append(s)
    return " ".join(parts)[:max_len]


# ── Core consolidation ───────────────────────────────────────────────────────


def consolidate_session(session_id: str, agent_id: str = "default") -> dict:
    """
    Consolidate a session's memories into one episodic memory.
    Returns result dict with status and details.
    """
    # Gate 1: session exists and ended
    with get_connection() as conn:
        session = conn.execute(
            "SELECT id, title, ended_at FROM sessions WHERE id = ? AND agent_id = ?",
            (session_id, agent_id),
        ).fetchone()
    if not session or not session["ended_at"]:
        return {"consolidated": False, "skipped": "session_not_ended"}

    # Gate 2: idempotency — check provenance on existing episodic
    provenance_ref = f"session:{session_id}"
    with get_connection() as conn:
        existing = conn.execute(
            "SELECT id FROM memories WHERE agent_id = ? AND memory_type = 'episodic'"
            " AND provenance LIKE ?",
            (agent_id, f'%"source_ref": "{provenance_ref}"%'),
        ).fetchone()
    if existing:
        return {"consolidated": False, "skipped": "already_consolidated", "existing_id": existing[0]}

    # Load candidates
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT id, content, category, importance, confidence,
                      facts_json, concepts_json, narrative
               FROM memories
               WHERE session_id = ? AND agent_id = ?
                 AND compressed_into IS NULL
                 AND archived_at IS NULL
                 AND invalidated_at IS NULL""",
            (session_id, agent_id),
        ).fetchall()
    candidates = [dict(r) for r in rows]

    # Gate 3: exclude conflicted
    excluded_conflicted = 0
    if candidates:
        cand_ids = [m["id"] for m in candidates]
        placeholders = ",".join("?" for _ in cand_ids)
        with get_connection() as conn:
            conflict_rows = conn.execute(
                f"""SELECT DISTINCT memory_a_id, memory_b_id
                    FROM memory_conflicts
                    WHERE resolved_at IS NULL AND agent_id = ?
                      AND (memory_a_id IN ({placeholders}) OR memory_b_id IN ({placeholders}))""",
                [agent_id, *cand_ids, *cand_ids],
            ).fetchall()
        conflicted_ids: set[int] = set()
        cand_set = set(cand_ids)
        for r in conflict_rows:
            conflicted_ids.add(r[0])
            conflicted_ids.add(r[1])
        conflicted_ids &= cand_set
        if conflicted_ids:
            excluded_conflicted = len(conflicted_ids)
            candidates = [m for m in candidates if m["id"] not in conflicted_ids]

    # Gate 4: minimum candidates
    if len(candidates) < 3:
        return {"consolidated": False, "skipped": "too_few_candidates",
                "candidates": len(candidates), "excluded_conflicted": excluded_conflicted}

    # Gate 5: avg confidence
    avg_conf = sum(m.get("confidence") or 1.0 for m in candidates) / len(candidates)
    if avg_conf < 0.5:
        return {"consolidated": False, "skipped": "low_confidence",
                "avg_confidence": round(avg_conf, 2)}

    # Classify: has structured fields?
    has_structured = any(m.get("facts_json") for m in candidates)

    if has_structured:
        facts = _aggregate_facts(candidates)
        concepts = _aggregate_concepts(candidates)
        narrative = _aggregate_narrative(candidates)
        content = narrative or _sentence_merge(candidates)
        title_base = session["title"] or (facts[0] if facts else "Session summary")
        title = f"Session: {title_base}"[:120]
    else:
        # Fallback (a): sentence merge only
        content = _sentence_merge(candidates)
        facts = None
        concepts = None
        narrative = None
        title = f"Session: {session['title'] or 'summary'}"[:120]

    # Determine category and importance
    cats = [m["category"] for m in candidates]
    category = max(set(cats), key=cats.count)
    importance = max(m.get("importance", 1) for m in candidates)
    confidence = round(avg_conf, 2)

    source_ids = [m["id"] for m in candidates]

    # Create episodic memory
    from .repository.memory import save_memory

    req = MemorySaveRequest(
        content=content[:4000],
        category=category,
        importance=importance,
        confidence=confidence,
        memory_type="episodic",
        title=title,
        facts=facts,
        concepts=concepts,
        narrative=narrative[:500] if narrative else None,
        metadata={
            "source_memory_ids": source_ids,
            "consolidation_type": "session_to_episodic",
            "source_count": len(candidates),
            "excluded_conflicted": excluded_conflicted,
        },
        provenance=ProvenanceSchema(
            source_type="consolidation",
            source_ref=provenance_ref,
            session_id=session_id,
        ),
    )
    new_id, _, _ = save_memory(req, agent_id=agent_id)

    # Mark sources as compressed
    with get_connection() as conn:
        placeholders = ",".join("?" for _ in source_ids)
        conn.execute(
            f"UPDATE memories SET compressed_into = ? WHERE id IN ({placeholders})",
            [new_id, *source_ids],
        )

    return {
        "consolidated": True,
        "episodic_id": new_id,
        "sources_compressed": len(source_ids),
        "excluded_conflicted": excluded_conflicted,
    }


def consolidate_agent(agent_id: str = "default") -> dict:
    """Consolidate all eligible sessions for an agent."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id FROM sessions WHERE agent_id = ? AND ended_at IS NOT NULL",
            (agent_id,),
        ).fetchall()

    results = []
    consolidated = 0
    skipped = 0
    for row in rows:
        result = consolidate_session(row["id"], agent_id)
        results.append(result)
        if result.get("consolidated"):
            consolidated += 1
        else:
            skipped += 1

    return {
        "sessions_processed": len(rows),
        "sessions_consolidated": consolidated,
        "sessions_skipped": skipped,
        "details": results,
    }
