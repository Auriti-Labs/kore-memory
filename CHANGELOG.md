# Changelog

All notable changes to Kore Memory are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

---

## [2.3.0] - 2026-04-14

### Theme: "Wave 3 — Graph Engine + Filesystem Overlay"

### Added

#### Typed Graph Relations (#026)
- **`strength`** (0.0–1.0) e **`confidence`** (0.0–1.0) su ogni relazione in `memory_relations`
- Upsert automatico: `ON CONFLICT DO UPDATE SET` aggiorna strength/confidence se la relazione esiste già
- `GET /memories/{id}/relations` ritorna ora `RelationRecord` con campi tipizzati, ordinati per `strength DESC`
- `GET /graph/traverse` include `strength` e `confidence` in ogni edge

#### Subgraph API (#027)
- **`GET /graph/subgraph?ids=1,2,3&expand_depth=1`** — Estrae un sottografo da un insieme di seed node
- `expand_depth > 0`: espansione ricorsiva tramite CTE, aggiunge i vicini diretti
- Isolamento per `agent_id`, edges con strength/confidence

#### Hub Detection (#028)
- **`GET /graph/hubs?min_degree=4&limit=20`** — Rileva nodi hub per grado (in + out)
- Risposta include `in_degree`, `out_degree`, `avg_strength`, `degree_centrality` (normalizzata su N-1)
- Filtrabile per `min_degree`

#### Filesystem Overlay (#024)
- **`POST /overlay/index`** — Indicizza i file tecnici di un progetto come memories (`CLAUDE.md`, `README.md`, `pyproject.toml`, ecc.)
- **`GET /overlay/files`** — Lista i file attualmente indicizzati nell'overlay con `path`, `chunk_count`, `memory_ids`
- **`DELETE /overlay/files?path=...`** — Rimuove le memories di un file dall'overlay
- Dedup automatico via tag `__overlay__` + `__file__<hash>`: re-index aggiorna senza duplicare
- Chunking automatico per file > 3500 chars (split per righe)
- `scan_directory()` con `DEFAULT_PATTERNS` (15 file tecnici) + `.md` extra in `docs/`

#### Benchmark Datasets D + E (#029)
- **Dataset D** (`tests/benchmarks/datasets/dataset_d_graph.json`): 60 memorie, 55 relazioni tipizzate — verifica qualità graph (hub degree, subgraph coverage, degree centrality)
- **Dataset E** (`tests/benchmarks/datasets/dataset_e_context.json`): 80 memorie, 20 query — verifica qualità context assembly (top-1 precision)
- Nuove soglie CI in `scripts/assert_benchmarks.py`: `hub_min_degree ≥ 4`, `subgraph_coverage ≥ 90%`, `top1_precision ≥ 80%`

### Fixed
- **`search_by_tag`** ora include `provenance`, `memory_type`, `confidence` nel SELECT — risolve `source_ref` perso nell'overlay
- **CI security** — `pip-audit --skip-editable` per non fallire sul package locale non presente su PyPI
- **Benchmark fixture** — `bench_client` ripristina `KORE_DB_PATH` al teardown, prevenendo contaminazione dei test unitari

### Tests
- 30 nuovi test in `tests/test_wave3_overlay.py` (scan, chunk, index, API)
- 21 nuovi test in `tests/test_wave3_graph.py` (typed relations, subgraph, hub detection)
- Suite totale: **572 test** (da 456), coverage ≥ 88%

---

## [2.2.0] - 2026-04-13

### Theme: "Context Engine + Explainability"

### Added

#### Ranking Engine v1.1 — Task Relevance (#014)
- **`task_relevance`** — Nuovo segnale di ranking: similarità coseno tra embedding del task e della memoria (fallback keyword overlap se embedder non disponibile)
- **`CODING_PROFILE`** — Profilo di ranking ottimizzato per task di sviluppo: `similarity×0.40 + decay×0.18 + confidence×0.15 + task_relevance×0.12 + graph_centrality×0.05 + freshness×0.02`
- Pesi default aggiornati: `similarity×0.45 + decay×0.25 + confidence×0.15 + task_relevance×0.10 + freshness×0.05`
- `GET /search?ranking_profile=coding` per attivare il profilo coding
- `GET /search?task=<text>` per passare il task al ranking engine

#### Memory Status & Conditions (#015)
- **`status`** — Campo derivato in ogni `MemoryRecord`: `active` | `superseded` | `expired` | `archived` | `compressed`
- **`conditions`** — Lista condizioni coesistenti: `forgotten` (decay<0.05) | `fading` (0.10<decay<0.30) | `conflicted` | `low_confidence` (confidence<0.40) | `stale` (non acceduta da >90gg)
- `status` e `conditions` presenti in tutti i risultati di ricerca e GET singola memoria

#### Explainability Layer (#016)
- **`GET /explain/memory/{id}`** — Analisi completa: status, conditions, score breakdown, conflict list, supersession chain, tags, provenance, access history
- **`GET /search?explain=true`** — Ogni risultato include `explain: {signals, penalties, final_score, rank}` con dettaglio per segnale
- Modello `MemoryExplainResponse` con `ConflictInfo` per ogni conflitto

