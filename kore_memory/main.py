"""
Kore — FastAPI application
Memory layer with decay, auto-scoring, compression, semantic search, and auth.
"""

import re as _re
import secrets

# ── Rate limiter in-memory ───────────────────────────────────────────────────
import threading as _rl_threading
import time
from collections import defaultdict
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response, StreamingResponse

from . import config
from .auth import get_agent_id, require_auth
from .dashboard import get_dashboard_html
from .database import init_db
from .models import (
    ACLGrantRequest,
    ACLResponse,
    AgentListResponse,
    AgentRecord,
    AnalyticsResponse,
    ArchiveResponse,
    AuditResponse,
    AutoTuneResponse,
    BatchSaveRequest,
    BatchSaveResponse,
    CleanupExpiredResponse,
    CompressRunResponse,
    ContextAssembleRequest,
    ContextAssembleResponse,
    DecayRunResponse,
    EntityListResponse,
    EntityRecord,
    GDPRDeleteResponse,
    GraphTraverseResponse,
    HubDetectionResponse,
    HubNodeRecord,
    LifecyclePolicyRecord,
    MemoryExplainResponse,
    MemoryExportResponse,
    MemoryImportRequest,
    MemoryImportResponse,
    MemoryRecord,
    MemorySaveRequest,
    MemorySaveResponse,
    MemorySearchResponse,
    MemoryUpdateRequest,
    OverlayFileRecord,
    OverlayFilesResponse,
    OverlayIndexRequest,
    OverlayIndexResponse,
    OverlayWatcherRecord,
    OverlayWatchersResponse,
    OverlayWatchRequest,
    OverlayWatchResponse,
    PluginListResponse,
    PolicyListResponse,
    PolicyToggleResponse,
    RankingProfileRequest,
    RelationRecord,
    RelationRequest,
    RelationResponse,
    ScoringStatsResponse,
    SessionCreateRequest,
    SessionDeleteResponse,
    SessionResponse,
    SessionSummaryResponse,
    SharedMemoriesResponse,
    SubgraphResponse,
    SummarizeResponse,
    TagRequest,
    TagResponse,
)
from .repository import (
    add_relation,
    add_tags,
    archive_memory,
    cleanup_expired,
    create_session,
    delete_memory,
    delete_session,
    end_session,
    export_memories,
    extract_subgraph,
    get_archived,
    get_degree_centrality,
    get_memory,
    get_memory_history,
    get_relations,
    get_session_memories,
    get_session_summary,
    get_tags,
    get_timeline,
    import_memories,
    list_agents,
    list_sessions,
    remove_tags,
    restore_memory,
    run_decay_pass,
    save_memory,
    save_memory_batch,
    search_by_tag,
    search_memories,
    traverse_graph,
    update_memory,
)

_rate_buckets: dict[str, list[float]] = defaultdict(list)
_rate_lock = _rl_threading.Lock()
_rate_last_cleanup = 0.0


_SESSION_ID_RE = _re.compile(r"^[a-zA-Z0-9_\-\.]{1,128}$")


def _validate_session_id(raw: str | None) -> str | None:
    """Validate and sanitize X-Session-Id header. None if absent or invalid."""
    if not raw:
        return None
    raw = raw.strip()
    if not _SESSION_ID_RE.match(raw):
        raise HTTPException(status_code=400, detail="X-Session-Id contains invalid characters")
    return raw


def _get_client_ip(request: Request) -> str:
    """Extract client IP. Ignores X-Forwarded-For in local-only mode to prevent spoofing."""
    # In local-only mode, use the raw socket IP only — prevents
    # spoofing via X-Forwarded-For: 127.0.0.1 to bypass auth/rate-limit
    if config.LOCAL_ONLY:
        return request.client.host if request.client else "unknown"
    # Behind a trusted reverse proxy, read the first IP from the chain
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()
    return request.client.host if request.client else "unknown"


def _check_rate_limit(client_ip: str, path: str) -> None:
    """Check rate limit for IP + path. Raises HTTPException 429 if exceeded."""
    limit_conf = config.RATE_LIMITS.get(path)
    if not limit_conf:
        return
    max_requests, window = limit_conf
    now = time.monotonic()
    key = f"{client_ip}:{path}"

    with _rate_lock:
        # Periodic cleanup of stale buckets (every 60s) — prevents memory leak
        global _rate_last_cleanup
        if now - _rate_last_cleanup > 60:
            stale_keys = [
                k for k, timestamps in _rate_buckets.items() if not timestamps or now - timestamps[-1] > window
            ]
            for k in stale_keys:
                del _rate_buckets[k]
            _rate_last_cleanup = now

        # Discard expired requests for this bucket
        _rate_buckets[key] = [ts for ts in _rate_buckets[key] if now - ts < window]

        if len(_rate_buckets[key]) >= max_requests:
            raise HTTPException(status_code=429, detail="Rate limit exceeded. Retry later.")

        _rate_buckets[key].append(now)


# ── Security headers middleware ──────────────────────────────────────────────


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    _API_CSP = "default-src 'none'; frame-ancestors 'none'"

    @staticmethod
    def _dashboard_csp(nonce: str) -> str:
        """Build CSP for dashboard with per-request nonce instead of unsafe-inline scripts."""
        return (
            "default-src 'self'; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            f"script-src 'nonce-{nonce}'; "
            "connect-src 'self'; "
            "img-src 'self' data:; "
            "frame-ancestors 'none'"
        )

    async def dispatch(self, request: Request, call_next) -> Response:
        # Generate a per-request nonce and store it for the dashboard endpoint
        nonce = secrets.token_urlsafe(16)
        request.state.csp_nonce = nonce

        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "0"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        # CSP with nonce for the dashboard, restrictive for APIs
        if request.url.path == "/dashboard":
            response.headers["Content-Security-Policy"] = self._dashboard_csp(nonce)
        else:
            response.headers["Content-Security-Policy"] = self._API_CSP
        return response


# ── App factory ──────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # Initialize API key (auto-generate if missing)
    from .auth import get_or_create_api_key

    get_or_create_api_key()
    # Enable audit log if configured
    if config.AUDIT_LOG:
        from .audit import register_audit_handler

        register_audit_handler()
    yield
    # Graceful shutdown: ferma i watcher attivi
    from .filesystem_watcher import stop_all_watchers

    stop_all_watchers()
    # Graceful shutdown: close the SQLite connection pool
    from .database import _pool

    _pool.clear()


