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

import atexit
import logging
import re as _re
import threading
import time as _time
from datetime import datetime

from mcp.server.fastmcp import FastMCP

from . import config as _cfg
from .database import init_db
from .models import MemorySaveRequest, MemoryUpdateRequest
from .repository import (
    add_relation,
    add_tags,
    cleanup_expired,
    create_session,
    delete_memory,
    end_session,
    export_memories,
    get_timeline,
    import_memories,
    run_decay_pass,
    save_memory,
    search_by_tag,
    search_memories,
    update_memory,
)

logger = logging.getLogger(__name__)

# Timestamp di avvio per il calcolo dell'uptime e dell'ID sessione
_SERVER_START_TIME = _time.monotonic()
_SESSION_TIMESTAMP = datetime.now().strftime("%Y%m%d-%H%M%S")

# Initialize DB before any operation
init_db()

mcp = FastMCP(
    "Kore Memory",
    json_response=True,
)

_SAFE_AGENT_RE = _re.compile(r"[^a-zA-Z0-9_\-]")

# ── Auto-Session ─────────────────────────────────────────────────────────────

# Sessioni attive per agent_id — create in modo lazy al primo save
_agent_sessions: dict[str, str] = {}
_session_lock = threading.Lock()


def _get_or_create_session(agent_id: str) -> str:
    """Restituisce la session_id corrente per l'agent, creandola se necessario."""
    if agent_id in _agent_sessions:
        return _agent_sessions[agent_id]
    with _session_lock:
        # Double-check dopo aver acquisito il lock
        if agent_id in _agent_sessions:
            return _agent_sessions[agent_id]
        session_id = f"kore-mcp-{agent_id}-{_SESSION_TIMESTAMP}"
        ts = _SESSION_TIMESTAMP
        date_str = f"{ts[:4]}-{ts[4:6]}-{ts[6:8]} {ts[9:11]}:{ts[11:13]}:{ts[13:15]}"
        title = f"MCP — {date_str}"
        try:
            create_session(session_id, agent_id=agent_id, title=title)
        except Exception:
            pass  # DB potrebbe già avere la sessione (restart rapido)
        _agent_sessions[agent_id] = session_id
        logger.info("Auto-sessione creata: %s (agent=%s)", session_id, agent_id)
        return session_id


def _close_all_sessions() -> None:
    """Chiude tutte le sessioni aperte all'uscita del server MCP."""
    for agent_id, session_id in _agent_sessions.items():
        try:
            end_session(session_id, agent_id=agent_id)
            logger.info("Sessione chiusa: %s (agent=%s)", session_id, agent_id)
        except Exception:
            pass


atexit.register(_close_all_sessions)


def _sanitize_agent_id(agent_id: str) -> str:
    """Sanitize agent_id: only alphanumeric characters, dashes and underscores, max 64 chars."""
    safe = _SAFE_AGENT_RE.sub("", agent_id)
    return (safe or "default")[:64]


# ── Helper ───────────────────────────────────────────────────────────────────


def _error(msg: str) -> dict:
    """Formatta un errore come dict per i tool MCP (non solleva eccezioni)."""
    logger.error("MCP tool error: %s", msg)
    return {"error": msg}


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
    safe_agent = _sanitize_agent_id(agent_id)
    session_id = _get_or_create_session(safe_agent)
    req = MemorySaveRequest(content=content, category=category, importance=importance or None)
    mem_id, imp, _ = save_memory(req, agent_id=safe_agent, session_id=session_id)
    return {"id": mem_id, "importance": imp, "session_id": session_id, "message": "Memory saved"}


