"""
Kore — Memory Summarizer
TF-IDF based keyword extraction and topic summarization without LLM.
"""

from __future__ import annotations

import math
import re
from collections import Counter

from .database import get_connection

# Common stop words (multilingual: EN + IT)
_STOP_WORDS = frozenset(
    [
        # English
        "the",
        "a",
        "an",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "shall",
        "should",
        "may",
        "might",
        "can",
        "could",
        "this",
        "that",
        "these",
        "those",
        "it",
        "its",
        "they",
        "them",
        "their",
        "he",
        "she",
        "him",
        "her",
        "his",
        "we",
        "our",
        "you",
        "your",
        "i",
        "me",
        "my",
        "and",
        "or",
        "but",
        "not",
        "no",
        "nor",
        "so",
        "if",
        "then",
        "than",
        "too",
        "very",
        "just",
        "about",
        "also",
        "back",
        "before",
        "between",
        "both",
        "by",
        "came",
        "come",
        "each",
        "from",
        "get",
        "got",
        "how",
        "into",
        # Italian
        "il",
        "lo",
        "la",
        "le",
        "gli",
        "un",
        "una",
        "dei",
        "delle",
        "del",
        "della",
        "di",
        "da",
        "in",
        "con",
        "su",
        "per",
        "tra",
        "fra",
        "che",
        "non",
        "si",
        "al",
        "sono",
        "stato",
        "essere",
        "fatto",
        "come",
        "anche",
        "più",
        "questo",
        "quello",
        "e",
        "o",
        "ma",
        "se",
        "poi",
        "già",
        "ancora",
        "solo",
        "tutto",
        "tutti",
        "dove",
        "quando",
        "perché",
        "cosa",
        "chi",
    ]
)

_WORD_RE = re.compile(r"[a-zA-ZÀ-ÿ]{2,}")


def _tokenize(text: str) -> list[str]:
    """Extract lowercase words, filter stop words."""
    return [w.lower() for w in _WORD_RE.findall(text) if w.lower() not in _STOP_WORDS]


def _compute_tfidf(documents: list[list[str]]) -> list[dict[str, float]]:
    """Compute TF-IDF scores for each document."""
    n = len(documents)
    if n == 0:
        return []

    # Document frequency
    df: Counter = Counter()
    for doc in documents:
        df.update(set(doc))

    results = []
    for doc in documents:
        tf = Counter(doc)
        total = len(doc) or 1
        tfidf = {}
        for word, count in tf.items():
            tf_score = count / total
            idf_score = math.log(1 + n / (1 + df.get(word, 0)))
            tfidf[word] = round(tf_score * idf_score, 4)
        results.append(tfidf)
    return results


def summarize_topic(
    topic: str,
    agent_id: str = "default",
    limit: int = 50,
    top_keywords: int = 10,
) -> dict:
    """
    Summarize memories related to a topic.
    Returns keyword extraction (TF-IDF), category breakdown, and timeline span.
    """
    with get_connection() as conn:
        # Search memories matching the topic via FTS5
        try:
            rows = conn.execute(
                """
                SELECT m.id, m.content, m.category, m.importance, m.decay_score,
                       m.created_at, m.access_count
                FROM memories m
                JOIN memories_fts f ON f.rowid = m.id
                WHERE memories_fts MATCH ? AND m.agent_id = ?
                  AND m.archived_at IS NULL AND m.compressed_into IS NULL
                ORDER BY m.importance DESC, m.created_at DESC
                LIMIT ?
                """,
                (topic, agent_id, limit),
            ).fetchall()
        except Exception:
            # FTS match failure — fallback to LIKE
            rows = conn.execute(
                """
                SELECT id, content, category, importance, decay_score,
                       created_at, access_count
                FROM memories
                WHERE content LIKE ? AND agent_id = ?
                  AND archived_at IS NULL AND compressed_into IS NULL
                ORDER BY importance DESC, created_at DESC
                LIMIT ?
                """,
                (f"%{topic}%", agent_id, limit),
            ).fetchall()

    if not rows:
        return {
            "topic": topic,
            "memory_count": 0,
            "keywords": [],
            "categories": {},
            "avg_importance": 0.0,
            "time_span": None,
        }

    # Tokenize all memories
    documents = [_tokenize(r["content"]) for r in rows]
    tfidf_scores = _compute_tfidf(documents)

    # Aggregate TF-IDF across all documents
    global_scores: Counter = Counter()
    for doc_scores in tfidf_scores:
        for word, score in doc_scores.items():
            global_scores[word] += score

    keywords = [{"word": word, "score": round(score, 4)} for word, score in global_scores.most_common(top_keywords)]

    # Category breakdown
    categories: Counter = Counter()
    for r in rows:
        categories[r["category"]] += 1

    # Timeline span
    dates = [r["created_at"] for r in rows]
    time_span = {
        "earliest": min(dates),
        "latest": max(dates),
    }

    # Average importance
    avg_importance = round(sum(r["importance"] for r in rows) / len(rows), 2)

    return {
        "topic": topic,
        "memory_count": len(rows),
        "keywords": keywords,
        "categories": dict(categories),
        "avg_importance": avg_importance,
        "time_span": time_span,
    }