app = FastAPI(
    title="Kore",
    description=(
        "The memory layer that thinks like a human: remembers what matters, forgets what doesn't, and never calls home."
    ),
    version=config.VERSION,
    lifespan=lifespan,
)

# CORS — configurable origins via env, restrictive by default
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["X-Kore-Key", "X-Agent-Id", "Content-Type"],
)

# Security headers on all responses
app.add_middleware(SecurityHeadersMiddleware)


# Global handler for unhandled exceptions — no stack trace exposed to client
@app.exception_handler(Exception)
async def _global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    import logging

    logging.error("Unhandled error: %s", exc, exc_info=True)
    return JSONResponse(status_code=500, content={"error": "Internal server error"})


# Shared auth dependencies
_Auth = Depends(require_auth)
_Agent = Depends(get_agent_id)


# ── Core endpoints ────────────────────────────────────────────────────────────


@app.post("/save", response_model=MemorySaveResponse, status_code=201)
def save(
    request: Request,
    req: MemorySaveRequest,
    _: str = _Auth,
    agent_id: str = _Agent,
) -> MemorySaveResponse:
    """Save a memory scoped to the requesting agent. Importance is auto-scored if omitted.
    Use X-Session-Id header to associate the memory with a conversation session."""
    _check_rate_limit(_get_client_ip(request), "/save")
    session_id = _validate_session_id(request.headers.get("X-Session-Id"))
    memory_id, importance, conflicts = save_memory(req, agent_id=agent_id, session_id=session_id)
    return MemorySaveResponse(
        id=memory_id,
        importance=importance,
        conflicts_detected=conflicts,
        superseded_id=req.supersedes_id,
    )


@app.post("/save/batch", response_model=BatchSaveResponse, status_code=201)
def save_batch(
    request: Request,
    req: BatchSaveRequest,
    _: str = _Auth,
    agent_id: str = _Agent,
) -> BatchSaveResponse:
    """Save multiple memories in a single request (max 100). Uses batch embedding."""
    _check_rate_limit(_get_client_ip(request), "/save")
    results = save_memory_batch(req.memories, agent_id=agent_id)
    saved = [MemorySaveResponse(id=mid, importance=imp) for mid, imp, *_ in results]
    return BatchSaveResponse(saved=saved, total=len(saved))


@app.get("/search", response_model=MemorySearchResponse)
def search(
    request: Request,
    q: str = Query(..., min_length=1, max_length=1000, description="Search query (any language)"),
    limit: int = Query(5, ge=1, le=20),
    cursor: str | None = Query(None, description="Opaque pagination cursor"),
    category: str | None = Query(None),
    semantic: bool = Query(True),
    task: str = Query("", description="Descrizione task corrente per task_relevance scoring"),
    ranking_profile: str = Query("default", description="Profilo ranking: default | coding"),
    explain: bool = Query(False, description="Includi score breakdown per ogni risultato"),
    _: str = _Auth,
    agent_id: str = _Agent,
    # Deprecated params for backwards compatibility
    offset: int = Query(0, ge=0, deprecated=True, description="Deprecated: use cursor"),
) -> MemorySearchResponse:
    """Semantic search with cursor pagination, optional task_relevance and explain."""
    _check_rate_limit(_get_client_ip(request), "/search")

    # Parse cursor (base64 encoded tuple di decay_score, id)
    cursor_tuple = None
    if cursor:
        try:
            import base64
            import json

            decoded = base64.b64decode(cursor).decode("utf-8")
            cursor_tuple = tuple(json.loads(decoded))
        except Exception:
            raise HTTPException(400, "Invalid cursor format") from None

    results, next_cursor, total_count, excluded = search_memories(
        query=q,
        limit=limit,
        category=category,
        semantic=semantic,
        agent_id=agent_id,
        cursor=cursor_tuple,
        task=task,
        ranking_profile=ranking_profile,
        explain=explain,
    )

    # Encode next cursor
    cursor_str = None
    if next_cursor:
        import base64
        import json

        cursor_str = base64.b64encode(json.dumps(next_cursor).encode("utf-8")).decode("utf-8")

    # Normalizza il nome del profilo per il response (default → default_v1)
    _profile_display = "default_v1" if ranking_profile in ("default", "default_v1") else ranking_profile

    return MemorySearchResponse(
        results=results,
        total=total_count,
        cursor=cursor_str,
        has_more=next_cursor is not None,
        excluded=excluded if explain else [],
        ranking_profile=_profile_display,
        offset=offset,
    )


@app.get("/timeline", response_model=MemorySearchResponse)
def timeline(
    request: Request,
    subject: str = Query(..., min_length=1),
    limit: int = Query(20, ge=1, le=50),
    cursor: str | None = Query(None, description="Opaque pagination cursor"),
    _: str = _Auth,
    agent_id: str = _Agent,
    offset: int = Query(0, ge=0, deprecated=True, description="Deprecated: use cursor"),
) -> MemorySearchResponse:
    """Chronological memory history for a subject, scoped to agent, with cursor-based pagination."""
    _check_rate_limit(_get_client_ip(request), "/timeline")

    # Parse cursor
    cursor_tuple = None
    if cursor:
        try:
            import base64
            import json

            decoded = base64.b64decode(cursor).decode("utf-8")
            cursor_tuple = tuple(json.loads(decoded))
        except Exception:
            raise HTTPException(400, "Invalid cursor format") from None

    results, next_cursor, total_count = get_timeline(
        subject=subject,
        limit=limit,
        agent_id=agent_id,
        cursor=cursor_tuple,
    )

    # Encode next cursor
    cursor_str = None
    if next_cursor:
        import base64
        import json

        cursor_str = base64.b64encode(json.dumps(next_cursor).encode("utf-8")).decode("utf-8")

    return MemorySearchResponse(
        results=results,
        total=total_count,
        cursor=cursor_str,
        has_more=next_cursor is not None,
        offset=offset,
    )


@app.get("/memories/{memory_id}", response_model=MemoryRecord)
def get_single(
    memory_id: int,
    _: str = _Auth,
    agent_id: str = _Agent,
) -> MemoryRecord:
    """Get a single memory by ID. Agents can only access their own memories."""
    memory = get_memory(memory_id, agent_id=agent_id)
    if not memory:
        raise HTTPException(status_code=404, detail="Memory not found")
    return memory