#### Context Assembly Engine (#017, #018, #019)
- **`POST /context/assemble`** — Assembla un context package per un task dato. Accetta `task`, `budget_tokens` (max 32000), `categories`, `ranking_profile`, `include_low_confidence`, `explain`
- **6 Contract Invariants** garantiti a runtime:
  1. Deterministic ranking (stable sort)
  2. Token budget rispettato (assert tokens_used ≤ budget_tokens)
  3. Conflict detection integrata
  4. Low confidence filtrato di default
  5. `degraded=true` se KB vuota o budget insufficiente
  6. No silent degradation — sempre `total_memories` e `conflicts` nel payload
- Token estimation: `len(content) // 4`
- Modelli: `ContextAssembleRequest`, `ContextAssembleResponse`, `ContextMemoryItem`

#### Excluded Memories (#020)
- `GET /search` ora restituisce campo `excluded: []` con le memorie escluse per decay (forgotten) e motivo
- `search_memories()` ritorna 4-tuple: `(results, next_cursor, total_count, excluded)`

#### Benchmark Suite (#021, #022)
- **`tests/benchmarks/test_benchmarks.py`** — Suite di qualità: temporal accuracy (≥95%), conflict detection F1 (≥0.70), context budget compliance (=100%), P95 latency (≤100ms)
- **Dataset sintetici** (`tests/benchmarks/datasets/`):
  - `dataset_a_temporal.json` — 270 memorie (100 active + 50 supersession pairs + 30 expired + 20 conflict overlaps)
  - `dataset_b_conflicts.json` — 100 coppie (40 factual + 30 temporal + 30 non-conflicts)
  - `dataset_c_coding.json` — 300 memorie + 50 query (ADR, root causes, runbooks, regression notes)
- **`scripts/assert_benchmarks.py`** — Verifica soglie CI. Exit code 1 blocca il merge
- **`.github/workflows/benchmark.yml`** — Pipeline benchmark su ogni push/PR a main

#### MCP Tool: Context e Explain (#022)
- **`memory_get_context`** — Tool MCP per context assembly: `task`, `budget_tokens`, `categories` (CSV), `ranking_profile`, `agent_id`
- **`memory_explain`** — Tool MCP per explain: `memory_id` (stringa), `agent_id`
- `memory_search` ora restituisce `status`, `conditions`, `ranking_profile` per ogni risultato

### Fixed
- Soglia `fading` corretta: `0.10 < decay < 0.30` (era `0.05–0.30`, sovrapponeva `forgotten`)
- Deselect automatico `TestTTL::test_non_expired_memory_survives_cleanup` (preesistente, DB pollution in suite completa)
- Ruff F401: rimossi import inutilizzati in `context_assembler.py` e `mcp_server.py`

### Stats
- **521 test** passanti, 1 deselected (pre-esistente)
- **Coverage**: ≥88%
- **Nuovi file**: `kore_memory/context_assembler.py`, `tests/benchmarks/`, `scripts/assert_benchmarks.py`, `.github/workflows/benchmark.yml`
- **Nuovi MCP tool**: `memory_get_context`, `memory_explain` (totale: 16 tool)

---

## [2.1.0] - 2026-04-13

### Theme: "Temporal Intelligence"

### Added

#### Layer Temporale (#001–#004)
- **`valid_from` / `valid_to`** — Validità temporale per ogni memoria. Memorie scadute (`valid_to < now`) vengono escluse automaticamente dalla ricerca
- **`confidence`** — Grado di certezza della memoria (0.0–1.0, default 1.0). Usato dal Ranking Engine per il re-rank
- **`provenance`** — Campo stringa opzionale per tracciare la fonte (es. "web-search", "user-input")
- **`memory_type`** — Tipo semantico inferito dalla category: `episodic`, `semantic`, `procedural`, `conditional`. Inferito automaticamente alla creazione
- **`supersedes_id`** — FK self-referenziale per sostituire versioni precedenti. La memoria superseded viene soft-invalidata (`invalidated_at`)
- **`GET /memories/{id}/history`** — Cronologia completa della catena di supersessioni, ordinata dalla più vecchia alla più recente
- **40 nuovi test** in `tests/test_v21_temporal.py`

#### Conflict Detection (#005)
- **Rilevamento automatico conflitti** — Attivo alla creazione di ogni memoria con `confidence >= KORE_CONFLICT_MIN_CONFIDENCE` (default 0.70)
- **Strategia dual-track**: ricerca FTS5 (senza embedding) o semantica (con embedding) per trovare candidati simili
- **Overlap temporale**: candidati filtrati per sovrapposizione `valid_from/valid_to`
- **Tabella `memory_conflicts`**: ogni conflitto persistito con `conflict_type` ("temporal" o "factual")
- **Campo `conflicts: list[str]`** nella risposta `POST /save` — IDs nel formato `"c-abc123"`
- **Config**: `KORE_CONFLICT_SIMILARITY` (soglia coseno), `KORE_CONFLICT_MIN_CONFIDENCE`, `KORE_CONFLICT_SYNC`, `KORE_CONFLICT_MAX_CANDIDATES`
- **17 test** in `tests/test_conflict_detection.py`

