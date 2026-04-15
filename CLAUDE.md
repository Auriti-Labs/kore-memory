# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Kore Memory is a persistent memory layer for AI agents (Python 3.11+, FastAPI, SQLite). Runs fully offline — no LLM calls, no cloud APIs. Implements Ebbinghaus forgetting curve decay, local auto-importance scoring, semantic search via sentence-transformers (with sqlite-vec native vector search), memory compression, graph RAG, multi-agent ACL, and a plugin system.

Published on PyPI as `kore-memory` (v2.5.0). JS SDK on npm as `kore-memory-client` (v2.5.0). MIT license.

## Commands

```bash
# Setup
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[semantic,dev]"

# Run server
kore                                    # localhost:8765
kore --port 9000 --reload               # dev mode
./start.sh                              # background (PID in logs/kore.pid)

# Tests (pytest, 15 file, 426 test)
pytest tests/ -v
pytest tests/test_api.py::TestSave -v           # singola classe
pytest tests/test_api.py::TestSave::test_save_basic -v  # singolo test

# Coverage (target >= 85%, attuale 88%)
pytest tests/ --cov=kore_memory --cov-report=term-missing

# JS SDK
cd sdk/js && npm install && npm run build   # build con tsup
cd sdk/js && npm test                       # test con vitest

# Build per PyPI
pip install build && python -m build
```

## Architecture

```
Request → FastAPI (main.py) → Auth (auth.py) → Pydantic (models.py) → Repository (repository/) → SQLite (database.py)
                                                     ↕                    ↕           ↕
                                               scorer.py           embedder.py    decay.py
                                                              vector_index.py  compressor.py
                                                     ↕
                                              events.py → audit.py
                                              auto_tuner.py
                                              integrations/entities.py
                                                     ↕
                                  summarizer.py | acl.py | analytics.py | plugins.py
```

**Entry points:**
- `kore_memory/cli.py` → comando `kore`, avvia uvicorn su `kore_memory.main:app`
- `kore_memory/main.py` → FastAPI app con lifespan (init_db + graceful shutdown), 50+ endpoint REST + dashboard
- `kore_memory/mcp_server.py` → comando `kore-mcp`, server MCP (stdio + streamable-http) per Claude/Cursor

**Repository package (kore_memory/repository/):**

The monolithic `repository.py` has been split into focused modules (v1.3.0):

| Module | Lines | Responsibility |
|--------|-------|----------------|
| `memory.py` | ~411 | CRUD: save, get, update (atomic), delete, batch, import/export, stats, agents |
| `search.py` | ~358 | Search: FTS5, semantic (asymmetric via embed_query), tag, timeline |
| `lifecycle.py` | ~125 | Decay pass, cleanup expired, archive, restore |
| `graph.py` | ~220 | Tags, relations, graph traversal (recursive CTE) |
| `sessions.py` | ~119 | Session CRUD + summarization |
| `__init__.py` | ~95 | Re-exports for backward compatibility |

**Core modules (kore_memory/):**