@app.get("/explain/memory/{memory_id}", response_model=MemoryExplainResponse)
def explain_memory(
    memory_id: int,
    _: str = _Auth,
    agent_id: str = _Agent,
) -> MemoryExplainResponse:
    """
    Analisi completa di una singola memoria: status, conditions, score breakdown,
    conflitti, catena di supersessioni, tag, provenance. (Wave 2, issue #016)
    """
    memory = get_memory(memory_id, agent_id=agent_id)
    if not memory:
        raise HTTPException(status_code=404, detail="Memory not found")

    from .database import get_connection
    from .ranking import compute_score
    from .repository.search import _load_conflicted_ids

    with get_connection() as conn:
        # access_count e last_accessed
        row_extra = conn.execute(
            "SELECT access_count, last_accessed FROM memories WHERE id = ? AND agent_id = ?",
            (memory_id, agent_id),
        ).fetchone()

        # Conflitti
        conflict_rows = conn.execute(
            """
            SELECT id, conflict_type, resolved_at, detected_at
            FROM memory_conflicts
            WHERE (memory_a_id = ? OR memory_b_id = ?) AND agent_id = ?
            ORDER BY detected_at DESC
            """,
            (memory_id, memory_id, agent_id),
        ).fetchall()

        # Catena supersessioni: risali fino all'origine
        chain: list[int] = []
        curr_id = memory.supersedes_id
        seen: set[int] = {memory_id}
        while curr_id and curr_id not in seen and len(chain) < 20:
            chain.append(curr_id)
            seen.add(curr_id)
            prev = conn.execute("SELECT supersedes_id FROM memories WHERE id = ?", (curr_id,)).fetchone()
            curr_id = prev[0] if prev else None

        # Tag
        tag_rows = conn.execute(
            "SELECT tag FROM memory_tags WHERE memory_id = ? ORDER BY tag",
            (memory_id,),
        ).fetchall()

    # Score breakdown con explain
    conflicted_ids = _load_conflicted_ids([memory_id], agent_id)
    compute_score(memory, conflict_ids=conflicted_ids, explain=True)
    score_breakdown = memory.explain or {}

    from .models import ConflictInfo

    return MemoryExplainResponse(
        id=memory.id,
        status=memory.status,
        conditions=memory.conditions,
        current_score=memory.score,
        score_breakdown=score_breakdown,
        access_count=row_extra["access_count"] if row_extra else 0,
        last_accessed=row_extra["last_accessed"] if row_extra else None,
        conflicts=[
            ConflictInfo(
                conflict_id=r["id"],
                conflict_type=r["conflict_type"],
                resolved=r["resolved_at"] is not None,
                detected_at=r["detected_at"],
                resolved_at=r["resolved_at"],
            )
            for r in conflict_rows
        ],
        supersession_chain=chain,
        tags=[r["tag"] for r in tag_rows],
        provenance=memory.provenance,
        memory_type=memory.memory_type,
        confidence=memory.confidence,
        valid_from=memory.valid_from,
        valid_to=memory.valid_to,
    )


@app.post("/context/assemble", response_model=ContextAssembleResponse)
def context_assemble(
    req: ContextAssembleRequest,
    request: Request,
    _: str = _Auth,
    agent_id: str = _Agent,
) -> ContextAssembleResponse:
    """
    Context Assembly Engine — costruisce un context package pronto per l'iniezione
    nel prompt di un agente, rispettando il budget token specificato.
    (Wave 2, issue #017)
    """
    _check_rate_limit(_get_client_ip(request), "/context/assemble")

    from .context_assembler import assemble_context

    return assemble_context(req, agent_id=agent_id)


@app.get("/memories/{memory_id}/history", response_model=list[MemoryRecord])
def get_history(
    memory_id: int,
    _: str = _Auth,
    agent_id: str = _Agent,
) -> list[MemoryRecord]:
    """
    Restituisce la catena di supersessioni per una memoria, dal predecessore più vecchio
    al nodo di partenza. Utile per navigare l'evoluzione di una memoria nel tempo.
    """
    chain = get_memory_history(memory_id, agent_id=agent_id)
    if not chain:
        raise HTTPException(status_code=404, detail="Memory not found")
    return chain


@app.put("/memories/{memory_id}", response_model=MemorySaveResponse)
def update(
    memory_id: int,
    req: MemoryUpdateRequest,
    _: str = _Auth,
    agent_id: str = _Agent,
) -> MemorySaveResponse:
    """Update a memory's content, category, or importance. Agents can only update their own memories."""
    if not update_memory(memory_id, req, agent_id=agent_id):
        raise HTTPException(status_code=404, detail="Memory not found")
    # Fetch the actual importance from DB (req.importance may be None)
    from .database import get_connection

    with get_connection() as conn:
        row = conn.execute(
            "SELECT importance FROM memories WHERE id = ? AND agent_id = ?",
            (memory_id, agent_id),
        ).fetchone()
    real_importance = row["importance"] if row else 1
    return MemorySaveResponse(id=memory_id, importance=real_importance, message="Memory updated")


@app.delete("/memories/{memory_id}", status_code=204)
def delete(
    memory_id: int,
    _: str = _Auth,
    agent_id: str = _Agent,
) -> None:
    """Delete a memory. Agents can only delete their own memories."""
    if not delete_memory(memory_id, agent_id=agent_id):
        raise HTTPException(status_code=404, detail="Memory not found")


# ── Tag endpoints ─────────────────────────────────────────────────────────────


@app.post("/memories/{memory_id}/tags", response_model=TagResponse, status_code=201)
def tag_add(
    memory_id: int,
    req: TagRequest,
    _: str = _Auth,
    agent_id: str = _Agent,
) -> TagResponse:
    """Add tags to a memory."""
    count = add_tags(memory_id, req.tags, agent_id=agent_id)
    tags = get_tags(memory_id, agent_id=agent_id)
    return TagResponse(count=count, tags=tags)


@app.delete("/memories/{memory_id}/tags", response_model=TagResponse)
def tag_remove(
    memory_id: int,
    req: TagRequest,
    _: str = _Auth,
    agent_id: str = _Agent,
) -> TagResponse:
    """Remove tags from a memory."""
    remove_tags(memory_id, req.tags, agent_id=agent_id)
    tags = get_tags(memory_id, agent_id=agent_id)
    return TagResponse(count=len(tags), tags=tags)