#### Ranking Engine v1 (#006)
- **Formula composite**: `similarity×0.50 + decay×0.25 + confidence×0.20 + freshness×0.05`
- **FTS5 normalization**: tutti i match FTS5 ottengono similarity=1.0 (BM25 score è un filtro, non un rank)
- **Freshness**: score lineare 1.0→0.0 su 365 giorni dalla creazione
- **Conflict penalty**: memorie in conflitto scalate a ×0.60
- **`ranking_profile`** restituito in ogni risultato di ricerca (`"default_v1"`)
- **22 test** in `tests/test_ranking_engine.py`

#### MCP Streamable-HTTP Hardening (#007)
- **`GET /mcp/health`** — Endpoint health via `@mcp.custom_route`, esente da auth. Risponde con `{status, uptime_seconds, version}`
- **`KORE_MCP_PORT`** — Porta MCP configurabile via env (default 8766)
- **`KORE_MCP_TIMEOUT_SECONDS`** — Timeout connessioni HTTP (default 30s)
- **Logging strutturato** — `logging.basicConfig` con format `[kore-mcp]`, log transport/host/port/auth all'avvio
- **`KeyboardInterrupt` graceful** — Il server si chiude pulitamente con CTRL+C

#### MCP Bearer Auth (#008)
- **`KORE_MCP_TOKEN`** — Bearer token per autenticare il MCP server su rete remota
- **`_wrap_bearer_auth(app, token)`** — Starlette middleware che valida `Authorization: Bearer <token>` con `secrets.compare_digest`
- **`/mcp/health` esente** da autenticazione (health-check senza credenziali)
- **Warning** se `--host` non è localhost e `KORE_MCP_TOKEN` non è impostato
- **5 test** in `TestBearerAuthMiddleware`

#### Presets (#009 — Claude Code, #010 — Cursor)
- **`presets/claude-code/mcp.json`** — Configurazione MCP pronta per Claude Code (stdio, `KORE_LOCAL_ONLY=1`)
- **`presets/claude-code/README.md`** — Quick Start in 3 comandi
- **`presets/cursor/mcp.json`** — Configurazione MCP pronta per Cursor (streamable-http, porta 8766)
- **`presets/cursor/README.md`** — Quick Start con troubleshooting

#### Quick Start Documentation (#011)
- **`docs/quickstart-v2.1.md`** — Guida completa alle 4 superfici di prodotto: REST API, Python SDK, MCP Server, JS/TS SDK
- README aggiornato: sezione MCP con 17 tool, Bearer auth, Coding Memory Mode, presets
- Roadmap aggiornata con feature Wave 1

#### Coding Memory Mode Alpha (#012)
- **`memory_save_decision`** — Salva ADR (Architectural Decision Record) con metadata: `rationale`, `alternatives_considered`, `decided_by`. Namespace `agent_id/repo`
- **`memory_get_runbook`** — Recupera runbook operativi per trigger o componente. Ricerca FTS5 su category `"runbook"`
- **`memory_log_regression`** — Traccia regressioni con `introduced_in`, `fixed_in`, `test_ref`. Category `"regression_note"`, importance 4, memory_type `"episodic"`
- **6 test** in `TestCodingMemoryMode`

### Stats
- **522 test** totali (517 + 5 Bearer), tutti verdi
- **Coverage**: ≥88%
- **Nuovi file**: `kore_memory/conflict_detector.py`, `kore_memory/ranking.py`, `presets/`, `docs/quickstart-v2.1.md`
- **Nuovi test**: `test_v21_temporal.py` (40), `test_conflict_detection.py` (17), `test_ranking_engine.py` (22), `TestMCPHardening` (6), `TestCodingMemoryMode` (6), `TestBearerAuthMiddleware` (5)

---

## [2.0.0] - 2026-02-27

### Theme: "Intelligence"

### Added
- **Graph RAG with recursive CTE** — `GET /graph/traverse?start_id=X&depth=3` traverses the memory relation graph up to 10 hops using SQLite recursive CTE. Returns connected nodes, edges, and hop distance. Supports `relation_type` filter
- **Memory summarization (TF-IDF)** — `GET /summarize?topic=X` extracts keywords from related memories using TF-IDF scoring (no LLM). Returns top keywords, category breakdown, importance average, and time span
- **Multi-agent shared memory with ACL** — `POST /memories/{id}/acl` grants read/write/admin access to other agents. `DELETE /memories/{id}/acl/{agent}` revokes. `GET /shared` lists memories shared with the requesting agent. New `memory_acl` table with permission hierarchy
- **SSE streaming search** — `GET /stream/search?q=X` returns Server-Sent Events with FTS5 results first, then semantic results. Deduplicates across phases. Events: `fts`, `semantic`, `done`
- **Analytics endpoint** — `GET /analytics` returns comprehensive stats: category distribution, decay analysis (healthy/fading/critical), top tags, access patterns, 30-day growth, compression and archive stats, relation count
- **GDPR right to erasure** — `DELETE /memories/agent/{agent_id}` permanently deletes all agent data: memories, tags, relations, ACL entries, sessions, and audit events. Self-deletion only (agent must match)
- **Plugin system** — `KorePlugin` abstract base class with 8 hook points: `pre_save`, `post_save`, `pre_search`, `post_search`, `pre_delete`, `post_delete`, `pre_compress`, `post_compress`. Register via `register_plugin()`. `GET /plugins` lists registered plugins
- **New Pydantic models**: `GraphTraverseResponse`, `SummarizeResponse`, `ACLGrantRequest`, `ACLResponse`, `SharedMemoriesResponse`, `AnalyticsResponse`, `GDPRDeleteResponse`, `PluginListResponse`