@mcp.tool()
def memory_search(
    query: str,
    limit: int = 5,
    category: str = "",
    semantic: bool = True,
    task: str = "",
    ranking_profile: str = "default",
    agent_id: str = "default",
) -> dict:
    """
    Search memory. Supports semantic (embedding) and full-text search.
    Returns the most relevant memories sorted by score.
    Leave category empty to search across all categories.
    Pass task to improve ranking with task_relevance signal (Wave 2).
    ranking_profile: "default" or "coding" (optimized for software development tasks).
    """
    results, next_cursor, total_count, _excluded = search_memories(
        query=query,
        limit=limit,
        category=category or None,
        semantic=semantic,
        agent_id=_sanitize_agent_id(agent_id),
        task=task,
        ranking_profile=ranking_profile,
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
                "status": r.status,
                "conditions": r.conditions,
                "created_at": str(r.created_at),
            }
            for r in results
        ],
        "total": total_count,
        "has_more": next_cursor is not None,
        "ranking_profile": ranking_profile,
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
    safe_agent = _sanitize_agent_id(agent_id)
    session_id = _get_or_create_session(safe_agent)
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
            mem_id, imp, _ = save_memory(req, agent_id=safe_agent, session_id=session_id)
            saved.append({"id": mem_id, "importance": imp})
        except Exception:
            errors += 1
    return {"saved": saved, "total": len(saved), "errors": errors, "session_id": session_id}


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


# ── Coding Memory Mode (Issue #012) ──────────────────────────────────────────


@mcp.tool()
def memory_save_decision(
    content: str,
    rationale: str = "",
    alternatives_considered: str = "",
    decided_by: str = "",
    repo: str = "",
    agent_id: str = "default",
) -> dict:
    """
    Save an architectural decision (ADR) with structured metadata.
    Use for technology choices, design patterns, infrastructure decisions.
    The memory is automatically categorized as architectural_decision (semantic type).

    Example: memory_save_decision(
        content="Usiamo PostgreSQL invece di MySQL",
        rationale="Supporto migliore per JSONB e query avanzate",
        alternatives_considered="MySQL, SQLite, MongoDB",
        decided_by="team-backend",
        repo="my-project",
    )
    """
    metadata = {
        "rationale": rationale,
        "alternatives_considered": alternatives_considered,
        "decided_by": decided_by,
        "still_valid": True,
    }
    sanitized = _sanitize_agent_id(f"{agent_id}/{repo}" if repo else agent_id)
    session_id = _get_or_create_session(sanitized)
    req = MemorySaveRequest(
        content=content,
        category="architectural_decision",
        importance=4,
        metadata=metadata,
    )
    mem_id, imp, conflicts = save_memory(req, agent_id=sanitized, session_id=session_id)
    return {
        "id": mem_id,
        "importance": imp,
        "category": "architectural_decision",
        "memory_type": "semantic",
        "conflicts_detected": conflicts,
        "message": "Decision saved",
    }


@mcp.tool()
def memory_get_runbook(
    trigger: str = "",
    component: str = "",
    repo: str = "",
    agent_id: str = "default",
    limit: int = 5,
) -> dict:
    """
    Retrieve runbooks matching a trigger or component.
    Runbooks are procedural memories for operational tasks (deploy, rollback, etc.).

    Example: memory_get_runbook(trigger="deploy failed", component="api-gateway")
    """
    sanitized = _sanitize_agent_id(f"{agent_id}/{repo}" if repo else agent_id)
    query = " ".join(filter(None, [trigger, component])) or "*"
    results, _, total, _excluded = search_memories(
        query=query,
        limit=limit,
        category="runbook",
        semantic=False,
        agent_id=sanitized,
    )
    return {
        "results": [
            {
                "id": r.id,
                "content": r.content,
                "score": r.score,
                "decay_score": r.decay_score,
            }
            for r in results
        ],
        "total": total,
    }