@app.get("/memories/{memory_id}/tags", response_model=TagResponse)
def tag_list(
    memory_id: int,
    _: str = _Auth,
    agent_id: str = _Agent,
) -> TagResponse:
    """Return the tags of a memory (only if it belongs to the agent)."""
    tags = get_tags(memory_id, agent_id=agent_id)
    return TagResponse(count=len(tags), tags=tags)


@app.get("/tags/{tag}/memories", response_model=MemorySearchResponse)
def tag_search(
    tag: str,
    limit: int = Query(20, ge=1, le=50),
    _: str = _Auth,
    agent_id: str = _Agent,
) -> MemorySearchResponse:
    """Search memories by tag."""
    results = search_by_tag(tag, agent_id=agent_id, limit=limit)
    return MemorySearchResponse(results=results, total=len(results))


# ── Relation endpoints ───────────────────────────────────────────────────────


@app.post("/memories/{memory_id}/relations", response_model=RelationResponse, status_code=201)
def relation_add(
    memory_id: int,
    req: RelationRequest,
    _: str = _Auth,
    agent_id: str = _Agent,
) -> RelationResponse:
    """Create a relation between two memories (with typed strength and confidence)."""
    add_relation(
        memory_id,
        req.target_id,
        req.relation,
        agent_id=agent_id,
        strength=req.strength,
        confidence=req.confidence,
    )
    relations = get_relations(memory_id, agent_id=agent_id)
    return RelationResponse(
        relations=[RelationRecord(**r) for r in relations],
        total=len(relations),
    )


@app.get("/memories/{memory_id}/relations", response_model=RelationResponse)
def relation_list(
    memory_id: int,
    _: str = _Auth,
    agent_id: str = _Agent,
) -> RelationResponse:
    """Return the relations of a memory."""
    relations = get_relations(memory_id, agent_id=agent_id)
    return RelationResponse(
        relations=[RelationRecord(**r) for r in relations],
        total=len(relations),
    )


# ── Maintenance endpoints ─────────────────────────────────────────────────────


@app.post("/decay/run", response_model=DecayRunResponse)
def decay_run(
    request: Request,
    dry_run: bool = Query(False, description="If true, evaluate policies without applying actions"),
    _: str = _Auth,
    agent_id: str = _Agent,
) -> DecayRunResponse:
    """Recalculate decay scores for agent's memories and run lifecycle policies."""
    _check_rate_limit(_get_client_ip(request), "/decay/run")
    updated, policy_result = run_decay_pass(agent_id=agent_id, dry_run=dry_run)

    return DecayRunResponse(
        updated=updated,
        policies_evaluated=policy_result.evaluated if policy_result else 0,
        policies_archived=policy_result.archived if policy_result else 0,
        policies_flagged=policy_result.flagged if policy_result else 0,
    )


@app.get("/lifecycle/policies", response_model=PolicyListResponse)
def list_policies(
    _: str = _Auth,
    agent_id: str = _Agent,
) -> PolicyListResponse:
    """List all lifecycle policies (global + agent-specific)."""
    import json

    from .database import get_connection

    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, agent_id, name, trigger, action, params_json, enabled, created_at "
            "FROM lifecycle_policies WHERE agent_id = '*' OR agent_id = ? ORDER BY id",
            (agent_id,),
        ).fetchall()

    policies = [
        LifecyclePolicyRecord(
            id=r["id"],
            agent_id=r["agent_id"],
            name=r["name"],
            trigger=r["trigger"],
            action=r["action"],
            params=json.loads(r["params_json"]),
            enabled=bool(r["enabled"]),
            created_at=r["created_at"],
        )
        for r in rows
    ]
    return PolicyListResponse(policies=policies, total=len(policies))


@app.put("/lifecycle/policies/{policy_id}/enabled", response_model=PolicyToggleResponse)
def toggle_policy(
    policy_id: str,
    enabled: bool = Query(..., description="Enable or disable the policy"),
    _: str = _Auth,
) -> PolicyToggleResponse:
    """Enable or disable a lifecycle policy."""
    from .database import get_connection

    with get_connection() as conn:
        cursor = conn.execute(
            "UPDATE lifecycle_policies SET enabled = ? WHERE id = ?",
            (int(enabled), policy_id),
        )
    if cursor.rowcount == 0:
        raise HTTPException(404, f"Policy '{policy_id}' not found")
    return PolicyToggleResponse(
        id=policy_id,
        enabled=enabled,
        message=f"Policy {'enabled' if enabled else 'disabled'}",
    )


@app.post("/compress", response_model=CompressRunResponse)
def compress(
    request: Request,
    _: str = _Auth,
    agent_id: str = _Agent,
) -> CompressRunResponse:
    """Merge similar memories for this agent."""
    _check_rate_limit(_get_client_ip(request), "/compress")
    from .compressor import run_compression

    result = run_compression(agent_id=agent_id)
    return CompressRunResponse(
        clusters_found=result.clusters_found,
        memories_merged=result.memories_merged,
        new_records_created=result.new_records_created,
    )


@app.post("/consolidate")
def consolidate(
    request: Request,
    body: dict | None = None,
    _: str = _Auth,
    agent_id: str = _Agent,
) -> dict:
    """Consolidate session memories into episodic summaries."""
    _check_rate_limit(_get_client_ip(request), "/compress")
    from .consolidation import consolidate_agent, consolidate_session

    if body and body.get("session_id"):
        return consolidate_session(body["session_id"], agent_id)
    return consolidate_agent(agent_id)


@app.post("/cleanup", response_model=CleanupExpiredResponse)
def cleanup(
    _: str = _Auth,
    agent_id: str = _Agent,
) -> CleanupExpiredResponse:
    """Remove expired memories (elapsed TTL) for this agent."""
    removed = cleanup_expired(agent_id=agent_id)
    return CleanupExpiredResponse(removed=removed)


@app.post("/auto-tune", response_model=AutoTuneResponse)
def auto_tune(
    request: Request,
    _: str = _Auth,
    agent_id: str = _Agent,
) -> AutoTuneResponse:
    """Auto-tune memory importance based on access patterns."""
    _check_rate_limit(_get_client_ip(request), "/decay/run")  # share decay rate limit
    from .auto_tuner import run_auto_tune

    result = run_auto_tune(agent_id=agent_id)
    return AutoTuneResponse(**result)


