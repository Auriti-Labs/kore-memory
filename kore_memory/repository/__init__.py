"""
Kore — Repository package.
Re-exports all public functions for backward compatibility.

The monolithic repository.py has been split into:
- memory.py   — CRUD operations (save, get, update, delete, batch, import/export)
- search.py   — Search operations (FTS5, semantic, tag, timeline)
- lifecycle.py — Decay, cleanup, archive, restore
- graph.py     — Tags and relations between memories
- sessions.py  — Session management
"""

# ruff: noqa: F401 — re-exports for backward compatibility
from .graph import (
    add_relation,
    add_tags,
    extract_subgraph,
    get_degree_centrality,
    get_relations,
    get_tags,
    remove_tags,
    traverse_graph,
)
from .lifecycle import (
    _compress_lock,
    _decay_lock,
    archive_memory,
    cleanup_expired,
    get_archived,
    restore_memory,
    run_decay_pass,
)
from .memory import (
    _embeddings_available,
    delete_memory,
    export_memories,
    get_memory,
    get_memory_history,
    get_stats,
    import_memories,
    list_agents,
    save_memory,
    save_memory_batch,
    update_memory,
)
from .search import (
    _count_active_memories,
    _row_to_record,
    _sanitize_fts_query,
    get_timeline,
    search_by_tag,
    search_memories,
)
from .sessions import (
    create_session,
    delete_session,
    end_session,
    get_session_memories,
    get_session_summary,
    list_sessions,
)

__all__ = [
    # Memory
    "_embeddings_available",
    "save_memory",
    "save_memory_batch",
    "update_memory",
    "get_memory",
    "get_memory_history",
    "delete_memory",
    "export_memories",
    "import_memories",
    "get_stats",
    "list_agents",
    # Search
    "search_memories",
    "get_timeline",
    "search_by_tag",
    "_count_active_memories",
    "_row_to_record",
    "_sanitize_fts_query",
    # Lifecycle
    "run_decay_pass",
    "cleanup_expired",
    "archive_memory",
    "restore_memory",
    "get_archived",
    "_decay_lock",
    "_compress_lock",
    # Graph
    "add_tags",
    "remove_tags",
    "get_tags",
    "add_relation",
    "get_relations",
    "traverse_graph",
    "extract_subgraph",
    "get_degree_centrality",
    # Sessions
    "create_session",
    "list_sessions",
    "get_session_memories",
    "end_session",
    "delete_session",
    "get_session_summary",
]
