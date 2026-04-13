# Kore Memory — Preset Cursor

Configura Kore Memory come MCP server per Cursor in 3 comandi.

## Quick Start

```bash
# 1. Installa Kore Memory
pip install kore-memory

# 2. Copia il preset nella config di Cursor
cp mcp.json ~/.cursor/mcp.json

# 3. Riavvia Cursor — i tool sono disponibili nel pannello MCP
```

> **Time-to-first-value:** ≤ 10 minuti su fresh environment.

## Come funziona

Cursor usa il trasporto `streamable-http` (porta 8766) per comunicare con Kore Memory.
Il server si avvia automaticamente come processo figlio di Cursor.

## Tool disponibili

I 16 tool disponibili nel preset Claude Code:
`memory_save`, `memory_search`, `memory_timeline`, `memory_save_batch`,
`memory_update`, `memory_delete`, `memory_add_tags`, `memory_search_by_tag`,
`memory_add_relation`, `memory_export`, `memory_import`, `memory_decay_run`,
`memory_compress`, `memory_cleanup`, `memory_get_context`, `memory_explain`.

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
        "KORE_MCP_TIMEOUT_SECONDS": "30"
      }
    }
  }
}
```

## Troubleshooting

**Cursor non trova il server:**
```bash
# Verifica che kore-mcp sia nel PATH
which kore-mcp

# Testa manualmente il server HTTP
kore-mcp --transport streamable-http --port 8766

# Verifica l'health endpoint
curl http://localhost:8766/mcp/health
```

**Porta 8766 già in uso:**
Cambia la porta in `mcp.json` e aggiungi `"KORE_MCP_PORT": "8767"` nell'`env`.