@app.get("/stats/scoring", response_model=ScoringStatsResponse)
def scoring_stats(
    _: str = _Auth,
    agent_id: str = _Agent,
) -> ScoringStatsResponse:
    """Return importance scoring statistics for the agent's memories."""
    from .auto_tuner import get_scoring_stats

    return ScoringStatsResponse(**get_scoring_stats(agent_id=agent_id))


# ── Ranking Profiles per-Agent (#98) ─────────────────────────────────────────


@app.get("/ranking/profiles")
def ranking_profiles_list(
    _: str = _Auth,
    agent_id: str = _Agent,
) -> dict:
    """List all custom ranking profiles for the agent."""
    from .ranking import list_agent_profiles

    profiles = list_agent_profiles(agent_id)
    return {"profiles": [{"agent_id": agent_id, **p} for p in profiles], "total": len(profiles)}


@app.put("/ranking/profiles", status_code=200)
def ranking_profile_save(
    req: "RankingProfileRequest",
    _: str = _Auth,
    agent_id: str = _Agent,
) -> dict:
    """Create or update a custom ranking profile for the agent."""
    from .ranking import save_agent_profile

    try:
        save_agent_profile(agent_id, req.weights, req.profile_name)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    return {"agent_id": agent_id, "profile_name": req.profile_name, "weights": req.weights, "message": "Profile saved"}


@app.delete("/ranking/profiles/{profile_name}", status_code=200)
def ranking_profile_delete(
    profile_name: str,
    _: str = _Auth,
    agent_id: str = _Agent,
) -> dict:
    """Delete a custom ranking profile."""
    from .ranking import delete_agent_profile

    deleted = delete_agent_profile(agent_id, profile_name)
    if not deleted:
        raise HTTPException(status_code=404, detail="Profile not found")
    return {"message": "Profile deleted"}


# ── Backup / Import ──────────────────────────────────────────────────────────


@app.get("/export", response_model=MemoryExportResponse)
def export(
    _: str = _Auth,
    agent_id: str = _Agent,
) -> MemoryExportResponse:
    """Export all active memories for the agent (without embeddings)."""
    data = export_memories(agent_id=agent_id)
    return MemoryExportResponse(memories=data, total=len(data))


@app.post("/import", response_model=MemoryImportResponse, status_code=201)
def import_data(
    req: MemoryImportRequest,
    _: str = _Auth,
    agent_id: str = _Agent,
) -> MemoryImportResponse:
    """Import memories from a previous export."""
    count = import_memories(req.memories, agent_id=agent_id)
    return MemoryImportResponse(imported=count)


# ── Archive endpoints ──────────────────────────────────────────────────────


@app.post("/memories/{memory_id}/archive", response_model=ArchiveResponse, status_code=200)
def archive(memory_id: int, _: str = _Auth, agent_id: str = _Agent) -> ArchiveResponse:
    if not archive_memory(memory_id, agent_id=agent_id):
        raise HTTPException(404, "Memory not found or already archived")
    return ArchiveResponse(success=True, message="Memory archived")


@app.post("/memories/{memory_id}/restore", response_model=ArchiveResponse, status_code=200)
def restore(memory_id: int, _: str = _Auth, agent_id: str = _Agent) -> ArchiveResponse:
    if not restore_memory(memory_id, agent_id=agent_id):
        raise HTTPException(404, "Memory not found or not archived")
    return ArchiveResponse(success=True, message="Memory restored")


@app.get("/archive", response_model=MemorySearchResponse)
def archive_list(limit: int = Query(50, ge=1, le=100), _: str = _Auth, agent_id: str = _Agent) -> MemorySearchResponse:
    results = get_archived(agent_id=agent_id, limit=limit)
    return MemorySearchResponse(results=results, total=len(results))


# ── Session endpoints ─────────────────────────────────────────────────────────


@app.post("/sessions", response_model=SessionResponse, status_code=201)
def session_create(
    req: SessionCreateRequest,
    _: str = _Auth,
    agent_id: str = _Agent,
) -> SessionResponse:
    """Create a new conversation session."""
    result = create_session(req.session_id, agent_id=agent_id, title=req.title)
    if not result:
        raise HTTPException(400, "Failed to create session")
    return SessionResponse(**result, memory_count=0)


@app.get("/sessions", response_model=list[SessionResponse])
def sessions_list(
    limit: int = Query(50, ge=1, le=200),
    _: str = _Auth,
    agent_id: str = _Agent,
) -> list[SessionResponse]:
    """List all sessions for the requesting agent."""
    rows = list_sessions(agent_id=agent_id, limit=limit)
    return [SessionResponse(**r) for r in rows]


@app.get("/sessions/{session_id}/memories", response_model=MemorySearchResponse)
def session_memories(
    session_id: str,
    _: str = _Auth,
    agent_id: str = _Agent,
) -> MemorySearchResponse:
    """Get all memories in a session."""
    results = get_session_memories(session_id, agent_id=agent_id)
    return MemorySearchResponse(results=results, total=len(results))


@app.get("/sessions/{session_id}/summary", response_model=SessionSummaryResponse)
def session_summary(
    session_id: str,
    _: str = _Auth,
    agent_id: str = _Agent,
) -> SessionSummaryResponse:
    """Get aggregated summary of a session (no LLM)."""
    summary = get_session_summary(session_id, agent_id=agent_id)
    if not summary:
        raise HTTPException(404, "Session not found")
    return SessionSummaryResponse(**summary)


@app.post("/sessions/{session_id}/end", response_model=ArchiveResponse)
def session_end(
    session_id: str,
    _: str = _Auth,
    agent_id: str = _Agent,
) -> ArchiveResponse:
    """Mark a session as ended."""
    if not end_session(session_id, agent_id=agent_id):
        raise HTTPException(404, "Session not found or already ended")
    return ArchiveResponse(success=True, message="Session ended")


@app.delete("/sessions/{session_id}", response_model=SessionDeleteResponse, status_code=200)
def session_delete(
    session_id: str,
    _: str = _Auth,
    agent_id: str = _Agent,
) -> SessionDeleteResponse:
    """Delete a session. Memories are unlinked but not deleted."""
    unlinked = delete_session(session_id, agent_id=agent_id)
    return SessionDeleteResponse(success=True, unlinked_memories=unlinked)


