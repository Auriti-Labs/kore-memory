# Kore Memory — Quick Start v2.1

> Guida rapida alle 4 superfici di prodotto: REST API, Python SDK, MCP Server, JS/TS SDK.

---

## 1. Installazione

```bash
pip install kore-memory          # core (FTS5, decay, auto-scoring, MCP stdio)
pip install 'kore-memory[semantic]'  # + semantic search (sentence-transformers + sqlite-vec)
pip install 'kore-memory[mcp]'   # + MCP server (streamable-http)
pip install 'kore-memory[all]'   # tutto
```

Avvia il server:

```bash
kore                             # localhost:8765
kore --port 9000 --reload        # dev mode
```

---

## 2. REST API

### Salva una memoria

```bash
curl -X POST http://localhost:8765/save \
  -H "Content-Type: application/json" \
  -H "X-Agent-Id: my-agent" \
  -d '{"content": "Il deployment usa Kubernetes con ArgoCD", "category": "decision"}'
# {"id": 1, "importance": 4, "conflicts": []}
```

### Layer Temporale (v2.1)

```bash
# Memoria con validità temporale
curl -X POST http://localhost:8765/save \
  -H "X-Agent-Id: my-agent" \
  -d '{
    "content": "La feature flag X è attiva in produzione",
    "category": "project",
    "valid_from": "2026-01-01T00:00:00Z",
    "valid_to": "2026-06-30T23:59:59Z",
    "confidence": 0.9
  }'

# Supersede una memoria obsoleta
curl -X POST http://localhost:8765/save \
  -d '{
    "content": "La feature flag X è stata rimossa",
    "supersedes_id": 1,
    "confidence": 1.0
  }'

# Cronologia di una memoria
curl http://localhost:8765/memories/1/history
```

### Conflict Detection (v2.1)

Il campo `conflicts` nella risposta di `/save` contiene gli ID delle memorie in conflitto rilevate automaticamente:

```bash
curl -X POST http://localhost:8765/save \
  -d '{"content": "Il database usa PostgreSQL", "confidence": 0.9}'
# {"id": 5, "importance": 3, "conflicts": []}

curl -X POST http://localhost:8765/save \
  -d '{"content": "Il database usa MySQL", "confidence": 0.9}'
# {"id": 6, "importance": 3, "conflicts": ["c-abc123"]}
#  ↑ conflitto rilevato con la memoria 5
```

Configura le soglie:

```bash
KORE_CONFLICT_SIMILARITY=0.75     # soglia coseno (default)
KORE_CONFLICT_MIN_CONFIDENCE=0.70 # confidence minima per il check
KORE_CONFLICT_SYNC=true           # sincrono (default)
```

### Ricerca semantica

```bash
curl "http://localhost:8765/search?q=database+postgresql&limit=5&semantic=true" \
  -H "X-Agent-Id: my-agent"
```

Il Ranking Engine v1 ordina i risultati per: `similarity×0.50 + decay×0.25 + confidence×0.20 + freshness×0.05`.

---

## 3. Python SDK

```python
from kore_memory import KoreClient

with KoreClient("http://localhost:8765", agent_id="my-agent") as kore:
    # Salva con validità temporale
    result = kore.save(
        "PostgreSQL scelto per il progetto Alpha",
        category="decision",
    )
    print(result.id, result.importance, result.conflicts)

    # Ricerca semantica
    hits = kore.search("database relazionale", limit=5)
    for mem in hits.results:
        print(f"{mem.content!r} score={mem.score:.3f}")

    # Tags e relazioni
    kore.add_tags(result.id, ["database", "infra"])
    other = kore.save("Redis per la cache", category="decision")
    kore.add_relation(result.id, other.id, "related")

    # Manutenzione
    kore.decay_run()
    kore.compress()
```

### Async

```python
from kore_memory import AsyncKoreClient

async with AsyncKoreClient("http://localhost:8765", agent_id="my-agent") as kore:
    result = await kore.save("Async memory test")
    hits = await kore.search("memory", semantic=True)
```

---

## 4. MCP Server

### Claude Code (stdio — default)

Copia il preset pronto:

```bash
pip install 'kore-memory[mcp]'
cp presets/claude-code/mcp.json ~/.claude/mcp.json
```

Oppure aggiungi manualmente a `~/.claude/mcp.json`:

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

### Cursor (streamable-http)

```bash
cp presets/cursor/mcp.json ~/.cursor/mcp.json
```

Oppure manualmente:

```json
{
  "mcpServers": {
    "kore-memory": {
      "command": "kore-mcp",
      "args": ["--transport", "streamable-http"],
      "env": { "KORE_LOCAL_ONLY": "1" }
    }
  }
}
```

