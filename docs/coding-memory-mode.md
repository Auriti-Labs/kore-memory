# Coding Memory Mode — Guida Completa

> **Versione**: v2.5.0 | **Status**: GA

Kore Memory offre un profilo specializzato per il ciclo di sviluppo software: il **Coding Memory Mode**. 
Ottimizza il retrieval per decisioni architetturali, root cause analysis, runbook operativi e regressions 
già viste — tutto associato al repository specifico.

---

## Setup in < 10 minuti

### 1. Installa Kore Memory

```bash
pip install kore-memory
kore                         # avvia su localhost:8765
```

### 2. Configura il MCP server in Claude Code

Aggiungi a `~/.claude.json` (o `.mcp.json` nel progetto):

```json
{
  "mcpServers": {
    "kore-memory": {
      "command": "kore-mcp",
      "args": [],
      "env": {}
    }
  }
}
```

### 3. Aggiungi il preset coding al tuo CLAUDE.md

Copia la sezione dal file `presets/claude-code-coding.md` (incluso in questo repo) 
nel tuo `CLAUDE.md` di progetto o nel globale `~/.claude/CLAUDE.md`.

---

## Concetti chiave

### Repository-scoped namespace

Ogni repository ottiene il suo namespace separato via `repo` parameter:

```
agent_id: "default" + repo: "my-project"  →  namespace: "default/my-project"
```

Tutti i tool coding accettano il parametro `repo`. I dati di un repo non contaminano mai un altro.

### Tipi di memoria coding

| Categoria | Tipo memoria | Quando usare |
|-----------|-------------|--------------|
| `architectural_decision` | semantic | Scelte tecnologiche, pattern, infrastruttura |
| `root_cause` | episodic | Bug investigations, incident post-mortem |
| `runbook` | procedural | Deploy, rollback, procedure operative |
| `regression_note` | episodic | Bug risolti che potrebbero tornare |

### Ranking profile "coding"

Il profilo `coding` pesa diversamente rispetto al default:

| Signal | Default | Coding |
|--------|---------|--------|
| similarity | 45% | 40% |
| decay_score | 25% | 18% |
| confidence | 15% | 15% |
| importance_n | 0% | 8% |
| task_relevance | 10% | 12% |
| graph_centrality | 0% | 5% |
| freshness | 5% | 2% |

Le decisioni ad alta importanza (4-5) emergono più facilmente. Il graph centrality valorizza 
le decisioni interconnesse a più componenti del sistema.

---

## MCP Tools disponibili

### `memory_save_decision` — Salva una decisione architetturale (ADR)

```python
memory_save_decision(
    content="Usiamo PostgreSQL invece di MySQL per il DB principale",
    rationale="Supporto nativo JSONB, estensioni PostGIS, better full-text search",
    alternatives_considered="MySQL 8.0, CockroachDB, SQLite",
    decided_by="team-backend",
    repo="my-project",
)
```

### `memory_log_root_cause` — Registra una root cause analysis

```python
memory_log_root_cause(
    content="Il connection pool non gestiva i timeout su query lente, bloccando tutti gli slot",
    symptom="API timeouts a cascata ogni ~2 ore",
    affected_component="database/pool",
    fix_applied="Aggiunto statement_timeout=30s e pool_timeout=10s in SQLAlchemy",
    repo="my-project",
)
```

### `memory_log_regression` — Traccia una regressione

```python
memory_log_regression(
    content="Race condition nella cache Redis: SET senza NX causava sovrascrittura",
    introduced_in="v2.3.0",
    fixed_in="v2.3.1",
    test_ref="tests/test_cache.py::test_concurrent_set",
    repo="my-project",
)
```

### `memory_get_runbook` — Recupera runbook per trigger/componente

```python
memory_get_runbook(
    trigger="deploy failed",
    component="api-gateway",
    repo="my-project",
)
```

### `memory_get_context` con profilo coding

```python
memory_get_context(
    task="implementare rate limiting per l'endpoint /search",
    budget_tokens=2000,
    categories="architectural_decision,runbook",
    ranking_profile="coding",
    agent_id="default/my-project",
)
```

Restituisce un **context package** pronto per l'injection nel prompt, con:
- Memorie ordinate per rilevanza (profilo coding)
- `explain=true` per vedere perché ogni memoria è stata inclusa
- Eventuali conflitti tra decisioni non risolti
- Budget token rispettato al 100%

---

## Workflow consigliato

### Inizio sessione

All'inizio di ogni sessione di sviluppo, Claude Code (con kore-mcp attivo) esegue automaticamente:

```
memory_get_context(task="<descrizione del task corrente>", ranking_profile="coding")
```

Ottieni il contesto rilevante delle decisioni passate senza fare nulla.

### Fine sessione

Dopo aver risolto un bug o preso una decisione importante:

```
# Se hai preso una decisione architetturale:
memory_save_decision(content="...", rationale="...", repo="...")

# Se hai trovato e risolto un bug:
memory_log_root_cause(content="...", symptom="...", fix_applied="...", repo="...")

# Se è una regressione già vista:
memory_log_regression(content="...", introduced_in="...", fixed_in="...", repo="...")
```

### Dopo 10+ sessioni

Il sistema accumula contesto. Il ranking degrader mantiene le decisioni recenti in primo piano 
mentre le vecchie scalano gradualmente (ma restano recuperabili con search diretto).

---

## Overlay CLAUDE.md

Se hai il filesystem overlay attivo, Kore indicizza automaticamente il tuo `CLAUDE.md` come memoria. 
Questo significa che le istruzioni di progetto sono recuperabili via context assembler.

```bash
# Avvia overlay per il tuo progetto (richiede kore-memory[watcher])
POST /overlay/watch  {"base_path": "/path/to/project"}
```

Ogni modifica al `CLAUDE.md` viene re-indicizzata entro 1 secondo (debounce).

---

## Benchmark — Precision@5

Il Dataset C (50 query su 300 memorie di sviluppo software) misura:

- **Top-1 precision ≥ 80%**: almeno 1 memoria rilevante nei primi risultati
- **Coding profile relevance**: l'output di `/context/assemble` è ordinato per score

Esegui i benchmark localmente:

```bash
pytest tests/benchmarks/test_benchmarks.py -k "coding" -v
```

---

## Troubleshooting

**Q: Le decisioni vecchie non appaiono nel context package.**  
A: Il decay riduce lo score nel tempo. Usa `memory_search(query="...", semantic=True)` per 
   recuperare anche memorie con decay basso.

**Q: Conflitti rilevati nel context package.**  
A: Due memorie con contenuto simile ma `still_valid: True` entrambe. Risolvi aggiornando 
   quella obsoleta via `memory_update(id=..., metadata={"still_valid": false})`.

**Q: Come uso namespace multipli per microservizi?**  
A: Usa `repo` diverso per ogni servizio: `repo="auth-service"`, `repo="payment-service"`. 
   Il context assembler per default usa solo il namespace corrente.

---

## Riferimenti

- [REST API docs](/docs/api-reference.md)
- [Context Assembler](/docs/context-assembler.md)
- [Filesystem Overlay](/docs/filesystem-overlay.md)
- [MCP Server setup](/docs/mcp-setup.md)