# ── Entity extraction ─────────────────────────────────────────────────────────


@app.get("/entities", response_model=EntityListResponse)
def entities_list(
    type: str | None = Query(
        None,
        description="Filter by entity type (person, org, email, url, date, money, location, product)",
    ),
    limit: int = Query(50, ge=1, le=200),
    _: str = _Auth,
    agent_id: str = _Agent,
) -> EntityListResponse:
    """List extracted entities from memory tags. Requires KORE_ENTITY_EXTRACTION=1."""
    from .integrations.entities import search_entities

    results = search_entities(agent_id, entity_type=type, limit=limit)
    return EntityListResponse(
        entities=[EntityRecord(**r) for r in results],
        total=len(results),
    )


# ── Graph RAG ────────────────────────────────────────────────────────────────


@app.get("/graph/traverse", response_model=GraphTraverseResponse)
def graph_traverse(
    start_id: int = Query(..., description="Starting memory ID"),
    depth: int = Query(3, ge=1, le=10, description="Max traversal depth"),
    relation_type: str | None = Query(None, description="Filter by relation type"),
    _: str = _Auth,
    agent_id: str = _Agent,
) -> GraphTraverseResponse:
    """Multi-hop graph traversal using recursive CTE. Returns connected memories up to N hops."""
    result = traverse_graph(start_id, agent_id=agent_id, depth=depth, relation_type=relation_type)
    return GraphTraverseResponse(**result)


@app.get("/graph/subgraph", response_model=SubgraphResponse)
def graph_subgraph(
    ids: str = Query(..., description="Comma-separated memory IDs (max 200)"),
    expand: int = Query(0, ge=0, le=5, description="Espandi i vicini entro N hop (0=solo i nodi richiesti)"),
    _: str = _Auth,
    agent_id: str = _Agent,
) -> SubgraphResponse:
    """
    Estrae il sottografo indotto dai nodi specificati.
    Con expand>0 aggiunge i vicini entro N hop per ogni nodo seed.
    """
    try:
        memory_ids = [int(x.strip()) for x in ids.split(",") if x.strip()]
    except ValueError:
        memory_ids = []
    if not memory_ids:
        return SubgraphResponse(total_nodes=0, total_edges=0)
    result = extract_subgraph(memory_ids, agent_id=agent_id, expand_depth=expand)
    return SubgraphResponse(**result)


@app.get("/graph/hubs", response_model=HubDetectionResponse)
def graph_hubs(
    limit: int = Query(20, ge=1, le=100, description="Numero massimo di hub da restituire"),
    min_degree: int = Query(1, ge=1, description="Grado minimo per essere incluso"),
    _: str = _Auth,
    agent_id: str = _Agent,
) -> HubDetectionResponse:
    """
    Rileva gli hub del grafo per l'agente usando il degree centrality.
    Restituisce i nodi ordinati per grado decrescente (in_degree + out_degree).
    """
    hubs = get_degree_centrality(agent_id=agent_id, limit=limit, min_degree=min_degree)
    return HubDetectionResponse(
        hubs=[HubNodeRecord(**h) for h in hubs],
        total=len(hubs),
    )


# ── Summarization ────────────────────────────────────────────────────────────


@app.get("/summarize", response_model=SummarizeResponse)
def summarize(
    topic: str = Query(..., min_length=1, description="Topic to summarize"),
    limit: int = Query(50, ge=1, le=200),
    top_keywords: int = Query(10, ge=1, le=50),
    _: str = _Auth,
    agent_id: str = _Agent,
) -> SummarizeResponse:
    """Summarize memories about a topic using TF-IDF keyword extraction (no LLM)."""
    from .summarizer import summarize_topic

    result = summarize_topic(topic, agent_id=agent_id, limit=limit, top_keywords=top_keywords)
    return SummarizeResponse(**result)


# ── ACL (multi-agent shared memory) ──────────────────────────────────────────


@app.post("/memories/{memory_id}/acl", response_model=ACLResponse, status_code=201)
def acl_grant(
    memory_id: int,
    req: ACLGrantRequest,
    _: str = _Auth,
    agent_id: str = _Agent,
) -> ACLResponse:
    """Grant access to a memory for another agent. Only owner or admin can grant."""
    from .acl import grant_access, list_permissions

    success = grant_access(memory_id, req.target_agent, req.permission, grantor_agent=agent_id)
    if not success:
        raise HTTPException(403, "Not authorized to grant access or memory not found")
    perms = list_permissions(memory_id, agent_id)
    return ACLResponse(success=True, permissions=perms)


@app.delete("/memories/{memory_id}/acl/{target_agent}", response_model=ACLResponse)
def acl_revoke(
    memory_id: int,
    target_agent: str,
    _: str = _Auth,
    agent_id: str = _Agent,
) -> ACLResponse:
    """Revoke access for an agent. Only owner or admin can revoke."""
    from .acl import list_permissions, revoke_access

    success = revoke_access(memory_id, target_agent, grantor_agent=agent_id)
    if not success:
        raise HTTPException(403, "Not authorized to revoke access or no permission found")
    perms = list_permissions(memory_id, agent_id)
    return ACLResponse(success=True, permissions=perms)


@app.get("/memories/{memory_id}/acl", response_model=ACLResponse)
def acl_list(
    memory_id: int,
    _: str = _Auth,
    agent_id: str = _Agent,
) -> ACLResponse:
    """List all permissions for a memory. Only visible to owner or admin."""
    from .acl import list_permissions

    perms = list_permissions(memory_id, agent_id)
    return ACLResponse(success=True, permissions=perms)


@app.get("/shared", response_model=SharedMemoriesResponse)
def shared_memories(
    limit: int = Query(50, ge=1, le=200),
    _: str = _Auth,
    agent_id: str = _Agent,
) -> SharedMemoriesResponse:
    """Get all memories shared with this agent by other agents."""
    from .acl import get_shared_memories

    results = get_shared_memories(agent_id, limit=limit)
    return SharedMemoriesResponse(memories=results, total=len(results))


# ── SSE Streaming Search ─────────────────────────────────────────────────────