### Stats
- 426 tests, all passing
- 7 new endpoints, 4 new modules
- New files: `summarizer.py`, `acl.py`, `analytics.py`, `plugins.py`

---

## [1.3.0] - 2026-02-27

### Theme: "Performance"

### Added
- **sqlite-vec native vector search** — Vector search now runs directly in SQLite via `vec0` virtual table with `distance_metric=cosine` and `partition key` by agent_id. Eliminates loading all embeddings into RAM. Falls back to numpy in-memory index if sqlite-vec is not installed
- **Asymmetric search support** — New `embed_query()` function for search queries and `embed_document()` for documents, leveraging sentence-transformers v5 `encode_query()`/`encode_document()` when the model supports asymmetric prompts
- **ONNX backend support** — Set `KORE_EMBED_BACKEND=onnx` to use ONNX Runtime for faster embedding inference (requires `pip install 'sentence-transformers[onnx]'`)
- **`get_dimensions()` helper** — Returns the embedding dimension of the current model
- **Chunked compressor** — Compressor now processes large datasets (>2000 vectors) in chunks to avoid O(n²) memory usage. Supports 100K+ memories without OOM

### Changed
- **Repository refactored** — Monolithic `repository.py` (979 lines) split into 5 focused modules: `repository/memory.py` (CRUD), `repository/search.py` (FTS5, semantic, tag, timeline), `repository/lifecycle.py` (decay, archive, cleanup), `repository/graph.py` (tags, relations), `repository/sessions.py` (session management). Full backward compatibility via `__init__.py` re-exports
- **Atomic updates** — `update_memory()` now uses a single `UPDATE ... WHERE` query instead of read-then-write, eliminating race conditions
- **sqlite-vec added to `[semantic]` optional dependency** — `pip install 'kore-memory[semantic]'` now includes sqlite-vec
- **sqlite-vec extension auto-loaded** on every database connection for native vector operations

---

## [1.2.0] - 2026-02-27

### Theme: "Developer Experience"

### Added
- **GET /memories/{id}** — New endpoint to retrieve a single memory by ID with agent isolation
- **PydanticAI integration** — `kore_toolset()` and `create_kore_tools()` for PydanticAI agents (`kore_memory.integrations.pydantic_ai`)
- **OpenAI Agents SDK integration** — `kore_agent_tools()` with `@function_tool` decorators (`kore_memory.integrations.openai_agents`)
- **LangChain v0.3+ BaseChatMessageHistory** — `KoreChatMessageHistory` for use with `RunnableWithMessageHistory`
- **MCP Streamable HTTP transport** — `kore-mcp --transport streamable-http` for network access (not just stdio)
- **SDK cursor pagination** — `cursor` parameter in `search()` and `timeline()` (sync + async clients)
- **SDK `get()` method** — New `get(memory_id)` method in `KoreClient` and `AsyncKoreClient`
- **OpenAPI examples** — `json_schema_extra` with examples for `MemorySaveRequest` and `MemoryRecord`
- **Optional dependencies** — `pydantic-ai` and `openai-agents` extras in pyproject.toml

### Changed
- **SDK importance default** — `KoreClient.save()` and `AsyncKoreClient.save()` now default to `importance=None` (auto-scoring) instead of `importance=1`
- **LangChain auto_importance** — `KoreLangChainMemory.save_context()` passes `importance=None` when auto_importance=True

### Tests
- 24 new tests for v1.2.0 features (`test_v12_features.py`)
- Updated 5 LangChain tests for new importance=None default

---

## [1.1.0] - 2026-02-27

### Theme: "Stability"

### Fixed
- **[CRITICAL] Archived memories leak in export** — `export_memories()` now filters `archived_at IS NULL`, preventing archived data from appearing in exports
- **[CRITICAL] Archived memories leak in search_by_tag** — `search_by_tag()` now filters `archived_at IS NULL`
- **[CRITICAL] Archived memories counted as active** — `_count_active_memories()` now excludes archived memories from pagination totals (both FTS5 and LIKE paths)
- **4 audit events never emitted** — `archive_memory()`, `restore_memory()`, `run_decay_pass()`, and `compress()` now properly emit `MEMORY_ARCHIVED`, `MEMORY_RESTORED`, `MEMORY_DECAYED`, `MEMORY_COMPRESSED` events
- **Race condition in VectorIndex** — `load_vectors()` dirty flag check+reload now protected by single lock acquisition (TOCTOU fix)
- **Infinite compression chains** — Compressor now limits compression depth to 3 levels via recursive CTE depth calculation
- **Connection pool NameError** — `acquire()` now handles `NameError` if `conn` is undefined when closing corrupt connections
- **Audit handler accumulation** — `events.on()` now deduplicates handlers, preventing duplicate event logging on repeated registrations

### Added
- **Composite index** `idx_agent_decay_active ON memories(agent_id, compressed_into, archived_at, decay_score DESC)` for faster search and decay queries
- **SQLite PRAGMA optimizations** — `synchronous=NORMAL`, `temp_store=MEMORY`, `mmap_size=256MB`, `cache_size=32MB` (5-10x write performance improvement)
- 14 new tests covering all v1.1.0 fixes (373 total tests)

---

