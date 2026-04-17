"""
Kore — Structured extraction (M2).
Decomposes content into facts, concepts, and narrative. Zero LLM.
Best-effort: optimized for technical content in English or mixed language.
"""

from __future__ import annotations

import re
from collections import Counter

# Assertive verb patterns for fact extraction (English-optimized)
_FACT_VERBS = re.compile(
    r"\b(?:uses?|is|are|has|have|runs?|depends?|requires?|supports?|implements?|"
    r"returns?|creates?|stores?|handles?|provides?|connects?|enables?|"
    r"manages?|processes?|generates?|validates?|configures?|deploys?)\b",
    re.I,
)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")

_STOPWORDS = frozenset({
    "about", "after", "again", "also", "been", "being", "below", "between",
    "both", "could", "does", "doing", "down", "during", "each", "every",
    "first", "from", "further", "given", "going", "have", "having", "here",
    "into", "just", "like", "make", "many", "more", "most", "much", "must",
    "need", "only", "other", "over", "same", "should", "some", "such",
    "than", "that", "their", "them", "then", "there", "these", "they",
    "this", "those", "through", "under", "upon", "very", "want", "well",
    "were", "what", "when", "where", "which", "while", "will", "with",
    "within", "without", "would", "your", "before", "because", "already",
    "always", "another", "cannot", "nothing", "something", "everything",
    "however", "therefore", "although", "otherwise", "things",
})

_MIN_CONTENT_LEN = 50
_MAX_FACTS = 20
_MAX_FACT_LEN = 200
_MAX_CONCEPTS = 15
_MAX_NARRATIVE_LEN = 500


def extract_structured(
    content: str,
) -> tuple[list[str] | None, list[str] | None, str | None]:
    """
    Decompose content into (facts, concepts, narrative). Zero LLM.

    Returns (None, None, None) if content is too short or unstructured.
    """
    if not content or len(content.strip()) < _MIN_CONTENT_LEN:
        return None, None, None

    sentences = [s.strip() for s in _SENTENCE_SPLIT.split(content.strip()) if s.strip()]
    if len(sentences) < 2:
        return None, None, None

    facts = _extract_facts(sentences)
    concepts = _extract_concepts(content)
    narrative = _extract_narrative(sentences, content)

    return (
        facts if facts else None,
        concepts if concepts else None,
        narrative if narrative else None,
    )


def _extract_facts(sentences: list[str]) -> list[str]:
    """Extract assertive sentences as atomic facts."""
    facts: list[str] = []
    seen: set[str] = set()
    for s in sentences:
        if _FACT_VERBS.search(s):
            normalized = s.strip()
            key = normalized.lower()
            if key not in seen and len(normalized) >= 10:
                seen.add(key)
                facts.append(normalized[:_MAX_FACT_LEN])
                if len(facts) >= _MAX_FACTS:
                    break
    return facts


def _extract_concepts(content: str) -> list[str]:
    """Extract significant words by frequency, excluding stopwords."""
    words = re.findall(r"[a-zA-Z][a-zA-Z0-9_-]*", content)
    counts = Counter(w.lower() for w in words if len(w) >= 4 and w.lower() not in _STOPWORDS)
    concepts: list[str] = []
    seen: set[str] = set()
    for word, _ in counts.most_common(30):
        if word not in seen and not word.isdigit():
            seen.add(word)
            concepts.append(word[:50])
            if len(concepts) >= _MAX_CONCEPTS:
                break
    return concepts


def _extract_narrative(sentences: list[str], content: str) -> str | None:
    """Build narrative from top keyword-dense sentences."""
    if len(sentences) < 2:
        return None

    # Score by keyword density
    keywords = {w.lower() for w in re.findall(r"[a-zA-Z]{4,}", content) if w.lower() not in _STOPWORDS}
    scored: list[tuple[int, float, str]] = []
    for i, s in enumerate(sentences):
        tokens = s.lower().split()
        if not tokens:
            continue
        hits = sum(1 for t in tokens if t.rstrip(".,;:!?") in keywords)
        density = hits / len(tokens)
        scored.append((i, density, s))

    scored.sort(key=lambda x: x[1], reverse=True)
    top = sorted(scored[:3], key=lambda x: x[0])  # restore original order

    narrative = " ".join(s for _, _, s in top)
    return narrative[:_MAX_NARRATIVE_LEN] if narrative else None