@app.get("/stream/search")
async def stream_search(
    request: Request,
    q: str = Query(..., min_length=1, max_length=1000, description="Search query"),
    limit: int = Query(10, ge=1, le=50),
    _: str = _Auth,
    agent_id: str = _Agent,
) -> StreamingResponse:
    """Server-Sent Events streaming search. FTS5 results first, then semantic."""
    import asyncio
    import json

    async def event_stream():
        # Phase 1: FTS5 results (fast)
        fts_results, _, fts_total, _excl = search_memories(
            query=q,
            limit=limit,
            semantic=False,
            agent_id=agent_id,
        )
        fts_data = {
            "results": [r.model_dump(mode="json") for r in fts_results],
            "total": fts_total,
            "phase": "fts",
        }
        yield f"event: fts\ndata: {json.dumps(fts_data)}\n\n"

        # Small delay to allow client to process FTS
        await asyncio.sleep(0.05)

        # Phase 2: Semantic results (slower, may overlap with FTS)
        try:
            sem_results, _, sem_total, _excl2 = search_memories(
                query=q,
                limit=limit,
                semantic=True,
                agent_id=agent_id,
            )
            # Deduplicate — exclude IDs already sent in FTS phase
            fts_ids = {r.id for r in fts_results}
            new_results = [r for r in sem_results if r.id not in fts_ids]
            sem_data = {
                "results": [r.model_dump(mode="json") for r in new_results],
                "total": sem_total,
                "phase": "semantic",
            }
            yield f"event: semantic\ndata: {json.dumps(sem_data)}\n\n"
        except Exception:
            err_data = {"results": [], "total": 0, "phase": "semantic", "error": "unavailable"}
            yield f"event: semantic\ndata: {json.dumps(err_data)}\n\n"

        # Done signal
        yield "event: done\ndata: {}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Analytics ────────────────────────────────────────────────────────────────


@app.get("/analytics", response_model=AnalyticsResponse)
def analytics(
    _: str = _Auth,
    agent_id: str = _Agent,
) -> AnalyticsResponse:
    """Comprehensive analytics: categories, decay, tags, access patterns, growth."""
    from .analytics import get_analytics

    return AnalyticsResponse(**get_analytics(agent_id=agent_id))


# ── GDPR / Right to Erasure ──────────────────────────────────────────────────


@app.delete("/memories/agent/{target_agent}", response_model=GDPRDeleteResponse)
def gdpr_delete_agent(
    target_agent: str,
    _: str = _Auth,
    agent_id: str = _Agent,
) -> GDPRDeleteResponse:
    """GDPR Article 17 — Right to erasure. Permanently deletes ALL data for an agent.
    The requesting agent must match the target agent (self-deletion only)."""
    if agent_id != target_agent:
        raise HTTPException(403, "Can only delete your own agent data")

    from .database import get_connection

    with get_connection() as conn:
        # Count before deletion
        mem_count = conn.execute("SELECT COUNT(*) FROM memories WHERE agent_id = ?", (target_agent,)).fetchone()[0]

        # Delete in dependency order
        # Tags and relations cascade from memories, but be explicit
        tag_count = conn.execute(
            "DELETE FROM memory_tags WHERE memory_id IN (SELECT id FROM memories WHERE agent_id = ?)",
            (target_agent,),
        ).rowcount

        rel_count = conn.execute(
            """DELETE FROM memory_relations WHERE
               source_id IN (SELECT id FROM memories WHERE agent_id = ?)
               OR target_id IN (SELECT id FROM memories WHERE agent_id = ?)""",
            (target_agent, target_agent),
        ).rowcount

        # Delete conflict records for this agent
        try:
            conn.execute(
                "DELETE FROM memory_conflicts WHERE agent_id = ?",
                (target_agent,),
            )
        except Exception:
            pass  # memory_conflicts table may not exist yet

        # Delete ACL entries if table exists
        try:
            conn.execute(
                "DELETE FROM memory_acl WHERE agent_id = ? OR granted_by = ?",
                (target_agent, target_agent),
            )
        except Exception:
            pass  # ACL table may not exist yet

        # Delete vec_memories if sqlite-vec available
        try:
            conn.execute("DELETE FROM vec_memories WHERE agent_id = ?", (target_agent,))
        except Exception:
            pass

        # Delete FTS entries (triggers handle this on memory delete)
        conn.execute("DELETE FROM memories WHERE agent_id = ?", (target_agent,))

        session_count = conn.execute("DELETE FROM sessions WHERE agent_id = ?", (target_agent,)).rowcount

        event_count = conn.execute("DELETE FROM event_logs WHERE agent_id = ?", (target_agent,)).rowcount

    return GDPRDeleteResponse(
        deleted_memories=mem_count,
        deleted_tags=tag_count,
        deleted_relations=rel_count,
        deleted_sessions=session_count,
        deleted_events=event_count,
    )


# ── Plugins ──────────────────────────────────────────────────────────────────


@app.get("/plugins", response_model=PluginListResponse)
def plugins_list(_: str = _Auth) -> PluginListResponse:
    """List registered plugins."""
    from .plugins import list_plugins

    names = list_plugins()
    return PluginListResponse(plugins=names, total=len(names))


# ── Agents ────────────────────────────────────────────────────────────────────


@app.get("/agents", response_model=AgentListResponse)
def agents_list(_: str = _Auth) -> AgentListResponse:
    """List all agent IDs with memory count and last activity. No agent scoping — returns all agents."""
    rows = list_agents()
    return AgentListResponse(
        agents=[AgentRecord(**r) for r in rows],
        total=len(rows),
    )


# ── Metrics ───────────────────────────────────────────────────────────────────


@app.get("/metrics", include_in_schema=False)
def metrics(_: str = _Auth, agent_id: str = _Agent) -> Response:
    """Prometheus-compatible metrics endpoint."""
    from .repository import get_stats

    stats = get_stats(agent_id)
    lines = [
        "# HELP kore_memories_total Total memory records",
        "# TYPE kore_memories_total gauge",
        f"kore_memories_total {stats['total_memories']}",
        "# HELP kore_memories_active Active (non-decayed) memory records",
        "# TYPE kore_memories_active gauge",
        f"kore_memories_active {stats['active_memories']}",
        "# HELP kore_memories_archived Archived memory records",
        "# TYPE kore_memories_archived gauge",
        f"kore_memories_archived {stats['archived_memories']}",
        "# HELP kore_db_size_bytes Database file size in bytes",
        "# TYPE kore_db_size_bytes gauge",
        f"kore_db_size_bytes {stats['db_size_bytes']}",
    ]
    return Response(content="\n".join(lines) + "\n", media_type="text/plain; charset=utf-8")