@mcp.tool()
def memory_log_regression(
    content: str,
    introduced_in: str = "",
    fixed_in: str = "",
    test_ref: str = "",
    repo: str = "",
    agent_id: str = "default",
) -> dict:
    """
    Log a regression note: track when a bug was introduced and fixed.
    Helps prevent the same regression from recurring in future versions.

    Example: memory_log_regression(
        content="Race condition nel pool di connessioni SQLite",
        introduced_in="v1.2.0",
        fixed_in="v1.2.1",
        test_ref="tests/test_database.py::test_concurrent_access",
    )
    """
    metadata = {
        "introduced_in": introduced_in,
        "fixed_in": fixed_in,
        "test_ref": test_ref,
    }
    sanitized = _sanitize_agent_id(f"{agent_id}/{repo}" if repo else agent_id)
    session_id = _get_or_create_session(sanitized)
    req = MemorySaveRequest(
        content=content,
        category="regression_note",
        importance=4,
        metadata=metadata,
    )
    mem_id, imp, conflicts = save_memory(req, agent_id=sanitized, session_id=session_id)
    return {
        "id": mem_id,
        "importance": imp,
        "category": "regression_note",
        "memory_type": "episodic",
        "conflicts_detected": conflicts,
        "message": "Regression logged",
    }


@mcp.tool()
def memory_log_root_cause(
    content: str,
    symptom: str = "",
    affected_component: str = "",
    fix_applied: str = "",
    repo: str = "",
    agent_id: str = "default",
) -> dict:
    """
    Log a root cause analysis for a bug or incident.
    Use after investigating an issue to record WHY it happened and how it was resolved.
    The memory is automatically categorized as root_cause (episodic type).

    Example: memory_log_root_cause(
        content="Il watcher non cancellava i timer pendenti al shutdown, causando thread leak",
        symptom="CPU spike al riavvio del server",
        affected_component="filesystem_watcher",
        fix_applied="Aggiunto cancel() di tutti i _timers in __del__ e stop_all",
        repo="kore-memory",
    )
    """
    metadata = {
        "symptom": symptom,
        "affected_component": affected_component,
        "fix_applied": fix_applied,
    }
    sanitized = _sanitize_agent_id(f"{agent_id}/{repo}" if repo else agent_id)
    session_id = _get_or_create_session(sanitized)
    req = MemorySaveRequest(
        content=content,
        category="root_cause",
        importance=4,
        metadata=metadata,
    )
    mem_id, imp, conflicts = save_memory(req, agent_id=sanitized, session_id=session_id)
    return {
        "id": mem_id,
        "importance": imp,
        "category": "root_cause",
        "memory_type": "episodic",
        "conflicts_detected": conflicts,
        "message": "Root cause logged",
    }


@mcp.tool()
def memory_get_context(
    task: str,
    budget_tokens: int = 2000,
    categories: str = "",
    ranking_profile: str = "default",
    agent_id: str = "",
) -> dict:
    """
    Assemble a context package for the current task within a token budget.
    Returns the most relevant memories structured for prompt injection.

    Parameters:
    - task: description of the current task (used for task_relevance ranking)
    - budget_tokens: maximum tokens to use (default 2000, max 32000)
    - categories: comma-separated list of categories to include (empty = all)
      e.g. "facts,decisions,architectural_decision"
    - ranking_profile: "default" or "coding" (optimized for software development)
    - agent_id: agent namespace (empty = "default")

    Invariants (Context Assembly Contract):
    - budget_tokens_used is ALWAYS ≤ budget_tokens_requested
    - Memories with confidence < 0.5 are excluded by default
    - Unresolved conflicts are surfaced in conflicts[]
    - If embedder unavailable: fallback to FTS5, degraded=True
    """
    from .context_assembler import assemble_context
    from .models import ContextAssembleRequest

    cat_list = [c.strip() for c in categories.split(",") if c.strip()] if categories else []
    sanitized = _sanitize_agent_id(agent_id) if agent_id else "default"

    req = ContextAssembleRequest(
        task=task,
        budget_tokens=budget_tokens,
        categories=cat_list,
        ranking_profile=ranking_profile,
    )
    response = assemble_context(req, agent_id=sanitized)

    return {
        "task": response.task,
        "budget_tokens_requested": response.budget_tokens_requested,
        "budget_tokens_used": response.budget_tokens_used,
        "total_memories": response.total_memories,
        "ranking_profile": response.ranking_profile,
        "degraded": response.degraded,
        "memories": [
            {
                "id": m.id,
                "content": m.content,
                "category": m.category,
                "importance": m.importance,
                "score": m.score,
                "tokens_estimated": m.tokens_estimated,
                "status": m.status,
                "conditions": m.conditions,
            }
            for m in response.memories
        ],
        "conflicts": response.conflicts,
    }


