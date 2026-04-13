"""
Kore — MCP Server (Model Context Protocol)
Exposes save, search, timeline, decay and compress as MCP tools
for direct integration with Claude, Cursor, and other MCP clients.

Usage:
  kore-mcp                                       # stdio (default)
  kore-mcp --transport streamable-http            # HTTP transport (porta 8766)
  kore-mcp --transport streamable-http --port 9000  # HTTP porta custom
"""

from __future__ import annotations

import re as _re

from mcp.server.fastmcp import FastMCP

from .database import init_db
from .models import MemorySaveRequest, MemoryUpdateRequest
from .repository import (
    add_relation,
    add_tags,
    cleanup_expired,
    delete_memory,
    export_memories,
    get_timeline,
    import_memories,
    run_decay_pass,
    save_memory,
    search_by_tag,
    search_memories,
    update_memory,
)

# Initialize DB before any operation
init_db()

mcp = FastMCP(
    "Kore Memory",
    json_response=True,
)

_SAFE_AGENT_RE = _re.compile(r"[^a-zA-Z0-9_\-]")


def _sanitize_agent_id(agent_id: str) -> str:
    """Sanitize agent_id: only alphanumeric characters, dashes and underscores, max 64 chars."""
    safe = _SAFE_AGENT_RE.sub("", agent_id)
    return (safe or "default")[:64]


# ── Tools ────────────────────────────────────────────────────────────────────


@mcp.tool()
def memory_save(
    content: str,
    category: str = "general",
    importance: int = 0,
    agent_id: str = "default",
) -> dict:
    """
    Save a memory to persistent storage.
    Importance is auto-calculated if 0 or not specified (1-5 = explicit).
    Categories: general, project, trading, finance, person, preference, task, decision.
    """
    req = MemorySaveRequest(content=content, category=category, importance=importance or None)
    mem_id, imp, _ = save_memory(req, agent_id=_sanitize_agent_id(agent_id))
    return {"id": mem_id, "importance": imp, "message": "Memory saved"}


@mcp.tool()
def memory_search(
    query: str,
    limit: int = 5,
    category: str = "",
    semantic: bool = True,
    agent_id: str = "default",
) -> dict:
    """
    Search memory. Supports semantic (embedding) and full-text search.
    Returns the most relevant memories sorted by score.
    Leave category empty to search across all categories.
    """
    results, next_cursor, total_count = search_memories(
        query=query,
        limit=limit,
        category=category or None,
        semantic=semantic,
        agent_id=_sanitize_agent_id(agent_id),
    )
    return {
        "results": [
            {
                "id": r.id,
                "content": r.content,
                "category": r.category,
                "importance": r.importance,
                "decay_score": r.decay_score,
                "score": r.score,
                "created_at": str(r.created_at),
            }
            for r in results
        ],
        "total": total_count,
        "has_more": next_cursor is not None,
    }


@mcp.tool()
def memory_timeline(
    subject: str,
    limit: int = 20,
    agent_id: str = "default",
) -> dict:
    """
    Timeline of memories on a subject, ordered from oldest to most recent.
    Useful for reconstructing the history of a project or a person.
    """
    results, next_cursor, total_count = get_timeline(
        subject=subject,
        limit=limit,
        agent_id=_sanitize_agent_id(agent_id),
    )
    return {
        "results": [
            {
                "id": r.id,
                "content": r.content,
                "category": r.category,
                "importance": r.importance,
                "created_at": str(r.created_at),
            }
            for r in results
        ],
        "total": total_count,
        "has_more": next_cursor is not None,
    }


@mcp.tool()
def memory_decay_run(agent_id: str = "default") -> dict:
    """
    Recalculate the decay score of all memories for the agent.
    Memories that have not been accessed decay over time following the Ebbinghaus curve.
    """
    updated = run_decay_pass(agent_id=_sanitize_agent_id(agent_id))
    return {"updated": updated, "message": "Decay pass complete"}


@mcp.tool()
def memory_compress(agent_id: str = "default") -> dict:
    """
    Compress similar memories by merging them into a single richer record.
    Reduces redundancy while preserving important information.
    """
    from .compressor import run_compression

    result = run_compression(agent_id=_sanitize_agent_id(agent_id))
    return {
        "clusters_found": result.clusters_found,
        "memories_merged": result.memories_merged,
        "new_records_created": result.new_records_created,
    }


@mcp.tool()
def memory_export(agent_id: str = "default") -> dict:
    """Export all active memories for the agent as a backup."""
    data = export_memories(agent_id=_sanitize_agent_id(agent_id))
    return {"memories": data, "total": len(data)}


@mcp.tool()
def memory_delete(
    memory_id: int,
    agent_id: str = "default",
) -> dict:
    """
    Delete a memory by id. The memory must belong to the specified agent.
    Returns success=True if deleted, False if not found.
    """
    deleted = delete_memory(memory_id, agent_id=_sanitize_agent_id(agent_id))
    return {
        "success": deleted,
        "message": "Memory deleted" if deleted else "Memory not found",
    }


