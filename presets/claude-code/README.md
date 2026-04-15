# Kore Memory — Preset Claude Code

Configura Kore Memory come MCP server per Claude Code in 3 comandi.

## Quick Start

```bash
# 1. Installa Kore Memory
pip install kore-memory

# 2. Copia il preset nella config di Claude Code
cp mcp.json ~/.claude/mcp.json

# 3. Avvia Claude Code — i tool sono disponibili immediatamente
claude
```

> **Time-to-first-value:** ≤ 10 minuti su fresh environment.

## Tool disponibili

| Tool | Descrizione |
|------|-------------|
| `memory_save` | Salva una memoria con categoria e importanza |
| `memory_search` | Ricerca semantica/FTS5 nelle memorie |
| `memory_timeline` | Cronologia su un argomento |
| `memory_save_batch` | Salva più memorie in una sola chiamata |
| `memory_update` | Aggiorna una memoria esistente |
| `memory_delete` | Elimina una memoria |
| `memory_add_tags` | Aggiunge tag a una memoria |
| `memory_search_by_tag` | Ricerca per tag |
| `memory_add_relation` | Crea relazione tra memorie (Graph RAG) |
| `memory_export` | Esporta tutte le memorie |
| `memory_import` | Importa memorie da JSON |
| `memory_decay_run` | Ricalcola decay scores |
| `memory_compress` | Comprimi memorie simili |
| `memory_cleanup` | Elimina memorie scadute |
| `memory_get_context` | Assembla context package ottimizzato per un task (budget token) |
| `memory_explain` | Analisi completa di una memoria (status, conditions, score breakdown) |
| `memory_save_decision` | **[Coding]** Salva ADR con rationale e alternative considerate |
| `memory_log_root_cause` | **[Coding]** Registra root cause analysis di un bug |
| `memory_log_regression` | **[Coding]** Traccia una regressione con versione e test_ref |
| `memory_get_runbook` | **[Coding]** Recupera runbook per trigger/componente |

## Configurazione avanzata

```json
{
  "mcpServers": {
    "kore-memory": {
      "command": "kore-mcp",
      "args": ["--transport", "streamable-http", "--port", "8766"],
      "env": {
        "KORE_DB_PATH": "/custom/path/memory.db",
        "KORE_LOCAL_ONLY": "1",
        "KORE_CONFLICT_SYNC": "true"
      }
    }
  }
}
```

## Variabili d'ambiente utili

| Variabile | Default | Descrizione |
|-----------|---------|-------------|
| `KORE_DB_PATH` | `data/memory.db` | Percorso del database |
| `KORE_LOCAL_ONLY` | `1` | Disabilita autenticazione su localhost |
| `KORE_MCP_TIMEOUT_SECONDS` | `30` | Timeout connessioni HTTP |
| `KORE_CONFLICT_SYNC` | `true` | Rilevamento conflitti sincrono |
| `KORE_EMBED_MODEL` | `paraphrase-multilingual-MiniLM-L12-v2` | Modello embedding |

## Troubleshooting

**Il server non parte:**
```bash
# Verifica installazione
kore-mcp --help

# Avvia manualmente per vedere gli errori
kore-mcp --transport streamable-http
```

**Le memorie non vengono trovate:**
```bash
# Verifica il percorso del DB
echo $KORE_DB_PATH

# Esporta le memorie esistenti
# Nel tool: memory_export(agent_id="default")
```