@mcp.tool()
def memory_explain(
    memory_id: str,
    agent_id: str = "",
) -> dict:
    """
    Full analysis of a single memory: status, conditions, score breakdown,
    unresolved conflicts, supersession chain, tags, provenance.
    Useful for debugging why a memory was or wasn't recalled.

    Parameters:
    - memory_id: numeric ID of the memory (as string to avoid anyOf schema)
    - agent_id: agent namespace (empty = "default")
    """
    from .database import get_connection
    from .ranking import compute_score
    from .repository import get_memory
    from .repository.search import _load_conflicted_ids

    sanitized = _sanitize_agent_id(agent_id) if agent_id else "default"
    try:
        mid = int(memory_id)
    except ValueError:
        return {"error": f"Invalid memory_id: {memory_id!r}"}

    memory = get_memory(mid, agent_id=sanitized)
    if not memory:
        return {"error": f"Memory {mid} not found for agent {sanitized!r}"}

    with get_connection() as conn:
        row_extra = conn.execute(
            "SELECT access_count, last_accessed FROM memories WHERE id = ? AND agent_id = ?",
            (mid, sanitized),
        ).fetchone()
        conflict_rows = conn.execute(
            """
            SELECT id, conflict_type, resolved_at, detected_at
            FROM memory_conflicts
            WHERE (memory_a_id = ? OR memory_b_id = ?) AND agent_id = ?
            ORDER BY detected_at DESC LIMIT 10
            """,
            (mid, mid, sanitized),
        ).fetchall()
        chain: list[int] = []
        curr = memory.supersedes_id
        seen: set[int] = {mid}
        while curr and curr not in seen and len(chain) < 10:
            chain.append(curr)
            seen.add(curr)
            prev = conn.execute("SELECT supersedes_id FROM memories WHERE id = ?", (curr,)).fetchone()
            curr = prev[0] if prev else None
        tag_rows = conn.execute("SELECT tag FROM memory_tags WHERE memory_id = ? ORDER BY tag", (mid,)).fetchall()

    conflicted_ids = _load_conflicted_ids([mid], sanitized)
    compute_score(memory, conflict_ids=conflicted_ids, explain=True)

    return {
        "id": memory.id,
        "status": memory.status,
        "conditions": memory.conditions,
        "current_score": memory.score,
        "score_breakdown": memory.explain or {},
        "access_count": row_extra["access_count"] if row_extra else 0,
        "last_accessed": row_extra["last_accessed"] if row_extra else None,
        "conflicts": [
            {
                "conflict_id": r["id"],
                "conflict_type": r["conflict_type"],
                "resolved": r["resolved_at"] is not None,
                "detected_at": r["detected_at"],
            }
            for r in conflict_rows
        ],
        "supersession_chain": chain,
        "tags": [r["tag"] for r in tag_rows],
        "memory_type": memory.memory_type,
        "confidence": memory.confidence,
    }


# ── Session Consolidation (M3a) ──────────────────────────────────────────────


@mcp.tool()
def memory_consolidate(
    agent_id: str = "",
    session_id: str = "",
) -> dict:
    """
    Consolidate session memories into episodic summaries.
    If session_id is provided, consolidates only that session.
    Otherwise, consolidates all eligible ended sessions for the agent.
    """
    aid = _sanitize_agent_id(agent_id)
    from .consolidation import consolidate_agent, consolidate_session

    if session_id.strip():
        return consolidate_session(session_id.strip(), aid)
    return consolidate_agent(aid)


# ── Resources ────────────────────────────────────────────────────────────────


@mcp.resource("kore://health")
def health_resource() -> str:
    """Kore server health status."""
    from . import config
    from .repository import _embeddings_available

    return f"Kore v{config.VERSION} — semantic_search={'enabled' if _embeddings_available() else 'disabled'}"


