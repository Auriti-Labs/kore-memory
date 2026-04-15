<div align="center">

<img src="assets/logo.svg" alt="Kore Memory" width="420"/>

<br/>

**The memory layer that thinks like a human.**
<br/>
Remembers what matters. Forgets what doesn't. Never calls home.

<br/>

[![CI](https://github.com/auriti-labs/kore-memory/actions/workflows/ci.yml/badge.svg)](https://github.com/auriti-labs/kore-memory/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/kore-memory.svg?style=flat-square&color=7c3aed)](https://pypi.org/project/kore-memory/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue?style=flat-square)](https://python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green?style=flat-square)](LICENSE)
[![Zero Cloud](https://img.shields.io/badge/cloud-zero-orange?style=flat-square)]()
[![Multilingual](https://img.shields.io/badge/languages-50%2B-purple?style=flat-square)]()
[![Tests](https://img.shields.io/badge/tests-648-brightgreen?style=flat-square)]()
[![Coverage](https://img.shields.io/badge/coverage-80%25-yellow?style=flat-square)]()

<br/>

[**Install**](#-install) · [**Quickstart**](#-quickstart) · [**Integrations**](#-integrations) · [**MCP Tools**](#-mcp-server--model-context-protocol) · [**API**](#-api-reference) · [**Dashboard**](#-web-dashboard) · [**Changelog**](CHANGELOG.md)

</div>

---

## Why Kore?

Every AI agent memory tool has the same problem: they remember everything forever, phone home to cloud APIs, or require an LLM just to decide what's worth keeping.

**Kore is different.** It runs 100% locally, scores memory importance without any LLM call, and implements the [Ebbinghaus forgetting curve](https://en.wikipedia.org/wiki/Forgetting_curve) — the same mathematics behind human long-term memory — to keep your agent's memory lean, relevant, and fast.

<div align="center">

| | 🟣 **Kore** | Mem0 | Letta | Zep |
|:---|:---:|:---:|:---:|:---:|
| **🔒 Privacy & Architecture** | | | | |
| 100% offline — zero cloud | **✅** | — | — | — |
| No LLM required | **✅** | — | — | — |
| Setup in < 2 minutes | **✅** | — | — | — |
| **🧠 Memory Intelligence** | | | | |
| Ebbinghaus forgetting curve | **✅** | — | — | — |
| Auto-importance scoring (local) | **✅** | via LLM | — | via LLM |
| Memory compression (cosine dedup) | **✅** | — | — | — |
| Temporal memory (valid_from/to) | ✅ | — | — | ✅ |
| **🕸️ Knowledge & Context** | | | | |
| Graph RAG (multi-hop traversal) | **✅** | — | ✅ | — |
| Context Engine (token budget) | **✅** | — | — | — |
| Semantic search — 50+ languages | **✅** local | via API | ✅ | via API |
| TTL / Auto-expiration | **✅** | — | — | — |
| **💻 Developer Experience** | | | | |
| MCP Server (Claude Code, Cursor) | **✅** | — | — | — |
| Coding Memory Mode (ADR + RCA) | **✅** | — | — | — |
| Filesystem Watcher (live sync) | **✅** | — | — | — |
| Multi-agent ACL | ✅ | ✅ | ✅ | ✅ |
| Python + JS/TS SDK | ✅ | ✅ | — | ✅ |
| Export / Import (JSON) | ✅ | — | ✅ | ✅ |

</div>

---

## ✨ What's New in v3.0 — Cognitive Runtime

Wave 3 is complete. Kore Memory is now a full **Cognitive Runtime** for AI agents.

| Feature | Since |
|---|---|
| 🧠 **Context Engine** — assemble the most relevant memories within a token budget | v2.2 |
| 🕸️ **Graph RAG** — multi-hop traversal, subgraph extraction, hub detection | v2.3 |
| 📁 **Filesystem Overlay** — index project files (CLAUDE.md, docs) as memories | v2.3 |
| 👁️ **Filesystem Watcher** — live sync: auto-reindex on file change via watchdog | v3.0 |
| 💻 **Coding Memory Mode GA** — ADR, Root Cause Analysis, Runbooks, Regressions | v3.0 |
| 📊 **Explain Mode** — understand why a memory was surfaced (`explain=true`) | v2.2 |
| ⏳ **Temporal Memory** — `valid_from/to`, `supersedes_id`, conflict detection | v2.1 |

---

## ✨ Core Features

### 📉 Memory Decay — The Ebbinghaus Engine

Memories fade over time using the [Ebbinghaus forgetting curve](https://en.wikipedia.org/wiki/Forgetting_curve). Critical memories persist for up to a year. Casual notes fade in days.

```
decay = e^(−t · ln2 / half_life)
```

Every retrieval boosts the decay score by `+15%` — spaced repetition built into every search.

| Importance | Label | Half-life |
|:---:|:---:|:---:|
| 1 | Low | 7 days |
| 2 | Normal | 14 days |
| 3 | Important | 30 days |
| 4 | High | 90 days |
| 5 | Critical | 365 days |

### 🤖 Auto-Importance Scoring — No LLM Required

Kore scores memory importance locally using keyword signals, category baseline, and content length. No API call, no latency, no cost.

```python
"API token: sk-abc123"          → importance: 5  # critical credentials
"User prefers concise answers"  → importance: 4  # preference
"Meeting rescheduled to Friday" → importance: 2  # general
```

### 🔍 Semantic Search — 50+ Languages, 100% Local

Powered by `sentence-transformers` running entirely on your machine. Search in English, retrieve results in any language. Zero latency from network calls.

### 🧠 Context Engine — Token-Budget Assembly

Assemble the most relevant memories for a task within a strict token budget. Designed for prompt injection.

```bash
POST /context/assemble
{
  "task": "debug the authentication timeout issue",
  "budget_tokens": 2000,
  "ranking_profile": "coding"
}
```

Returns a structured context package: memories ranked by `similarity × decay × task_relevance × graph_centrality`, with `explain=true` showing score breakdown.

### 🕸️ Graph RAG — Multi-Hop Memory Traversal

Build a knowledge graph of connected memories. Traverse relations up to 10 hops deep via recursive CTE, extract subgraphs, detect hub nodes.

```bash
GET /graph/traverse?start_id=42&depth=3&relation_type=depends_on
```

### 📁 Filesystem Overlay + Live Watcher

Index your project files (CLAUDE.md, docs, configs) as memories. The watcher auto-reindexes any file within 1 second of being modified — no manual refresh.

```bash
pip install 'kore-memory[watcher]'
POST /overlay/watch  {"base_path": "/path/to/project"}
```

Supports `.md`, `.rst`, `.toml`, `.txt`, `.json`, `.yaml`, `.py`, `.cfg`. Uses debounce to handle IDE auto-save bursts.

### 🗜️ Memory Compression

Similar memories (cosine similarity > 0.88) are automatically merged into richer, deduplicated records. Your database stays lean forever.

### 💻 Coding Memory Mode — GA

Specialized tools for software development workflows:

```python
# Save an Architectural Decision Record
memory_save_decision("Use PostgreSQL for the main DB",
    rationale="Native JSONB, better query planner",
    alternatives_considered="MySQL, SQLite")

# Log a Root Cause Analysis
memory_log_root_cause("Connection pool exhaustion under load",
    symptom="API timeouts every ~2 hours",
    affected_component="db/pool",
    fix_applied="Added statement_timeout=30s + pool_timeout=10s")

# Track a Regression
memory_log_regression("Race condition in cache layer",
    introduced_in="v2.3.0", fixed_in="v2.3.1",
    test_ref="tests/test_cache.py::test_concurrent_set")

# Retrieve Runbooks
memory_get_runbook(trigger="deploy failed", component="api-gateway")
```

See [`docs/coding-memory-mode.md`](docs/coding-memory-mode.md) for the full guide.

### 📡 Native MCP Server

First-class [Model Context Protocol](https://modelcontextprotocol.io) server. Connect Claude Code, Cursor, or any MCP client to persistent, intelligent memory in under 5 minutes.

---

## 📦 Install

```bash
# Core (FTS5 search, no external deps)
pip install kore-memory

# + Local semantic search (50+ languages)
pip install 'kore-memory[semantic]'

# + MCP server (Claude Code, Cursor integration)
pip install 'kore-memory[semantic,mcp]'

# + Filesystem watcher (live overlay sync)
pip install 'kore-memory[semantic,mcp,watcher]'

# Everything
pip install 'kore-memory[semantic,mcp,watcher,nlp]'
```

> **Requirements:** Python 3.11+ · SQLite 3.35+ (bundled with Python) · No cloud account needed

---

## 🚀 Quickstart

### Start the server

```bash
kore
# → Kore Memory v3.0.0 running on http://localhost:8765
# → Dashboard: http://localhost:8765/dashboard
# → API docs:  http://localhost:8765/docs
```

### Save, search, done

```bash
# Save a memory (importance is auto-scored)
curl -X POST http://localhost:8765/save \
  -H "Content-Type: application/json" \
  -H "X-Agent-Id: my-agent" \
  -d '{"content": "User prefers concise responses in Italian", "category": "preference"}'
# → {"id": 1, "importance": 4}

# Search
curl "http://localhost:8765/search?q=user+preferences&limit=5" \
  -H "X-Agent-Id: my-agent"

# Save with TTL (auto-expires in 48 hours)
curl -X POST http://localhost:8765/save \
  -H "Content-Type: application/json" \
  -H "X-Agent-Id: my-agent" \
  -d '{"content": "Deploy scheduled for Friday", "category": "task", "ttl_hours": 48}'

# Batch save (up to 100 per request)
curl -X POST http://localhost:8765/save/batch \
  -H "Content-Type: application/json" \
  -H "X-Agent-Id: my-agent" \
  -d '{"memories": [
    {"content": "Always use parameterized queries", "category": "decision", "importance": 5},
    {"content": "React 19 supports server components", "category": "project"}
  ]}'
```

### Build a knowledge graph

```bash
# Tag a memory
curl -X POST http://localhost:8765/memories/1/tags \
  -H "Content-Type: application/json" \
  -H "X-Agent-Id: my-agent" \
  -d '{"tags": ["react", "frontend"]}'

# Link two related memories
curl -X POST http://localhost:8765/memories/1/relations \
  -H "Content-Type: application/json" \
  -H "X-Agent-Id: my-agent" \
  -d '{"target_id": 2, "relation": "depends_on", "strength": 0.9}'

# Traverse the graph (up to 10 hops)
curl "http://localhost:8765/graph/traverse?start_id=1&depth=3" \
  -H "X-Agent-Id: my-agent"
```

### Maintenance (cron-friendly)

```bash
# Daily decay pass — keeps memory fresh
curl -X POST http://localhost:8765/decay/run -H "X-Agent-Id: my-agent"

# Merge similar memories — keep DB lean
curl -X POST http://localhost:8765/compress -H "X-Agent-Id: my-agent"

# Remove expired TTL memories
curl -X POST http://localhost:8765/cleanup -H "X-Agent-Id: my-agent"

# Export full backup (JSON)
curl http://localhost:8765/export -H "X-Agent-Id: my-agent" > backup.json
```

---

## 🔌 Integrations

### Claude Code (MCP — stdio)

```bash
# Install
pip install 'kore-memory[semantic,mcp]'

# One-line setup
cp presets/claude-code/mcp.json ~/.claude/mcp.json
```

Or manually in `~/.claude/mcp.json`:

```json
{
  "mcpServers": {
    "kore-memory": {
      "command": "kore-mcp",
      "args": [],
      "env": { "KORE_LOCAL_ONLY": "1" }
    }
  }
}
```

**Coding Memory Mode preset** — add to your `CLAUDE.md`:

```bash
cat presets/claude-code-coding.md >> ~/.claude/CLAUDE.md
```

This enables Claude Code to automatically save architectural decisions, log root causes, retrieve runbooks, and assemble context packages with `ranking_profile: "coding"`.

### Cursor (streamable-http)

```bash
cp presets/cursor/mcp.json ~/.cursor/mcp.json
```

### Remote instance with Bearer Auth

```bash
export KORE_MCP_TOKEN=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
kore-mcp --transport streamable-http --host 0.0.0.0 --port 8766
```

The `/mcp/health` endpoint is always exempt from auth. All other routes require `Authorization: Bearer <token>`.

### Python SDK

```bash
pip install kore-memory   # SDK is built in
```

```python
from kore_memory import KoreClient

with KoreClient("http://localhost:8765", agent_id="my-agent") as kore:
    # Save
    result = kore.save("User prefers dark mode", category="preference")
    print(result.id, result.importance)  # → 1, 4

    # Semantic search
    memories = kore.search("dark mode", limit=5, semantic=True)
    for m in memories.results:
        print(m.content, round(m.score, 2), round(m.decay_score, 2))

    # Graph
    other = kore.save("Use Tailwind for styling", category="decision")
    kore.add_relation(result.id, other.id, "related")

    # Maintenance
    kore.decay_run()
    kore.compress()

    # Backup
    backup = kore.export_memories()
```

**Async variant:**

```python
from kore_memory import AsyncKoreClient

async with AsyncKoreClient("http://localhost:8765", agent_id="my-agent") as kore:
    result = await kore.save("Async memory", category="project")
    memories = await kore.search("async", limit=5)
```

**Exception hierarchy:** `KoreError` → `KoreAuthError` | `KoreNotFoundError` | `KoreValidationError` | `KoreRateLimitError` | `KoreServerError`

### JavaScript / TypeScript SDK

```bash
npm install kore-memory-client
```

```typescript
import { KoreClient } from 'kore-memory-client';

const kore = new KoreClient({ baseUrl: 'http://localhost:8765', agentId: 'my-agent' });

// Save + search
const { id } = await kore.save({ content: 'User prefers dark mode', category: 'preference' });
const results = await kore.search({ q: 'dark mode', limit: 5, semantic: true });

// Tags, relations, maintenance
await kore.addTags(id, ['ui', 'preference']);
await kore.addRelation(id, otherId, 'depends_on');
await kore.decayRun();
await kore.compress();
```

**Zero runtime deps · ESM + CJS · Full TypeScript · ~6 KB minified · Node 18+**

### LangChain

```python
from kore_memory.integrations.langchain import KoreLangChainMemory

memory = KoreLangChainMemory(base_url="http://localhost:8765", agent_id="langchain-agent")
chain = ConversationChain(llm=llm, memory=memory)
```

### CrewAI

```python
from kore_memory.integrations.crewai import KoreCrewAIMemory

crew = Crew(agents=[...], tasks=[...], memory=True,
            long_term_memory=KoreCrewAIMemory(base_url="http://localhost:8765"))
```

### PydanticAI / OpenAI Agents SDK

```bash
pip install 'kore-memory[pydantic-ai]'   # PydanticAI
pip install 'kore-memory[openai-agents]' # OpenAI Agents SDK
```

---

## 🛠️ MCP Server — Model Context Protocol

Kore ships a native [Model Context Protocol](https://modelcontextprotocol.io) server exposing **19 tools** for any MCP-compatible client.

```bash
kore-mcp                                              # stdio (Claude Code default)
kore-mcp --transport streamable-http --port 8766      # HTTP (Cursor, remote)
```

### Available MCP Tools

| Tool | Category | Description |
|---|---|---|
| `memory_save` | Core | Save a memory with auto-scoring |
| `memory_search` | Core | Semantic or FTS5 full-text search |
| `memory_delete` | Core | Delete a memory by ID |
| `memory_update` | Core | Update content, category, or importance |
| `memory_save_batch` | Core | Save up to 100 memories in one call |
| `memory_add_tags` | Graph | Add tags to a memory |
| `memory_search_by_tag` | Graph | Search memories by tag |
| `memory_add_relation` | Graph | Link two memories with a typed relation |
| `memory_timeline` | History | Chronological history for a subject |
| `memory_decay_run` | Maintenance | Recalculate all decay scores |
| `memory_compress` | Maintenance | Merge similar memories (cosine > 0.88) |
| `memory_cleanup` | Maintenance | Remove TTL-expired memories |
| `memory_import` | Backup | Import memories from JSON |
| `memory_export` | Backup | Export all active memories to JSON |
| `memory_get_context` | Context Engine | Assemble ranked context within token budget |
| `memory_save_decision` | Coding Mode | Save ADR with rationale and alternatives |
| `memory_log_root_cause` | Coding Mode | Log root cause analysis with symptom and fix |
| `memory_log_regression` | Coding Mode | Track regression with version and test ref |
| `memory_get_runbook` | Coding Mode | Retrieve runbook by trigger or component |

### Auto-session tracking

When used with Claude Code, `kore-mcp` automatically creates a session on the first `memory_save` call and closes it gracefully on shutdown. Your conversations appear as organized sessions in the dashboard — no manual tracking required.

---

## 📊 Web Dashboard

Built-in web UI served directly from FastAPI. No build step, no npm, no extra process.

```bash
kore
open http://localhost:8765/dashboard
```

| Tab | What you can do |
|---|---|
| **Overview** | Health, total memories, category breakdown, decay histogram |
| **Memories** | Full-text + semantic search, save, delete, view metadata |
| **Tags** | Browse by tag, add/remove tags on any memory |
| **Relations** | Visualize and create memory links |
| **Timeline** | Trace any subject chronologically |
| **Sessions** | Browse auto-created MCP sessions |
| **Maintenance** | Run decay, compress, and cleanup with one click |
| **Backup** | Export as JSON download, import from file |

Dark theme · responsive · agent selector · real-time updates via SSE

---

## 🧠 How It Works

```
Save memory
    │
    ├─ Auto-score importance (local, no LLM)
    ├─ Generate embedding (local sentence-transformers)
    ├─ Infer memory_type from category
    └─ Store in SQLite: decay_score = 1.0

         [time passes · Ebbinghaus curve runs]

    ├─ decay_score decreases continuously
    └─ Access reinforcement: decay_score += 0.05 per retrieval

Search query arrives
    │
    ├─ FTS5 full-text search  OR  local vector similarity
    ├─ Filter: decay_score < 0.05 → "forgotten", archived, expired TTL
    ├─ Re-rank: similarity × decay × confidence × task_relevance × graph_centrality
    │           (weights depend on ranking_profile: "default" | "coding")
    └─ Return top-k with score breakdown (explain=true)
```

### Memory lifecycle

```
saved (decay=1.0)
    ↓ time
active (0.05 < decay < 1.0)
    ↓ retrieval → decay += 0.05
reinforced
    ↓ no access
forgotten (decay < 0.05) — excluded from search
    ↓ /cleanup
purged from DB
```

---

## 📡 API Reference

Interactive docs at **http://localhost:8765/docs** (Swagger UI).

### Core CRUD

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/save` | Save a memory (auto-scored) |
| `POST` | `/save/batch` | Batch save (max 100) |
| `GET` | `/search?q=...` | Semantic / FTS5 search with cursor pagination |
| `GET` | `/memories/{id}` | Get single memory |
| `PUT` | `/memories/{id}` | Update memory |
| `DELETE` | `/memories/{id}` | Hard delete |

### Graph

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/memories/{id}/tags` | Add tags |
| `DELETE` | `/memories/{id}/tags` | Remove tags |
| `GET` | `/tags/{tag}/memories` | Search by tag |
| `POST` | `/memories/{id}/relations` | Create typed relation |
| `GET` | `/memories/{id}/relations` | List relations |
| `GET` | `/graph/traverse` | Multi-hop traversal (max 10 hops) |
| `GET` | `/graph/subgraph` | Extract subgraph |
| `GET` | `/graph/hubs` | Detect hub nodes by centrality |

### Context Engine

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/context/assemble` | Ranked context within token budget |
| `GET` | `/memories/{id}/explain` | Score breakdown for a memory |

### Lifecycle

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/decay/run` | Recalculate decay scores |
| `POST` | `/compress` | Merge similar memories |
| `POST` | `/cleanup` | Remove expired (TTL) |
| `POST` | `/auto-tune` | Auto-adjust importance from access patterns |
| `POST` | `/memories/{id}/archive` | Soft-delete |
| `POST` | `/memories/{id}/restore` | Unarchive |

### Filesystem Overlay

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/overlay/index` | Index files as memories |
| `DELETE` | `/overlay/files` | Remove overlay memories |
| `GET` | `/overlay/files` | List indexed files |
| `POST` | `/overlay/watch` | Start live filesystem watcher |
| `DELETE` | `/overlay/watch` | Stop watcher |
| `GET` | `/overlay/watchers` | List active watchers with stats |

### Multi-agent ACL

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/memories/{id}/acl` | Grant read/write/admin to another agent |
| `DELETE` | `/memories/{id}/acl/{agent}` | Revoke access |
| `GET` | `/shared` | List memories shared with requesting agent |

### Analytics & Admin

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/analytics` | Categories, decay buckets, top tags, growth |
| `GET` | `/agents` | List all agents with memory count |
| `GET` | `/audit` | Event log |
| `GET` | `/metrics` | Prometheus-compatible metrics |
| `GET` | `/health` | Health check + capabilities |
| `GET` | `/dashboard` | Web UI |
| `DELETE` | `/memories/agent/{id}` | GDPR right to erasure |

### Request Headers

| Header | Required | Description |
|---|:---:|---|
| `X-Agent-Id` | No | Agent namespace. Default: `"default"`. Max 64 chars `[a-zA-Z0-9_-]` |
| `X-Kore-Key` | On non-localhost | API key (auto-generated on first run, stored in `data/.api_key`) |
| `X-Session-Id` | No | Session tracking. Pattern: `[a-zA-Z0-9_\-.]{1,128}` |

### Memory Categories

**Standard:** `general` · `project` · `finance` · `person` · `preference` · `task` · `decision` · `fact`

**Coding Mode:** `architectural_decision` · `root_cause` · `runbook` · `regression_note` · `tech_debt` · `api_contract`

---

## ⚙️ Configuration

All configuration via environment variables. No config file needed.

| Variable | Default | Description |
|---|---|---|
| `KORE_DB_PATH` | `data/memory.db` | Database path |
| `KORE_HOST` | `127.0.0.1` | Bind address |
| `KORE_PORT` | `8765` | Server port |
| `KORE_LOCAL_ONLY` | `1` | Skip auth for localhost (set `0` for remote) |
| `KORE_API_KEY` | auto-generated | Override the auto-generated API key |
| `KORE_CORS_ORIGINS` | *(empty)* | Allowed origins (comma-separated) |
| `KORE_EMBED_MODEL` | `paraphrase-multilingual-MiniLM-L12-v2` | Sentence-transformers model |
| `KORE_EMBED_DIM` | `384` | Embedding dimensions (must match model) |
| `KORE_EMBED_BACKEND` | *(empty)* | Set `"onnx"` for ONNX inference backend |
| `KORE_MAX_EMBED_CHARS` | `8000` | Max chars per embedding call (OOM protection) |
| `KORE_SIMILARITY_THRESHOLD` | `0.88` | Cosine threshold for compression |
| `KORE_AUTO_TUNE` | `0` | Enable auto-tuning importance from access patterns |
| `KORE_ENTITY_EXTRACTION` | `0` | Enable spaCy NER entity extraction |
| `KORE_AUDIT_LOG` | `0` | Enable full audit log |
| `KORE_MCP_TOKEN` | *(empty)* | Bearer token for remote MCP server |
| `KORE_MCP_PORT` | `8766` | MCP HTTP transport port |

---

## 🔐 Security

- **API key** — auto-generated on first run, stored in `data/.api_key` (chmod 600). Override via `KORE_API_KEY`
- **Agent isolation** — all queries are scoped to `agent_id`. Agents cannot read each other's memories without explicit ACL grant
- **Parameterized queries** — no SQL injection possible; all DB queries use placeholders
- **Timing-safe comparison** — `secrets.compare_digest` for API key validation
- **Input validation** — Pydantic v2 on all endpoints; content 3–4000 chars, agent_id sanitized
- **Rate limiting** — per IP + path, configurable; 429 with `Retry-After` header
- **Security headers** — `X-Content-Type-Options`, `X-Frame-Options`, `CSP`, `Referrer-Policy` on every response
- **CORS** — restrictive by default; configure via `KORE_CORS_ORIGINS`
- **FTS5 sanitization** — special chars stripped, token count limited before DB query
- **OOM protection** — embedding input capped at 8000 chars
- **CSP nonce** — per-request nonce for dashboard inline scripts; no `unsafe-inline`
- **XSS prevention** — no user-supplied HTML rendered; all output escaped
- **Connection pool** — thread-safe SQLite pool (size 4), connection validation, fd leak cleanup

---

## 🗺️ Roadmap

**Wave 3 — Complete ✅**

- [x] Graph RAG (multi-hop traversal, subgraph, hub detection)
- [x] Context Engine (token-budget assembly, ranking profiles)
- [x] Filesystem Overlay (index project files as memories)
- [x] Filesystem Watcher (live auto-sync via watchdog)
- [x] Coding Memory Mode GA (ADR, Root Cause, Runbook, Regression)
- [x] Temporal memory (valid_from/to, supersession, conflict detection)
- [x] Multi-agent ACL (grant/revoke/check permissions)
- [x] SSE streaming search
- [x] Analytics endpoint
- [x] GDPR right to erasure
- [x] Plugin system (8 lifecycle hooks)
- [x] Explain mode (score breakdown per memory)
- [x] MCP Bearer Auth + auto-session tracking

**Wave 4 — In Planning**

- [ ] Lifecycle Policy Engine (auto-archive rules, importance decay overrides)
- [ ] Ranking Profiles per-Agent (persistent custom weights)
- [ ] Temporal Graph (relations with valid_from/to)
- [ ] Explainable Graph Retrieval (graph_path in context package)
- [ ] Docker self-hosted packaging
- [ ] PostgreSQL backend (for high-volume deployments)
- [ ] Embeddings v2 (multilingual-e5-large, 768 dims)

---

## 🛠️ Development

```bash
git clone https://github.com/auriti-labs/kore-memory
cd kore-memory
python -m venv .venv && source .venv/bin/activate
pip install -e ".[semantic,dev,mcp,watcher]"

# Run server
kore --reload

# Run all 648 tests
pytest tests/ -v

# Run with coverage
pytest tests/ --cov=kore_memory --cov-report=term-missing

# Lint
ruff check kore_memory/ && ruff format kore_memory/

# Benchmarks
pytest tests/benchmarks/ -v
```

---

## ❓ FAQ

**Does Kore send any data to external servers?**
No. Kore runs 100% locally. No telemetry, no cloud APIs, no LLM calls of any kind unless you explicitly configure a remote endpoint. Your memories never leave your machine.

**Do I need a GPU for semantic search?**
No. The default model (`paraphrase-multilingual-MiniLM-L12-v2`) runs efficiently on CPU, typically in < 50ms per query on any modern machine.

**Can I use Kore without sentence-transformers?**
Yes. Without `[semantic]`, Kore uses SQLite FTS5 (full-text search with BM25-style ranking) which is fast and fully offline. Install `[semantic]` only when you need cross-lingual search or semantic similarity.

**How does Kore differ from a vector database?**
Kore combines vector search with the Ebbinghaus forgetting curve, importance scoring, graph relations, temporal validity, and a context assembler. It's not just a database — it's a memory system that curates itself.

**Can multiple AI agents share memories?**
Yes. Each agent has its own namespace (`X-Agent-Id` header). Agents can optionally share memories via the ACL system (grant read/write/admin to specific agents).

**Is the MCP server compatible with Claude Code?**
Yes. Kore ships a ready-made preset (`presets/claude-code/mcp.json`). Copy it to `~/.claude/mcp.json` and the 19 tools are immediately available in Claude Code.

---

## 📄 License

MIT © [Juan Auriti](https://github.com/auriti)

---

<div align="center">

**Kore Memory** — persistent, intelligent, offline-first memory for AI agents.

<br/>

[PyPI](https://pypi.org/project/kore-memory/) · [npm](https://www.npmjs.com/package/kore-memory-client) · [Issues](https://github.com/auriti-labs/kore-memory/issues) · [Changelog](CHANGELOG.md) · [Docs](docs/)

<br/>

<sub>Built for AI agents that deserve better memory.</sub>

<br/>

<a href="https://buymeacoffee.com/auritidesign">
  <img src="https://img.shields.io/badge/Buy%20Me%20a%20Coffee-ffdd00?style=for-the-badge&logo=buy-me-a-coffee&logoColor=black" alt="Buy Me a Coffee" />
</a>

</div>
