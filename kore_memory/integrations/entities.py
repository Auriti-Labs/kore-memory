"""
Kore Memory — Entity Extraction integration.
Extracts named entities from memory content and stores them as tags.

Uses spaCy for NER when available, falls back to regex-based extraction.
Entities are stored as tags with `entity:type:value` format.

Usage:
    from kore_memory.integrations.entities import extract_entities, auto_tag_entities

    entities = extract_entities("Meeting with Juan at Google on 2024-01-15")
    # [{"type": "person", "value": "Juan"}, {"type": "org", "value": "Google"}, ...]

    auto_tag_entities(memory_id=1, content="...", agent_id="default")
"""

from __future__ import annotations

import re
from typing import Any

# ── spaCy lazy loading ────────────────────────────────────────────────────────

_spacy_nlp: Any = None
_spacy_checked: bool = False
_HAS_SPACY: bool = False

# Mapping from spaCy entity labels to our entity types
_SPACY_LABEL_MAP: dict[str, str] = {
    "PERSON": "person",
    "ORG": "org",
    "GPE": "location",
    "DATE": "date",
    "MONEY": "money",
    "PRODUCT": "product",
}


def _get_spacy_nlp() -> Any:
    """Lazy-load spaCy model on first use. Returns None if unavailable."""
    global _spacy_nlp, _spacy_checked, _HAS_SPACY

    if _spacy_checked:
        return _spacy_nlp

    _spacy_checked = True
    try:
        import spacy

        _HAS_SPACY = True
        try:
            _spacy_nlp = spacy.load("en_core_web_sm")
        except OSError:
            # Model not downloaded — try other common models
            for model_name in ("en_core_web_md", "en_core_web_lg"):
                try:
                    _spacy_nlp = spacy.load(model_name)
                    break
                except OSError:
                    continue
    except ImportError:
        _HAS_SPACY = False
        _spacy_nlp = None

    return _spacy_nlp


def spacy_available() -> bool:
    """Check if spaCy is available and a model is loaded."""
    _get_spacy_nlp()
    return _spacy_nlp is not None


# ── Regex-based fallback extractors ──────────────────────────────────────────

# Email: standard RFC-ish pattern
_EMAIL_RE = re.compile(r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b")

# URL: http/https URLs
_URL_RE = re.compile(r"https?://[a-zA-Z0-9._~:/?#\[\]@!$&'()*+,;=%-]+")

# Date: common formats (YYYY-MM-DD, DD/MM/YYYY, MM/DD/YYYY, Month DD YYYY, etc.)
_DATE_RE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}\b"  # 2024-01-15
    r"|\b\d{1,2}/\d{1,2}/\d{2,4}\b"  # 01/15/2024 or 15/01/24
    r"|\b(?:January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+\d{1,2},?\s*\d{4}\b"  # January 15, 2024
    r"|\b\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+\d{4}\b",  # 15 January 2024
    re.IGNORECASE,
)

# Money: currency values ($100, EUR 50.00, 1,000.50 USD, etc.)
_MONEY_RE = re.compile(
    r"[$\u20ac\u00a3\u00a5]\s*[\d,]+(?:\.\d{1,2})?"  # $100, EUR50.00
    r"|[\d,]+(?:\.\d{1,2})?\s*(?:USD|EUR|GBP|JPY|CHF|BTC|ETH)\b"  # 100 USD
    r"|(?:USD|EUR|GBP|JPY|CHF)\s*[\d,]+(?:\.\d{1,2})?",  # USD 100
    re.IGNORECASE,
)


def _extract_regex(text: str) -> list[dict[str, str]]:
    """Extract entities using regex patterns (no external dependencies)."""
    entities: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for match in _EMAIL_RE.finditer(text):
        val = match.group().lower()
        key = ("email", val)
        if key not in seen:
            seen.add(key)
            entities.append({"type": "email", "value": val})

    for match in _URL_RE.finditer(text):
        val = match.group().rstrip(".,;:")
        key = ("url", val.lower())
        if key not in seen:
            seen.add(key)
            entities.append({"type": "url", "value": val})

    for match in _DATE_RE.finditer(text):
        val = match.group().strip()
        key = ("date", val.lower())
        if key not in seen:
            seen.add(key)
            entities.append({"type": "date", "value": val})

    for match in _MONEY_RE.finditer(text):
        val = match.group().strip()
        key = ("money", val.lower())
        if key not in seen:
            seen.add(key)
            entities.append({"type": "money", "value": val})

    return entities