| Module | Lines | Responsibility |
|--------|-------|----------------|
| `main.py` | ~1050 | FastAPI app, 50+ REST endpoints, rate limiting, security headers, SSE streaming |
| `client.py` | ~509 | Python client SDK (sync `KoreClient` + async `AsyncKoreClient`) |
| `models.py` | ~370 | Pydantic v2 schemas, 35+ request/response models |
| `mcp_server.py` | ~400 | FastMCP server, 14 tool MCP + streamable-http transport |
| `compressor.py` | ~373 | Merge memories via cosine similarity > 0.88. Chunked clustering (O(chunk×n)) |
| `database.py` | ~257 | SQLite WAL mode, connection pool, schema (memories, FTS5, tags, relations, sessions, events, vec_memories) |
| `vector_index.py` | ~370 | SqliteVecIndex (native sqlite-vec) + legacy VectorIndex (in-memory numpy) fallback |
| `auto_tuner.py` | ~207 | Auto-tuning importance based on access patterns |
| `summarizer.py` | ~120 | TF-IDF keyword extraction and topic summarization (no LLM) |
| `acl.py` | ~193 | Multi-agent access control: grant/revoke/check permissions (read/write/admin) |
| `analytics.py` | ~131 | Aggregated analytics: categories, decay, tags, access patterns, growth |
| `plugins.py` | ~144 | Plugin system: KorePlugin ABC with 8 pre/post hooks |
| `embedder.py` | ~120 | Wrapper sentence-transformers v5: asymmetric search (encode_query/encode_document), ONNX backend |
| `auth.py` | ~118 | API key auto-generated, timing-safe comparison, agent namespace isolation |
| `audit.py` | ~110 | Event logging for memory operations |
| `config.py` | ~70 | Centralized config from env vars (all `KORE_*`) |
| `decay.py` | ~69 | Ebbinghaus curve: `decay = e^(-t·ln2/half_life)`. Half-life 7d→365d. +15% per retrieval |
| `scorer.py` | ~67 | Auto-scoring importance 1-5 without LLM: keyword signals, category baseline, length bonus |
| `events.py` | ~48 | Event bus for lifecycle hooks (save, delete, update, compress, archive, restore, decay) |

**Integrations (kore_memory/integrations/):**
- `pydantic_ai.py` — Tool-based memory access for PydanticAI agents
- `openai_agents.py` — Function tools for OpenAI Agents SDK
- `langchain.py` — `KoreLangChainMemory` (BaseMemory) + `KoreChatMessageHistory` (BaseChatMessageHistory v2)
- `crewai.py` — `KoreCrewAIMemory` memory provider for CrewAI agents
- `entities.py` — Entity extraction (spaCy NER optional, regex fallback)
- Optional install: `pip install 'kore-memory[pydantic-ai]'` / `'[openai-agents]'` / `'[langchain]'` / `'[crewai]'` / `'[nlp]'`

**JS/TS SDK (sdk/js/):**
- `src/client.ts` — class `KoreClient`, 17 async methods
- `src/types.ts` — TypeScript interfaces (HealthResponse aligned with real API)
- `src/errors.ts` — error hierarchy (`KoreError` → `KoreValidationError` | `KoreAuthError` | ...)
- Build: tsup (ESM + CJS), test: vitest

**Database schema:**
- Table `memories`: `id`, `agent_id`, `content`, `category`, `importance` (1-5), `decay_score` (0.0-1.0), `access_count`, `embedding` (JSON blob), `compressed_into` (FK self-ref), `expires_at` (TTL), `session_id`, `archived_at`
- Virtual table `memories_fts` (FTS5) on content + category with auto-sync triggers
- Virtual table `vec_memories` (sqlite-vec, optional) — native vector search with cosine distance, agent_id partition key
- Table `memory_tags`: many-to-many tags
- Table `memory_relations`: directed graph relations between memories
- Table `memory_acl`: access control (memory_id, agent_id, permission, granted_by)
- Table `sessions`: conversations (id, agent_id, title, created_at, ended_at)
- Table `event_logs`: audit trail (event, agent_id, memory_id, data, created_at)
- Composite index `idx_agent_decay_active` on (agent_id, compressed_into, archived_at, decay_score DESC)
- PRAGMA optimizations: synchronous=NORMAL, mmap_size=256MB, cache_size=32MB, temp_store=MEMORY

**Search flow:**
1. If `q=*` → return all memories (global wildcard)
2. If semantic=True and embeddings available → cosine similarity via sqlite-vec (native) or VectorIndex (legacy numpy)
3. Otherwise → FTS5 with wildcard, fallback LIKE
4. Filter archived (`archived_at IS NULL`), forgotten (`decay_score < 0.05`), and expired TTL
5. Re-rank by `similarity × decay × importance_weight`
6. Reinforcement: `access_count++`, `decay_score += 0.05`

**Auto-importance scoring:**
- `importance: None` (or omitted) → auto-scored via keyword signals, category, length
- `importance: 1-5` (explicit) → used as-is, no override

## Test Structure