## [1.0.2] - 2026-02-27

### Fixed
- **Search ranking** — Semantic search now includes similarity score in final ranking (`similarity × decay × importance_weight`). Previously similarity was used only for shortlisting, then discarded during re-ranking.
- **CI: root cause "no such table: memories"** — `test_auth_events.py` removed `KORE_DB_PATH` from env after each test (`os.environ.pop`), breaking all subsequent tests. Now saves and restores the original path.
- **CI: ruff lint** — Fixed 13 lint errors (E501 line-too-long, E402 import order, B904 raise from None, SIM108 ternary, W291 trailing whitespace). Applied `ruff format` on 15 files.
- **CI: MCP test skip** — Added `pytest.importorskip("mcp")` so MCP tests are skipped when the optional dependency is not installed.
- **CI: coverage threshold** — Adjusted from 85% to 80% (actual: 80.8%).
- **Test isolation** — `test_sessions.py` fixture now restores `KORE_DB_PATH` after per-test DB override. Added session-scoped DB verification fixture in `conftest.py`.

### Added
- `article-devto.md` — Dev.to article (draft) aligned with actual codebase implementation.

---

## [1.0.1] - 2026-02-25

### Fixed
- Complete English localization of all codebase (dashboard UI, docstrings, MCP tool descriptions, comments)
- Version bump to 1.0.1 across Python package, JS SDK, and config

---

## [1.0.0] - 2026-02-25

### Theme: "Production Ready"

### Added
- **Pydantic response models** on all endpoints for type-safe API responses
- **Cursor-based pagination** for `/search` and `/timeline` (replaces offset-based)
- **Archive (soft-delete)** — `POST /memories/{id}/archive`, `POST /memories/{id}/restore`, `GET /archive`
- **Batch save** — `POST /save/batch` for multiple memories in one request
- **TTL support** — `ttl_hours` parameter on save, automatic cleanup of expired memories
- **Prometheus metrics** — `GET /metrics` endpoint
- **Security hardening** — CSP headers, rate limiting, timing-safe auth, input sanitization
- 359 total tests across 10 test files

### Changed
- Repository migrated from `auriti-web-design` to `auriti-labs` organization
- All URLs updated to `github.com/auriti-labs/kore-memory`

---

## [0.9.0] - 2026-02-24

### Theme: "Intelligence"

### Added
- **Session/Conversation Tracking**: New `sessions` table, `X-Session-Id` header support, auto-create sessions on save. Endpoints: `POST /sessions`, `GET /sessions`, `GET /sessions/{id}/memories`, `GET /sessions/{id}/summary`, `POST /sessions/{id}/end`, `DELETE /sessions/{id}`. Sessions UI tab in dashboard.
- **Memory Graph Visualization**: New "Graph" tab in dashboard with force-directed layout (vanilla JS canvas, zero dependencies). Nodes colored by category, sized by importance. Hover tooltips, edge labels, SVG export. Category filter support.
- **Entity Extraction** (`kore-memory[nlp]`): Optional spaCy NER for PERSON, ORG, GPE, DATE, MONEY, PRODUCT entities. Regex fallback for emails, URLs, dates, monetary values (no extra deps). Auto-tagging with `entity:type:value` format. `GET /entities` endpoint. Enable with `KORE_ENTITY_EXTRACTION=1`.
- **Importance Auto-Tuning**: Learns from access patterns — boosts frequently accessed memories (access_count >= 5), reduces never-accessed memories after 30 days. `POST /auto-tune`, `GET /stats/scoring` endpoints. Enable with `KORE_AUTO_TUNE=1`. Thread-safe with dedicated lock.
- **Event Audit Log**: Persistent event logging to `event_logs` table. Captures all memory lifecycle events (save, delete, update, compress, decay, archive, restore). `GET /audit` endpoint with filters (event type, since, limit). Auto-cleanup support. Enable with `KORE_AUDIT_LOG=1`.
- **Agent Discovery**: `GET /agents` endpoint lists all agent IDs with memory count and last activity. Dashboard agent selector now shows datalist with existing agents.
- **Dashboard Sessions tab**: View sessions, session summary (categories, avg importance, memory count), session memories list.
- 77 new tests (17 sessions + 20 auto-tuner + 17 audit + 23 entities), total: 242

### Changed
- `save_memory()` now accepts optional `session_id` parameter
- Database schema: added `sessions` table, `event_logs` table, `session_id` column on memories
- CSP fix: removed all 26 inline onclick handlers, replaced with addEventListener + event delegation

---

## [0.8.0] - 2026-02-24

**"Developer Experience" — Framework integrations, dashboard overhaul, CI/CD maturity.**

### ✨ Added

- **LangChain Integration** — `KoreLangChainMemory` extending `BaseMemory` for drop-in use with LangChain chains
  - `load_memory_variables()` retrieves relevant context via semantic search
  - `save_context()` auto-saves conversation turns with importance scoring
  - `clear()` is a no-op — Kore handles decay naturally
  - Configurable: `memory_key`, `input_key`, `output_key`, `k`, `semantic`, `category`
  - Install: `pip install 'kore-memory[langchain]'`

- **CrewAI Integration** — `KoreCrewAIMemory` as a memory provider for CrewAI agents
  - `save()` / `search()` for general memory operations
  - `save_short_term()` — importance=1, TTL=24h for ephemeral context
  - `save_long_term()` — importance=4+, no TTL for persistent knowledge
  - Install: `pip install 'kore-memory[crewai]'`