def _extract_spacy(text: str) -> list[dict[str, str]]:
    """Extract entities using spaCy NER."""
    nlp = _get_spacy_nlp()
    if nlp is None:
        return []

    doc = nlp(text[:10000])  # Limit text length for performance
    entities: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for ent in doc.ents:
        entity_type = _SPACY_LABEL_MAP.get(ent.label_)
        if entity_type is None:
            continue
        val = ent.text.strip()
        if not val:
            continue
        key = (entity_type, val.lower())
        if key not in seen:
            seen.add(key)
            entities.append({"type": entity_type, "value": val})

    return entities


# ── Public API ────────────────────────────────────────────────────────────────


def extract_entities(text: str) -> list[dict[str, str]]:
    """
    Extract entities from text content.

    Uses spaCy NER when available (PERSON, ORG, GPE, DATE, MONEY, PRODUCT),
    falls back to regex extraction (email, url, date, money).

    Both methods are combined when spaCy is available — spaCy handles named
    entities while regex catches structured patterns (emails, URLs) that
    spaCy may miss.

    Args:
        text: The text to extract entities from.

    Returns:
        List of dicts with 'type' and 'value' keys.
        Example: [{"type": "person", "value": "Juan"}, ...]
    """
    if not text or not text.strip():
        return []

    entities: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    # Always run regex extraction (catches emails, URLs, structured patterns)
    regex_entities = _extract_regex(text)
    for ent in regex_entities:
        key = (ent["type"], ent["value"].lower())
        if key not in seen:
            seen.add(key)
            entities.append(ent)

    # Add spaCy entities if available
    if spacy_available():
        spacy_entities = _extract_spacy(text)
        for ent in spacy_entities:
            key = (ent["type"], ent["value"].lower())
            if key not in seen:
                seen.add(key)
                entities.append(ent)

    return entities


def auto_tag_entities(memory_id: int, content: str, agent_id: str = "default") -> int:
    """
    Extract entities from content, save as tags AND populate graph_entities + links.

    Tags are stored in `entity:type:value` format (backward compat).
    Graph entities are stored in graph_entities + memory_entity_links (M1 v4.0).

    Returns:
        Number of entity tags added.
    """
    from ..repository import add_tags

    entities = extract_entities(content)
    if not entities:
        return 0

    tags = []
    for ent in entities:
        value = ent["value"].strip().lower()[:80]
        tag = f"entity:{ent['type']}:{value}"
        tags.append(tag)

    tag_count = add_tags(memory_id, tags, agent_id=agent_id) if tags else 0

    # M1: also populate graph_entities + memory_entity_links
    graph_entities = extract_graph_entities(content)
    if graph_entities:
        try:
            from ..repository.entity import link_entities_to_memory

            link_entities_to_memory(memory_id, graph_entities, agent_id=agent_id)
        except Exception:
            pass  # graceful degradation

    return tag_count


# ── M1: Graph-compatible entity extraction ───────────────────────────────────

# File path pattern
_FILE_RE = re.compile(r"(?:^|[\s\"'`(])([a-zA-Z0-9_./\\-]+\.[a-zA-Z]{1,10})(?=[\s\"'`),:;]|$)")

# Project name pattern (word-word or word_word)
_PROJECT_RE = re.compile(r"\b([a-z][a-z0-9]*(?:[-_][a-z0-9]+)+)\b")

# Person name pattern (two+ capitalized words)
_PERSON_RE = re.compile(r"\b([A-Z][a-z]{1,20}(?:\s+[A-Z][a-z]{1,20})+)\b")