### Istanza remota con Bearer Auth (v2.1)

Per esporre il server MCP su rete remota in modo sicuro:

```bash
# Genera un token sicuro
export KORE_MCP_TOKEN=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")

# Avvia con auth attiva (attenzione: bind su 0.0.0.0 solo con token!)
KORE_MCP_TOKEN=$KORE_MCP_TOKEN kore-mcp --transport streamable-http --host 0.0.0.0 --port 8766
```

Il client deve passare `Authorization: Bearer <token>`. Il path `/mcp/health` è sempre esente.

Configurazione MCP client remoto:

```json
{
  "mcpServers": {
    "kore-memory": {
      "command": "kore-mcp",
      "args": ["--transport", "streamable-http", "--host", "0.0.0.0"],
      "env": {
        "KORE_MCP_TOKEN": "your-secret-token",
        "KORE_DB_PATH": "/data/kore/memory.db"
      }
    }
  }
}
```

### Tool MCP disponibili (v2.1 — 17 tool)

| Tool | Descrizione |
|------|-------------|
| `memory_save` | Salva memoria (importance=0 → auto-score) |
| `memory_search` | Ricerca semantica/FTS5 |
| `memory_timeline` | Cronologia su un argomento |
| `memory_save_batch` | Salva fino a 100 memorie |
| `memory_update` | Aggiorna una memoria |
| `memory_delete` | Elimina una memoria |
| `memory_add_tags` | Aggiunge tag |
| `memory_search_by_tag` | Ricerca per tag |
| `memory_add_relation` | Crea relazione tra memorie |
| `memory_export` | Esporta tutte le memorie |
| `memory_import` | Importa da JSON |
| `memory_decay_run` | Ricalcola decay scores |
| `memory_compress` | Comprimi memorie simili |
| `memory_cleanup` | Elimina memorie scadute |
| `memory_save_decision` | Salva ADR con metadata strutturati (v2.1) |
| `memory_get_runbook` | Recupera runbook operativi (v2.1) |
| `memory_log_regression` | Traccia regressioni con versione introdotta/fixata (v2.1) |

#### Coding Memory Mode (v2.1)

```python
# Salva una decisione architettuale (ADR)
memory_save_decision(
    content="Usiamo PostgreSQL invece di MySQL",
    rationale="Supporto migliore per JSONB e query avanzate",
    alternatives_considered="MySQL, SQLite, MongoDB",
    decided_by="team-backend",
    repo="my-project",
)

# Recupera runbook per un componente
memory_get_runbook(trigger="deploy failed", component="api-gateway")

# Traccia una regressione
memory_log_regression(
    content="Race condition nel pool connessioni SQLite",
    introduced_in="v1.2.0",
    fixed_in="v1.2.1",
    test_ref="tests/test_database.py::test_concurrent_access",
)
```

---

## 5. JavaScript/TypeScript SDK

```bash
npm install kore-memory-client
```

```typescript
import { KoreClient } from 'kore-memory-client';

const kore = new KoreClient({ baseUrl: 'http://localhost:8765', agentId: 'my-agent' });

// Salva
const mem = await kore.save({ content: 'TypeScript is great', category: 'preference' });

// Cerca
const results = await kore.search({ q: 'typescript', limit: 5 });
results.results.forEach(r => console.log(r.content, r.score));

// Manutenzione
await kore.decayRun();
await kore.compress();
```

---

## 6. Variabili d'ambiente principali

| Variabile | Default | Descrizione |
|-----------|---------|-------------|
| `KORE_DB_PATH` | `data/memory.db` | Percorso del database |
| `KORE_LOCAL_ONLY` | `1` | Salta auth per localhost |
| `KORE_MCP_TOKEN` | *(vuoto)* | Bearer token per MCP remoto (v2.1) |
| `KORE_MCP_PORT` | `8766` | Porta MCP HTTP |
| `KORE_MCP_TIMEOUT_SECONDS` | `30` | Timeout connessioni MCP |
| `KORE_EMBED_MODEL` | `paraphrase-multilingual-MiniLM-L12-v2` | Modello embedding |
| `KORE_CONFLICT_SIMILARITY` | `0.75` | Soglia conflict detection (v2.1) |
| `KORE_CONFLICT_SYNC` | `true` | Conflict detection sincrona (v2.1) |

---

## 7. Health check

```bash
# REST API
curl http://localhost:8765/health

# MCP server (streamable-http)
curl http://localhost:8766/mcp/health
# {"status": "ok", "uptime_seconds": 42.1, "version": "2.1.0"}
```