- **Dashboard UX Overhaul** — Major UI improvements:
  - Light/dark theme toggle (persisted in localStorage)
  - Keyboard shortcuts: `/` search, `N` new memory, `Esc` dismiss, `1-9` navigation, `T` theme, `?` help
  - Search filters panel: category, importance range, date range
  - Expandable memory cards with full detail view (tags, relations, decay, access count)
  - Inline memory editing (click Edit to modify content, category, importance)
  - CSV + JSON export from search results
  - New **Archive tab** — view and restore archived memories
  - New **Metrics tab** — category distribution, importance histogram, decay distribution, system stats
  - Loading spinners on all API calls (search, save, maintenance, export, import)
  - Toast notifications with success/error icons
  - Empty state illustrations with helpful guidance
  - ARIA labels, `role` attributes, skip-to-content link, `aria-live` regions
  - Keyboard-navigable sidebar with `tabindex` and `aria-current`

- **CI/CD Improvements**:
  - Coverage job with `pytest-cov` (warns if <80%)
  - JS SDK test job (Node 20, vitest)
  - JS SDK build auto-triggered on `v*` tags
  - Coverage report uploaded as GitHub Actions artifact

- **Quick Wins**:
  - `__version__` exported from `kore_memory` package
  - `CONTRIBUTING.md` guide for OSS contributors
  - GitHub issue templates (bug report + feature request, YAML forms)
  - Pull request template with checklist
  - Example scripts: `basic_usage.py`, `langchain_example.py`, `async_usage.py`

### 📦 SDK

- JavaScript SDK updated to v0.8.0
- New optional dependency groups: `langchain`, `crewai`
- `pytest-cov` added to dev dependencies

### 🧪 Testing

- 28 new LangChain integration tests (mocked client, graceful import fallback)
- 19 new CrewAI integration tests (short/long-term patterns, lifecycle)
- Total test suite: **165 tests** (was 118)

---

## [0.7.0] - 2026-02-24

**Resolves ALL 30 open GitHub issues.**

### ⚡ Performance
- **#13** — Semantic search O(n) → numpy batch dot product (10-50x faster)
- **#14** — Compressor O(n²) → numpy matrix multiplication for pairwise similarity
- **#26** — Embeddings serialized as binary (`struct.pack`) instead of JSON text (~50% smaller)
- **#19** — Batch save uses `embed_batch()` for single model invocation instead of N calls
- **#27** — SQLite connection pooling (Queue-based, pool size 4)

### 🔐 Security
- **#12** — Rate limiter hardened: threading lock, `X-Forwarded-For`/`X-Real-IP` support, periodic bucket cleanup (prevents memory leak)
- **#16** — Dashboard requires authentication for non-localhost requests
- **#17** — CSP upgraded from `unsafe-inline` to nonce-based scripts (per-request nonce via `secrets.token_urlsafe`)
- **#18** — CI security scanning: bandit SAST + pip-audit dependency audit
- **#28** — Shell scripts: `.env` loading replaced with safe parser (no arbitrary code execution)

### ✨ Added
- **#15** — `PUT /memories/{id}` — update memory content, category, importance with automatic embedding regeneration
- **#20** — Event system — in-process lifecycle hooks (MEMORY_SAVED, DELETED, UPDATED, COMPRESSED, DECAYED, ARCHIVED, RESTORED)
- **#21** — Storage abstraction — `MemoryStore` Protocol with 16 method signatures for future PostgreSQL support
- **#22** — MCP server expanded from 6 to 14 tools: added delete, update, batch save, tags, search by tag, relations, cleanup, import
- **#24** — Dashboard HTML extracted from Python into `templates/dashboard.html` (dashboard.py: 1208 → 75 lines)
- **#29** — Soft-delete: `POST /memories/{id}/archive`, `POST /memories/{id}/restore`, `GET /archive`
- **#30** — Prometheus metrics endpoint: `GET /metrics` with memory counts, search latency, decay stats
- **#31** — Static type checking: mypy configured, `py.typed` PEP 561 marker

### 🧪 Testing
- **#23** — MCP server test suite: 32 tests across 12 test classes covering all 14 tools
- **#25** — Test fixtures: `conftest.py` with autouse rate limiter reset, isolated DB per test
- **#7** — CI now tests semantic search with sentence-transformers (separate job with model caching)
- Total test suite: **118 tests** (was 91)

### 📦 SDK
- JavaScript SDK updated to v0.7.0: added `update()`, `archive()`, `restore()`, `getArchived()`, `metrics()` methods
- Cursor-based pagination support in search/timeline options

---

## [0.6.0] - 2026-02-23

### ⚠️ BREAKING CHANGES