15 files in `tests/` — **426 tests** total, coverage **88%**. Uses `TestClient` FastAPI (in-process, no network). Each test uses a shared temp DB (`KORE_DB_PATH` env var), `KORE_TEST_MODE=1` for testclient trusted host, isolated via `X-Agent-Id: test-agent`.

- `test_client_sync.py` (~812 lines) — 64 tests sync KoreClient (all methods)
- `test_api.py` (~769 lines) — TestHealth, TestSave, TestAuth, TestAgentIsolation, TestSearch, TestDecay, TestCompress, TestTimeline, TestDelete, TestArchive, TestCursorPagination, TestRateLimit, TestUpdateMemory, TestAutoScore
- `test_v2_features.py` (~428 lines) — 29 tests: Graph RAG, Summarization, ACL, SSE Streaming, Analytics, GDPR, Plugins
- `test_langchain.py` (~423 lines) — 28 tests LangChain integration (mocked)
- `test_client.py` (~398 lines) — Python client SDK (sync + async)
- `test_crewai.py` (~354 lines) — 19 tests CrewAI integration (mocked)
- `test_mcp.py` (~351 lines) — 32 tests MCP server (14 tools)
- `test_auto_tuner.py` (~348 lines) — auto-tuning importance
- `test_auth_events.py` (~304 lines) — 19 tests: auth, events, integrations, database edge cases
- `test_entities.py` (~296 lines) — entity extraction (NER, regex fallback)
- `test_audit.py` (~287 lines) — audit log (tracking, filtering, endpoint)
- `test_cli.py` (~261 lines) — 19 tests CLI (args, uvicorn mock, errors)
- `test_v11_fixes.py` (~230 lines) — 14 tests for v1.1.0 fixes (archived leak, audit emit, PRAGMA, thread-safety)
- `test_sessions.py` (~183 lines) — sessions (create, list, summarize, end, delete)
- `test_dashboard.py` (~100 lines) — dashboard route + CSP

Config pytest in `pyproject.toml` (`asyncio_mode = "auto"`). conftest.py sets `KORE_TEST_MODE=1` and resets rate limiter between tests.

## CI/CD

- `.github/workflows/ci.yml` — push/PR on main: test (Python 3.11+3.12+3.13), test-semantic, security (bandit + pip-audit), lint (ruff), coverage (pytest-cov ≥80%), test-js-sdk (Node 20)
- `.github/workflows/publish.yml` — tag v*, build + publish PyPI (trusted OIDC)
- `.github/workflows/build-sdk.yml` — tag v* + manual dispatch, build + test JS SDK

## Environment Variables

| Variable | Default | Usage |
|----------|---------|-------|
| `KORE_API_KEY` | auto-generated in `data/.api_key` | Override API key |
| `KORE_LOCAL_ONLY` | `"1"` | Skip auth for localhost (`"1"` = auth disabled on 127.0.0.1) |
| `KORE_TEST_MODE` | `"0"` | Enable `testclient` as trusted host (`"1"` in tests) |
| `KORE_DB_PATH` | `data/memory.db` | DB path (overridden in tests for temp DB) |
| `KORE_HOST` | `127.0.0.1` | Bind address |
| `KORE_PORT` | `8765` | Server port |
| `KORE_CORS_ORIGINS` | *(empty)* | Allowed origins (comma-separated) |
| `KORE_EMBED_MODEL` | `paraphrase-multilingual-MiniLM-L12-v2` | sentence-transformers model |
| `KORE_EMBED_DIM` | `384` | Embedding dimensions for sqlite-vec virtual table |
| `KORE_EMBED_BACKEND` | *(empty)* | Set to `"onnx"` for ONNX inference backend |
| `KORE_MAX_EMBED_CHARS` | `8000` | Max chars per embedder call (OOM protection) |
| `KORE_SIMILARITY_THRESHOLD` | `0.88` | Cosine threshold for compression |
| `KORE_AUTO_TUNE` | `"0"` | Enable auto-tuning importance (`"1"` to activate) |
| `KORE_ENTITY_EXTRACTION` | `"0"` | Enable entity extraction with spaCy/regex (`"1"` to activate) |
| `KORE_AUDIT_LOG` | `"0"` | Enable audit log for all operations (`"1"` to activate) |