# Tech terms: loaded once from static file or inline fallback
_TECH_TERMS: set[str] | None = None


def _load_tech_terms() -> set[str]:
    global _TECH_TERMS
    if _TECH_TERMS is not None:
        return _TECH_TERMS
    # Try loading from file
    from pathlib import Path

    terms_file = Path(__file__).parent.parent / "data" / "tech_terms.txt"
    if terms_file.exists():
        _TECH_TERMS = {line.strip().lower() for line in terms_file.read_text().splitlines() if line.strip()}
    else:
        # Inline fallback: common tech terms
        _TECH_TERMS = {
            "python",
            "javascript",
            "typescript",
            "rust",
            "go",
            "java",
            "ruby",
            "php",
            "swift",
            "kotlin",
            "react",
            "vue",
            "angular",
            "svelte",
            "next.js",
            "nuxt",
            "astro",
            "fastapi",
            "django",
            "flask",
            "express",
            "nest.js",
            "spring",
            "laravel",
            "rails",
            "sqlite",
            "postgresql",
            "mysql",
            "mongodb",
            "redis",
            "elasticsearch",
            "docker",
            "kubernetes",
            "terraform",
            "ansible",
            "nginx",
            "apache",
            "git",
            "github",
            "gitlab",
            "aws",
            "gcp",
            "azure",
            "vercel",
            "netlify",
            "cloudflare",
            "graphql",
            "rest",
            "grpc",
            "websocket",
            "oauth",
            "jwt",
            "openai",
            "anthropic",
            "claude",
            "gpt",
            "llm",
            "rag",
            "mcp",
            "pydantic",
            "sqlalchemy",
            "prisma",
            "drizzle",
            "tailwind",
            "webpack",
            "vite",
            "esbuild",
            "npm",
            "pip",
            "cargo",
            "pytest",
            "jest",
            "vitest",
            "cypress",
            "linux",
            "macos",
            "windows",
            "ubuntu",
            "debian",
            "alpine",
            "node.js",
            "deno",
            "bun",
            "pandas",
            "numpy",
            "scipy",
            "pytorch",
            "tensorflow",
            "huggingface",
            "langchain",
            "crewai",
            "supabase",
            "firebase",
            "stripe",
            "twilio",
            "sendgrid",
            "datadog",
            "sentry",
            "grafana",
            "prometheus",
            "kafka",
            "rabbitmq",
            "celery",
            "airflow",
            "spark",
            "hadoop",
            "snowflake",
            "spacy",
            "nltk",
            "scikit-learn",
            "matplotlib",
            "plotly",
            "streamlit",
            "gradio",
            "html",
            "css",
            "sass",
            "less",
            "json",
            "yaml",
            "toml",
            "xml",
            "markdown",
            "latex",
            "sql",
            "nosql",
            "orm",
            "api",
            "sdk",
            "cli",
            "tui",
            "gui",
            "ide",
            "vscode",
            "neovim",
            "sentence-transformers",
            "sqlite-vec",
            "chromadb",
            "pinecone",
            "weaviate",
            "qdrant",
            "fastmcp",
            "pydantic-ai",
            "openai-agents",
            "filament",
            "wordpress",
            "gutenberg",
        }
    return _TECH_TERMS


# M1 entity type precedence (fallback mode): file → tech → project → person → location → concept
_GRAPH_ENTITY_TYPES = {"person", "org", "tech", "file", "concept", "location", "project"}

# Map spaCy labels to M1 graph entity types
_SPACY_TO_GRAPH: dict[str, str] = {
    "PERSON": "person",
    "ORG": "org",
    "GPE": "location",
    "PRODUCT": "tech",
}