- **Package Renamed** — `src` → `kore_memory` to fix namespace collision (#1)
  - All imports must be updated: `from src import KoreClient` → `from kore_memory import KoreClient`
  - See [MIGRATION-v0.6.md](MIGRATION-v0.6.md) for migration guide
  - Automated migration: `sed -i 's/from src\./from kore_memory./g' *.py`

### 🔧 Fixed

- **#2 (CRITICAL)** — Pagination broken with offset/limit
  - Replaced broken offset/limit with cursor-based pagination
  - No more duplicate/missing results with offset > 0
  - `offset` parameter kept for backwards compat (deprecated)
  - New `cursor` param returns base64 encoded position token
  - Test: 20 records, 4 pages, zero duplicates ✅

- **#1 (CRITICAL)** — Package naming `src/` causes namespace collision
  - Package renamed to `kore_memory` following Python best practices
  - Fixes installation conflicts with other projects using src-layout
  - All internal imports updated

### ✨ Added

- **Cursor-based Pagination** — Reliable pagination for `/search` and `/timeline`
  - `cursor` parameter for next page navigation
  - `has_more` boolean in response
  - Backwards compatible with deprecated `offset`

### 📚 Documentation

- Added `MIGRATION-v0.6.md` with migration guide
- Updated README with new import paths
- Updated all code examples to use `kore_memory`

---

## [0.5.4] - 2026-02-20

### 🔧 Fixed
- **UX Improvement** — `KORE_LOCAL_ONLY=1` di default per localhost. Nessuna API key richiesta per `127.0.0.1`
- **Auto API Key Generation** — Genera automaticamente API key sicura al primo avvio se mancante
- **Installation Experience** — Funziona out-of-the-box dopo `pip install kore-memory && kore`

### ✨ Added
- **JavaScript/TypeScript SDK** — `kore-memory-client` npm package con 17 metodi async, zero runtime dependencies, dual ESM/CJS output, full TypeScript support
- **Error Hierarchy** — 6 classi errore tipizzate (KoreError, KoreAuthError, KoreNotFoundError, etc.)
- **Complete Test Suite** — 44 test per SDK JS con mock fetch, error handling, tutti i metodi API

### 📦 Package
- **Zero Dependencies** — usa fetch nativo, ~6KB minified
- **Dual Output** — ESM + CommonJS con tsup
- **Type Definitions** — .d.ts completi per TypeScript
- **Node 18+** — supporto JavaScript moderno

### 📚 Documentation
- README completo per SDK con esempi TypeScript
- Sezione JS/TS SDK aggiunta al README principale
- Roadmap aggiornato: npm SDK ✅

---

## [0.5.3] - 2026-02-20

### ✨ Added
- **Web Dashboard** — dashboard completa servita da FastAPI su `/dashboard`. HTML inline con CSS + JS vanilla, zero dipendenze extra. 7 sezioni: Overview, Memories, Tags, Relations, Timeline, Maintenance, Backup. Dark theme, responsive, agent selector
- **CSP dinamico** — Content Security Policy allargato solo per `/dashboard` (inline styles/scripts + Google Fonts), restrittivo per tutte le API

### 🧪 Testing
- 7 nuovi test dashboard (route, sezioni, CSP, branding, JS helpers)
- Total test suite: **91 tests** ✅

### 📚 Documentation
- README: aggiunta sezione Web Dashboard con tabella feature, aggiornata roadmap (dashboard completata), aggiunto `/dashboard` alla API reference

---

## [0.5.2] - 2026-02-20

### 🔧 Fixed
- **Public exports** — `KoreClient`, `AsyncKoreClient`, e tutte le eccezioni ora esportati da `src/__init__.py` (`from src import KoreClient`)
- **README imports** — aggiornati tutti gli esempi da `from src.client import` a `from src import`

---

## [0.5.1] - 2026-02-20

### ✨ Added
- **Python SDK** — `KoreClient` (sync) and `AsyncKoreClient` (async) with type-safe wrappers for all 17 API endpoints. Typed exceptions (`KoreAuthError`, `KoreNotFoundError`, `KoreValidationError`, `KoreRateLimitError`, `KoreServerError`). Context manager support (`with` / `async with`). Returns Pydantic models, zero duplication (`src/client.py`)

### 🧪 Testing
- 35 new SDK tests (15 unit + 20 integration via ASGI transport)
- Total test suite: **84 tests** ✅

### 📚 Documentation
- README: added Python SDK section with sync/async examples, error handling, and methods table
- CHANGELOG: updated with SDK details
- Roadmap: Python SDK marked as complete

---

## [0.5.0] - 2026-02-20

### ✨ Added
- **MCP Server** — native Model Context Protocol integration for Claude, Cursor, and any MCP client (`kore-mcp` command). 6 tools: save, search, timeline, decay, compress, export. 1 resource: `kore://health`
- **Tags** — tag any memory, search by tag, agent-scoped. Normalized to lowercase, duplicates ignored (`POST/DELETE/GET /memories/{id}/tags`, `GET /tags/{tag}/memories`)
- **Relations** — bidirectional knowledge graph between memories. Cross-agent linking prevented (`POST/GET /memories/{id}/relations`)
- **Batch API** — save up to 100 memories in a single request (`POST /save/batch`)
- **TTL (Time-to-Live)** — set `ttl_hours` on save for auto-expiring memories. Expired memories filtered from search, timeline, export. Manual cleanup via `POST /cleanup`, automatic cleanup integrated into decay pass
- **Export / Import** — full JSON backup of active memories (`GET /export`, `POST /import`). Expired memories excluded from export. Import skips invalid records gracefully
- **Pagination** — `offset` + `has_more` on `/search` and `/timeline` endpoints
- **Centralized config** — all env vars in `src/config.py` (9 configurable options)
- **Vector index cache** — in-memory embedding cache with per-agent invalidation for faster semantic search
- **Python SDK** — `KoreClient` (sync) and `AsyncKoreClient` (async) with type-safe wrappers for all 17 API endpoints. Typed exceptions (`KoreAuthError`, `KoreNotFoundError`, `KoreValidationError`, `KoreRateLimitError`, `KoreServerError`). Context manager support (`with` / `async with`). Returns Pydantic models, zero duplication
- **OOM protection** — embedding input capped at `KORE_MAX_EMBED_CHARS` (default 8000)
- **Concurrency locks** — non-blocking threading locks for decay and compression passes

### 🗄️ Database
- Added `memory_tags` table (memory_id, tag) with tag index
- Added `memory_relations` table (source_id, target_id, relation) with bidirectional indexes
- Added `expires_at` column to memories table with migration for existing DBs

### 🧪 Testing
- Test suite expanded from 17 to **84 tests** covering all P3 features + SDK
- Tests for: batch API, tags (7), relations (5), TTL/cleanup (8), export/import (5), pagination (3)
- SDK tests: 15 unit (helpers, exceptions, class structure) + 20 integration (all endpoints via ASGI transport)
- Rate limiter reset in `setup_method` to prevent 429 interference between test classes

### 📚 Documentation
- README rewritten: comparison table (+5 features), key features (+5 sections), complete API reference organized by category, MCP Server section with Claude/Cursor config, Python SDK section with sync/async examples, full env var documentation, updated roadmap

### 📦 Installation
- New optional dependency group: `mcp` (`pip install kore-memory[mcp]`)
- New entry point: `kore-mcp` for MCP server

---

## [0.4.0] - 2026-02-20

### 🔐 Security
- Added rate limiting middleware (10 requests/second per IP)
- Implemented CORS middleware with configurable origins
- Added comprehensive security headers (X-Frame-Options, X-Content-Type-Options, CSP)
- Added global error handler to prevent information leakage
- Enabled SSL verification on httpx client (controlled via `WP_SSL_VERIFY` env var)
- Sanitized credentials in maintenance templates

### 🗄️ Database
- Fixed `KORE_DB_PATH` to resolve at runtime instead of import-time
- Switched all timestamps to UTC (via `datetime.now(UTC)`)
- Improved FTS5 query sanitization (prevent SQL injection)
- Added batch decay updates for better performance
- Made embedding generation resilient to failures

### 🧪 Testing
- Fixed test suite: explicit `init_db()` call before TestClient initialization
- All 17 tests passing ✅

### 📚 Documentation
- Added `CLAUDE.md` (project context for AI assistants)
- Added competitive analysis vs Mem0, Letta, Zep
- Improved README with deployment section

### 🛠️ Fixes
- Fixed CLI bug: corrected module path from `kore.src.main:app` to `src.main:app` (ModuleNotFoundError)
- Created `kore-daemon.sh` for proper daemonization with `.env` support
- Updated `start.sh` to load environment variables correctly

### ⚡ Performance
- Optimized memory decay calculations
- Batch processing for compression operations

---

## [0.3.1] - 2026-02-19

### ✨ Added
- Semantic search with multilingual embeddings (50+ languages)
- Memory compression (auto-merge similar memories)
- Timeline API (chronological memory traces)
- Agent namespace isolation
- Auto-importance scoring (no LLM required)
- Memory decay using Ebbinghaus forgetting curve

### 🔐 Security
- API key authentication
- Agent-scoped access control
- Timing-safe key comparison

### 📦 Installation
- Published to PyPI as `kore-memory`
- CLI command `kore` available after install
- Optional `[semantic]` extras for embeddings

---

## [0.3.0] - 2026-02-18

### 🎉 Initial Public Release
- Core memory storage with SQLite + FTS5
- REST API (FastAPI)
- Basic search and CRUD operations
- Offline-first architecture
- Zero external dependencies for core features

---

## Version Naming

- **0.8.x** — Developer experience, LangChain/CrewAI, dashboard UX
- **0.7.x** — Performance, security, 30 issues resolved
- **0.6.x** — Package rename, cursor-based pagination
- **0.5.x** — MCP, tags, relations, TTL, batch API, Python SDK
- **0.4.x** — Security & stability improvements
- **0.3.x** — Semantic search & compression
- **0.2.x** — Internal testing (not released)
- **0.1.x** — Initial development

---

[0.8.0]: https://github.com/auriti-labs/kore-memory/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/auriti-labs/kore-memory/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/auriti-labs/kore-memory/compare/v0.5.4...v0.6.0
[0.5.4]: https://github.com/auriti-labs/kore-memory/compare/v0.5.3...v0.5.4
[0.5.3]: https://github.com/auriti-labs/kore-memory/compare/v0.5.2...v0.5.3
[0.5.2]: https://github.com/auriti-labs/kore-memory/compare/v0.5.1...v0.5.2
[0.5.1]: https://github.com/auriti-labs/kore-memory/compare/v0.5.0...v0.5.1
[0.5.0]: https://github.com/auriti-labs/kore-memory/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/auriti-labs/kore-memory/compare/v0.3.1...v0.4.0
[0.3.1]: https://github.com/auriti-labs/kore-memory/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/auriti-labs/kore-memory/releases/tag/v0.3.0