## MCP Server

14 tools exposed via stdio + streamable-http transport (`kore-mcp`), with sanitized agent_id:

| Tool | Parameters | Usage |
|------|------------|-------|
| `memory_save` | content, category, importance, agent_id | Save memory (importance=0 → auto-score) |
| `memory_search` | query, limit, category, semantic, agent_id | Search (semantic/FTS5) |
| `memory_timeline` | subject, limit, agent_id | Chronological history |
| `memory_decay_run` | agent_id | Recalculate decay scores |
| `memory_compress` | agent_id | Merge similar memories |
| `memory_export` | agent_id | Export all memories |
| `memory_delete` | memory_id, agent_id | Delete memory |
| `memory_update` | memory_id, content, category, importance, agent_id | Update memory |
| `memory_save_batch` | memories[], agent_id | Batch save (max 100) |
| `memory_add_tags` | memory_id, tags[], agent_id | Add tags |
| `memory_search_by_tag` | tag, agent_id, limit | Search by tag |
| `memory_add_relation` | source_id, target_id, relation, agent_id | Create relation |
| `memory_cleanup` | agent_id | Delete expired memories |
| `memory_import` | memories[], agent_id | Bulk import (max 500) |

**NOTE**: Optional params use `str = ""` / `int = 0` as sentinels (not `str | None`) to avoid `anyOf` schema that prevents tool loading in Claude Code.

## REST API Endpoints (v2.0.0)

### Core CRUD
- `POST /save` — Save memory (auto-importance if omitted, X-Session-Id support)
- `POST /save/batch` — Batch save (max 100)
- `GET /search` — Semantic/FTS5 search with cursor pagination
- `GET /memories/{id}` — Get single memory by ID
- `PUT /memories/{id}` — Update memory (atomic single-query UPDATE)
- `DELETE /memories/{id}` — Hard delete

### Tags & Relations
- `POST /memories/{id}/tags` — Add tags
- `DELETE /memories/{id}/tags` — Remove tags
- `GET /memories/{id}/tags` — List tags
- `GET /tags/{tag}/memories` — Search by tag
- `POST /memories/{id}/relations` — Create relation
- `GET /memories/{id}/relations` — List relations

### Graph RAG (v2.0)
- `GET /graph/traverse?start_id=X&depth=3&relation_type=Y` — Multi-hop traversal via recursive CTE (max 10 hops)

### Summarization (v2.0)
- `GET /summarize?topic=X` — TF-IDF keyword extraction from related memories (no LLM)

### ACL (v2.0)
- `POST /memories/{id}/acl` — Grant read/write/admin to another agent
- `DELETE /memories/{id}/acl/{agent}` — Revoke access
- `GET /memories/{id}/acl` — List permissions
- `GET /shared` — List memories shared with requesting agent

### SSE Streaming (v2.0)
- `GET /stream/search?q=X` — Server-Sent Events (FTS first, then semantic, with dedup)

### Analytics (v2.0)
- `GET /analytics` — Categories, decay buckets, top tags, access patterns, 30-day growth

### GDPR (v2.0)
- `DELETE /memories/agent/{agent_id}` — Right to erasure (permanent deletion of ALL agent data)

### Plugins (v2.0)
- `GET /plugins` — List registered plugins

### Lifecycle
- `POST /decay/run` — Recalculate decay scores
- `POST /compress` — Merge similar memories
- `POST /cleanup` — Delete expired memories
- `POST /auto-tune` — Auto-adjust importance from access patterns
- `POST /memories/{id}/archive` — Soft-delete
- `POST /memories/{id}/restore` — Unarchive
- `GET /archive` — List archived

### Sessions
- `POST /sessions` — Create session
- `GET /sessions` — List sessions
- `GET /sessions/{id}/memories` — Session memories
- `GET /sessions/{id}/summary` — Session stats
- `POST /sessions/{id}/end` — End session
- `DELETE /sessions/{id}` — Delete session