def extract_graph_entities(text: str) -> list[tuple[str, str]]:
    """
    Extract entities suitable for graph_entities table.
    Returns list of (name, entity_type) tuples.
    Precedence (fallback mode): file → tech → project → person → location → concept.
    """
    if not text or not text.strip():
        return []

    results: list[tuple[str, str]] = []
    seen: set[str] = set()  # canonical lowercase names

    # If spaCy available, use it first (highest quality)
    if spacy_available():
        nlp = _get_spacy_nlp()
        if nlp:
            doc = nlp(text[:10000])
            for ent in doc.ents:
                graph_type = _SPACY_TO_GRAPH.get(ent.label_)
                if graph_type and ent.text.strip():
                    key = ent.text.strip().lower()
                    if key not in seen and len(key) >= 3:
                        seen.add(key)
                        results.append((ent.text.strip(), graph_type))

    # Fallback regex extraction in precedence order
    # 1. Files
    for m in _FILE_RE.finditer(text):
        val = m.group(1)
        key = val.lower()
        if key not in seen and len(key) >= 3 and "/" in val:
            seen.add(key)
            results.append((val, "file"))

    # 2. Tech terms
    tech_terms = _load_tech_terms()
    words = re.findall(r"[a-zA-Z][a-zA-Z0-9._-]*", text)
    for w in words:
        key = w.lower()
        if key in tech_terms and key not in seen:
            seen.add(key)
            results.append((w, "tech"))

    # 3. Projects (word-word patterns)
    for m in _PROJECT_RE.finditer(text):
        val = m.group(1)
        key = val.lower()
        if key not in seen and key not in tech_terms:
            seen.add(key)
            results.append((val, "project"))

    # 4. Person names (only in fallback mode, if spaCy didn't run)
    if not spacy_available():
        for m in _PERSON_RE.finditer(text):
            val = m.group(1)
            key = val.lower()
            if key not in seen:
                seen.add(key)
                results.append((val, "person"))

    # 5. Concepts: words ≥5 chars appearing 2+ times, not already extracted
    _stopwords = {
        "about",
        "after",
        "again",
        "being",
        "below",
        "between",
        "could",
        "doing",
        "during",
        "every",
        "first",
        "found",
        "given",
        "going",
        "having",
        "their",
        "there",
        "these",
        "thing",
        "think",
        "those",
        "three",
        "through",
        "under",
        "using",
        "value",
        "where",
        "which",
        "while",
        "would",
        "should",
        "other",
        "before",
        "because",
        "already",
        "always",
        "another",
        "without",
        "within",
    }
    from collections import Counter

    word_counts = Counter(w.lower() for w in re.findall(r"[a-zA-Z]{5,}", text))
    for word, count in word_counts.most_common(10):
        if count >= 2 and word not in seen and word not in _stopwords and word not in tech_terms:
            seen.add(word)
            results.append((word, "concept"))

    return results[:20]  # Cap at 20 per spec


def search_entities(
    agent_id: str,
    entity_type: str | None = None,
    limit: int = 50,
) -> list[dict[str, str]]:
    """
    Search entity tags across all memories for an agent.

    Queries the memory_tags table for tags matching `entity:*` pattern,
    optionally filtered by entity type.

    Args:
        agent_id: The agent namespace to search within.
        entity_type: Optional filter by entity type (e.g., "person", "email").
        limit: Maximum number of results (default: 50).

    Returns:
        List of dicts with 'type', 'value', 'memory_id', and 'tag' keys.
    """
    from ..database import get_connection

    pattern = f"entity:{entity_type.lower()}:%" if entity_type else "entity:%"

    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT mt.memory_id, mt.tag
            FROM memory_tags mt
            JOIN memories m ON mt.memory_id = m.id
            WHERE mt.tag LIKE ?
              AND m.agent_id = ?
              AND m.compressed_into IS NULL
              AND (m.expires_at IS NULL OR m.expires_at > datetime('now'))
            ORDER BY m.created_at DESC
            LIMIT ?
            """,
            (pattern, agent_id, limit),
        ).fetchall()

    results: list[dict[str, str]] = []
    for row in rows:
        tag = row["tag"]
        parts = tag.split(":", 2)
        if len(parts) == 3:
            results.append(
                {
                    "type": parts[1],
                    "value": parts[2],
                    "memory_id": row["memory_id"],
                    "tag": tag,
                }
            )

    return results