# ── Bearer Auth Middleware (streamable-http remoto) ──────────────────────────


def _wrap_bearer_auth(app, token: str):
    """
    Aggiunge un middleware Bearer token all'app Starlette.
    /mcp/health è esente per permettere health-check senza credenziali.
    """
    import secrets as _secrets

    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.requests import Request
    from starlette.responses import JSONResponse

    class BearerAuthMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            # Health check esente da autenticazione
            if request.url.path == "/mcp/health":
                return await call_next(request)

            auth = request.headers.get("Authorization", "")
            if not auth.startswith("Bearer "):
                return JSONResponse(
                    {
                        "error": "Missing Bearer token",
                        "hint": "Authorization: Bearer <KORE_MCP_TOKEN>",
                    },
                    status_code=401,
                    headers={"WWW-Authenticate": "Bearer"},
                )

            provided = auth[len("Bearer ") :]
            if not _secrets.compare_digest(provided.encode(), token.encode()):
                return JSONResponse({"error": "Invalid token"}, status_code=403)

            return await call_next(request)

    app.add_middleware(BearerAuthMiddleware)
    return app


# ── Health endpoint (streamable-http) ────────────────────────────────────────


def _add_health_route() -> None:
    """
    Registra GET /mcp/health sul transport streamable-http.
    Risponde con {status, uptime_seconds, version}.
    Viene chiamato solo quando il transport HTTP è attivo.
    """
    try:
        from starlette.requests import Request
        from starlette.responses import JSONResponse

        @mcp.custom_route("/mcp/health", methods=["GET"])
        async def mcp_health(_req: Request) -> JSONResponse:
            uptime = round(_time.monotonic() - _SERVER_START_TIME, 1)
            return JSONResponse(
                {
                    "status": "ok",
                    "uptime_seconds": uptime,
                    "version": _cfg.VERSION,
                }
            )
    except Exception as exc:
        logger.warning("Impossibile registrare /mcp/health: %s", exc)


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
        default=_cfg.MCP_PORT,
        help="Porta per HTTP transport (default: 8766, override: KORE_MCP_PORT)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [kore-mcp] %(levelname)s %(message)s",
    )

    if args.transport in ("streamable-http", "sse"):
        token = _cfg.MCP_TOKEN
        timeout = _cfg.MCP_TIMEOUT_SECONDS
        logger.info(
            "Avvio MCP server transport=%s host=%s port=%d timeout=%ss auth=%s",
            args.transport,
            args.host,
            args.port,
            timeout,
            "bearer" if token else "none (locale)",
        )
        _add_health_route()

        if token:
            # Token configurato → costruiamo l'app manualmente e aggiungiamo
            # il middleware Bearer prima di passare a uvicorn
            import uvicorn

            app = mcp.streamable_http_app() if args.transport == "streamable-http" else mcp.sse_app()
            _wrap_bearer_auth(app, token)
            try:
                uvicorn.run(app, host=args.host, port=args.port)
            except KeyboardInterrupt:
                logger.info("MCP server fermato (KeyboardInterrupt)")
            except Exception as exc:
                logger.error("MCP server crash: %s", exc, exc_info=True)
                raise
        else:
            # No token: only allow localhost binding
            if args.host not in ("127.0.0.1", "localhost", "::1"):
                logger.error(
                    "Refusing to bind to %s without KORE_MCP_TOKEN. "
                    "Set KORE_MCP_TOKEN or use --host 127.0.0.1.",
                    args.host,
                )
                raise SystemExit(1)
            try:
                mcp.run(transport=args.transport, host=args.host, port=args.port)
            except KeyboardInterrupt:
                logger.info("MCP server fermato (KeyboardInterrupt)")
            except Exception as exc:
                logger.error("MCP server crash: %s", exc, exc_info=True)
                raise
    else:
        mcp.run()


if __name__ == "__main__":
    main()