### Admin
- `GET /export` — Export all agent memories
- `POST /import` — Import memories (max 500)
- `GET /entities` — Extracted entities
- `GET /agents` — List all agents
- `GET /audit` — Event log
- `GET /stats/scoring` — Importance stats
- `GET /metrics` — Prometheus-compatible metrics
- `GET /health` — Health check
- `GET /dashboard` — Web UI

## Key Patterns

- **Agent isolation**: all DB queries filter by `agent_id`. Header `X-Agent-Id`, default `"default"`, sanitized to `[a-zA-Z0-9_-]` max 64 chars
- **Local-only auth**: with `KORE_LOCAL_ONLY=1` (default), localhost requests skip API key validation. `testclient` trusted only with `KORE_TEST_MODE=1`. X-Forwarded-For ignored in local-only mode to prevent spoofing
- **Archived memories**: filtered with `AND archived_at IS NULL` in search (FTS5, semantic, LIKE), compression, decay pass, and vector index reload
- **Session ID validation**: header `X-Session-Id` validated with regex `^[a-zA-Z0-9_\-\.]{1,128}$`
- **Lazy embeddings**: sentence-transformers model loaded on first use, not at server startup
- **sqlite-vec**: native vector search via vec0 virtual table with partition key for agent isolation. Falls back to in-memory numpy if extension unavailable
- **Asymmetric search**: embedder v3 uses `encode_query()` for search queries and `encode_document()` for stored content (when model supports prompts)
- **Atomic updates**: `update_memory()` uses single UPDATE query with rowcount check (no read-then-write race condition)
- **Chunked compression**: similarity matrix processed in blocks of 2000 vectors — O(chunk×n) memory instead of O(n²)
- **Plugin hooks**: 8 hook points (pre/post save, search, delete, compress) via `KorePlugin` ABC
- **ACL hierarchy**: admin > write > read. Owner always has full access. Non-owners need explicit ACL grant
- **DB path**: `data/` and `logs/` directories created at runtime, ignored by git
- **Dashboard**: HTML served from `dashboard.py` with template in `templates/dashboard.html`
- **Client SDK exports**: `kore_memory/__init__.py` exports `KoreClient`, `AsyncKoreClient` and error hierarchy
- **CSP nonce**: each HTML response includes a per-request nonce to prevent XSS
- **Connection pool**: SQLite thread-safe pool size 4, connection validation, fd leak cleanup, graceful shutdown
- **Rate limiting**: in-memory per IP+path, configured in `config.RATE_LIMITS`
- **Response models**: all endpoints have `response_model` Pydantic for OpenAPI validation

## Release History

| Version | Theme | Key Features |
|---------|-------|--------------|
| v1.0.0 | Launch | Core API, FTS5, decay, auto-scoring, MCP server, dashboard |
| v1.1.0 | Stability | Bug fixes (archived leak), SQLite PRAGMA optimization, audit emit |
| v1.2.0 | Developer Experience | PydanticAI/OpenAI Agents/LangChain v2 integrations, MCP HTTP transport, SDK cursor pagination |
| v1.3.0 | Performance | sqlite-vec native vector search, repository refactoring (5 modules), embedder v3 (asymmetric + ONNX), chunked compressor |
| v2.0.0 | Intelligence | Graph RAG (recursive CTE), TF-IDF summarization, multi-agent ACL, SSE streaming, analytics, GDPR right to erasure, plugin system |
| v2.1.0 | Temporal Memory | valid_from/valid_to, invalidated_at, supersedes_id, confidence, provenance, memory_type, conflict detection |
| v2.2.0 | Context Engine | Context Assembler (budget tokens), Ranking Engine v1.1 (task_relevance, recency, freshness), explain mode, coding profile |
| v2.3.0 | Wave 3 Graph | Typed relations (strength/confidence), subgraph API, hub detection, filesystem overlay, benchmark datasets D+E |
| v2.4.0 | Dashboard + MCP | MCP auto-session, fix dashboard (wildcard count, timeline tab, semantic default), fix DB migration order |
| v2.5.0 | Filesystem Watcher | Live overlay sync via watchdog: debounce 1s, DELETE immediato, 3 REST endpoint, graceful degradation, 35 test |