# ── Filesystem Overlay (issue #024) ──────────────────────────────────────────


@app.post("/overlay/index", response_model=OverlayIndexResponse)
def overlay_index(
    body: OverlayIndexRequest,
    _: str = _Auth,
    agent_id: str = _Agent,
) -> OverlayIndexResponse:
    """Indicizza i file tecnici di un progetto come memories.

    Scansiona base_path cercando CLAUDE.md, README.md, pyproject.toml ecc.
    e crea memories con tag __overlay__ per dedup automatico.
    """
    from .filesystem_overlay import index_files, scan_directory

    patterns = body.patterns if body.patterns else None
    try:
        filepaths = scan_directory(
            base_path=body.base_path,
            patterns=patterns,
            include_extra_md=body.include_extra_md,
            max_depth=body.max_depth,
        )
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    stats = index_files(
        filepaths=filepaths,
        agent_id=agent_id,
        replace_existing=body.replace_existing,
    )
    return OverlayIndexResponse(
        indexed=stats["indexed"],
        updated=stats["updated"],
        skipped=stats["skipped"],
        errors=stats["errors"],
        file_results=stats["file_results"],
        files_scanned=len(filepaths),
    )


@app.get("/overlay/files", response_model=OverlayFilesResponse)
def overlay_files(
    _: str = _Auth,
    agent_id: str = _Agent,
) -> OverlayFilesResponse:
    """Restituisce la lista dei file attualmente indicizzati nell'overlay."""
    from .filesystem_overlay import list_overlay_files

    files = list_overlay_files(agent_id=agent_id)
    return OverlayFilesResponse(
        files=[OverlayFileRecord(**f) for f in files],
        total=len(files),
    )


@app.delete("/overlay/files", response_model=dict)
def overlay_remove_file(
    path: str = Query(..., description="Path assoluto del file da rimuovere dall'overlay"),
    _: str = _Auth,
    agent_id: str = _Agent,
) -> dict:
    """Rimuove tutte le memories di un file dall'overlay."""
    from .filesystem_overlay import remove_file_from_overlay

    removed = remove_file_from_overlay(filepath=path, agent_id=agent_id)
    return {"removed": removed, "path": path}


# ── Filesystem Watcher (issue #025) ──────────────────────────────────────────


@app.post("/overlay/watch", response_model=OverlayWatchResponse)
def overlay_watch_start(
    body: OverlayWatchRequest,
    _: str = _Auth,
    agent_id: str = _Agent,
) -> OverlayWatchResponse:
    """
    Avvia un watcher su base_path che auto-aggiorna l'overlay al cambio dei file.
    Richiede: pip install kore-memory[watcher]
    """
    from .filesystem_watcher import start_watcher

    try:
        patterns = body.patterns if body.patterns else None
        result = start_watcher(
            base_path=body.base_path,
            agent_id=agent_id,
            patterns=patterns,
            include_extra_md=body.include_extra_md,
            max_depth=body.max_depth,
        )
        return OverlayWatchResponse(**result)
    except ImportError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/overlay/watch", response_model=dict)
def overlay_watch_stop(
    path: str = Query(..., description="base_path del watcher da fermare"),
    _: str = _Auth,
    agent_id: str = _Agent,
) -> dict:
    """Ferma il watcher attivo per base_path."""
    from .filesystem_watcher import stop_watcher

    return stop_watcher(base_path=path, agent_id=agent_id)


@app.get("/overlay/watchers", response_model=OverlayWatchersResponse)
def overlay_watchers(
    _: str = _Auth,
) -> OverlayWatchersResponse:
    """Lista tutti i watcher attivi con statistiche."""
    from .filesystem_watcher import is_available, list_watchers

    watchers = list_watchers()
    return OverlayWatchersResponse(
        watchers=[OverlayWatcherRecord(**w) for w in watchers],
        total=len(watchers),
        watcher_available=is_available(),
    )


# ── Audit log ────────────────────────────────────────────────────────────────


@app.get("/audit", response_model=AuditResponse)
def audit_log(
    request: Request,
    event: str | None = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    since: str | None = Query(None, description="ISO datetime"),
    _: str = _Auth,
    agent_id: str = _Agent,
) -> AuditResponse:
    """Query the audit event log for the requesting agent."""
    from .audit import query_audit_log

    entries = query_audit_log(agent_id, event_type=event, limit=limit, since=since)
    return AuditResponse(events=entries, total=len(entries))


# ── Favicon ───────────────────────────────────────────────────────────────────


@app.get("/favicon.svg", include_in_schema=False)
async def favicon():
    """Serve the SVG favicon."""
    from pathlib import Path

    svg_path = Path(__file__).parent.parent / "assets" / "favicon.svg"
    if svg_path.exists():
        return Response(content=svg_path.read_text(), media_type="image/svg+xml")
    return Response(status_code=404)


# ── Dashboard ─────────────────────────────────────────────────────────────────


@app.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
async def dashboard(request: Request) -> HTMLResponse:
    """Web dashboard for memory management. Requires auth if not in local-only mode."""
    from .auth import _is_local, _local_only_mode

    if not (_local_only_mode() and _is_local(request)):
        await require_auth(request, request.headers.get("X-Kore-Key"))
    html = get_dashboard_html()
    # Inject CSP nonce
    nonce = getattr(request.state, "csp_nonce", "")
    html = html.replace("<script>", f'<script nonce="{nonce}">')
    return HTMLResponse(content=html)


# ── Utility ───────────────────────────────────────────────────────────────────


@app.get("/health")
def health() -> JSONResponse:
    from .database import get_connection
    from .repository import _embeddings_available

    # Verify DB connectivity
    db_ok = True
    try:
        with get_connection() as conn:
            conn.execute("SELECT 1").fetchone()
    except Exception:
        db_ok = False
    status = "ok" if db_ok else "degraded"
    return JSONResponse(
        {
            "status": status,
            "version": app.version,
            "semantic_search": _embeddings_available(),
            "database": "connected" if db_ok else "error",
        }
    )