@mcp.tool()
def memory_update(
    memory_id: int,
    content: str = "",
    category: str = "",
    importance: int = 0,
    agent_id: str = "default",
) -> dict:
    """
    Update an existing memory. Only the provided fields are modified.
    Regenerates the embedding if the content changes.
    Leave fields empty/0 for those you do not want to modify.
    """
    req = MemoryUpdateRequest(
        content=content or None,
        category=category or None,
        importance=importance or None,
    )
    updated = update_memory(memory_id, req, agent_id=_sanitize_agent_id(agent_id))
    return {
        "success": updated,
        "message": "Memory updated" if updated else "Memory not found",
    }


@mcp.tool()
def memory_save_batch(
    memories: list[dict],
    agent_id: str = "default",
) -> dict:
    """
    Save multiple memories in a batch. Each item must have at least 'content'.
    Optional fields: category (default 'general'), importance (None=auto, 1-5=explicit).
    Maximum 100 memories per batch.
    """
    saved = []
    errors = 0
    for mem in memories[:100]:
        content = mem.get("content", "")
        if not content or len(content.strip()) < 3:
            continue
        try:
            raw_imp = mem.get("importance")
            req = MemorySaveRequest(
                content=content,
                category=mem.get("category", "general"),
                importance=raw_imp if raw_imp and raw_imp >= 1 else None,
            )
            mem_id, imp, _ = save_memory(req, agent_id=_sanitize_agent_id(agent_id))
            saved.append({"id": mem_id, "importance": imp})
        except Exception:
            errors += 1
    return {"saved": saved, "total": len(saved), "errors": errors}


@mcp.tool()
def memory_add_tags(
    memory_id: int,
    tags: list[str],
    agent_id: str = "default",
) -> dict:
    """
    Add tags to a memory. Tags are normalized to lowercase.
    Returns the number of tags added.
    """
    count = add_tags(memory_id, tags, agent_id=_sanitize_agent_id(agent_id))
    return {"count": count, "message": f"{count} tags added"}


@mcp.tool()
def memory_search_by_tag(
    tag: str,
    agent_id: str = "default",
    limit: int = 20,
) -> dict:
    """
    Search memories by tag. Returns memories sorted by importance and date.
    """
    results = search_by_tag(tag, agent_id=_sanitize_agent_id(agent_id), limit=limit)
    return {
        "results": [
            {
                "id": r.id,
                "content": r.content,
                "category": r.category,
                "importance": r.importance,
                "decay_score": r.decay_score,
                "created_at": str(r.created_at),
            }
            for r in results
        ],
        "total": len(results),
    }


@mcp.tool()
def memory_add_relation(
    source_id: int,
    target_id: int,
    relation: str = "related",
    agent_id: str = "default",
) -> dict:
    """
    Create a relation between two memories (graph). Both must belong to the agent.
    Common types: related, causes, blocks, extends, contradicts.
    """
    created = add_relation(source_id, target_id, relation, agent_id=_sanitize_agent_id(agent_id))
    return {
        "success": created,
        "message": "Relation created" if created else "Failed — memories not found or not owned by agent",
    }


@mcp.tool()
def memory_cleanup(agent_id: str = "default") -> dict:
    """
    Delete memories with an expired TTL for the specified agent.
    Returns the number of records removed.
    """
    removed = cleanup_expired(agent_id=_sanitize_agent_id(agent_id))
    return {"removed": removed, "message": f"{removed} expired memories cleaned up"}


@mcp.tool()
def memory_import(
    memories: list[dict],
    agent_id: str = "default",
) -> dict:
    """
    Import memories from a list of dicts. Each item must have at least 'content'.
    Optional fields: category, importance. Maximum 500 memories.
    """
    count = import_memories(memories, agent_id=_sanitize_agent_id(agent_id))
    return {"imported": count, "message": f"{count} memories imported"}


# ── Resources ────────────────────────────────────────────────────────────────


@mcp.resource("kore://health")
def health_resource() -> str:
    """Kore server health status."""
    from . import config
    from .repository import _embeddings_available

    return f"Kore v{config.VERSION} — semantic_search={'enabled' if _embeddings_available() else 'disabled'}"


# ── Entry point ──────────────────────────────────────────────────────────────


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Kore MCP Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http", "sse"],
        default="stdio",
        help="Transport protocol (default: stdio)",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host per HTTP transport (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8766,
        help="Porta per HTTP transport (default: 8766)",
    )
    args = parser.parse_args()

    if args.transport in ("streamable-http", "sse"):
        mcp.run(transport=args.transport, host=args.host, port=args.port)
    else:
        mcp.run()


if __name__ == "__main__":
    main()
