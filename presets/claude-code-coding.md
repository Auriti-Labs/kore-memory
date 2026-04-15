# Kore Memory — Coding Memory Mode Preset

Aggiungi questa sezione al tuo `CLAUDE.md` (globale o di progetto) per attivare il Coding Memory Mode.
Sostituisci `YOUR_PROJECT` con il nome del tuo repository.

---

```markdown
## Kore Memory — Coding Mode

### Setup
MCP server `kore-memory` attivo via `kore-mcp`. Namespace progetto: `YOUR_PROJECT`.

### All'inizio di ogni sessione
Esegui:
```
memory_get_context(task="<descrizione del task corrente>", ranking_profile="coding", agent_id="default/YOUR_PROJECT")
```
Usa il contesto per recuperare decisioni architetturali, runbook e root cause precedenti.

### Quando prendere una decisione architetturale
```
memory_save_decision(
    content="<decisione>",
    rationale="<perché>",
    alternatives_considered="<opzioni scartate>",
    repo="YOUR_PROJECT"
)
```

### Quando risolvi un bug (root cause)
```
memory_log_root_cause(
    content="<causa radice>",
    symptom="<sintomo osservato>",
    affected_component="<modulo/file>",
    fix_applied="<cosa hai cambiato>",
    repo="YOUR_PROJECT"
)
```

### Quando trovi una regressione già vista
```
memory_log_regression(
    content="<descrizione>",
    introduced_in="<versione/commit>",
    fixed_in="<versione/commit>",
    test_ref="<file::test>",
    repo="YOUR_PROJECT"
)
```

### Per procedure operative (deploy, rollback, ecc.)
Prima di eseguire procedure critiche:
```
memory_get_runbook(trigger="<azione>", component="<componente>", repo="YOUR_PROJECT")
```

Per salvare un nuovo runbook:
```
memory_save(content="<procedura passo-passo>", category="runbook", importance=4)
```
Con `X-Agent-Id: default/YOUR_PROJECT`.

### explain=true
Per capire perché una decisione è stata surfaced nel context package:
```
memory_get_context(task="...", ranking_profile="coding", agent_id="default/YOUR_PROJECT")
```
Il campo `memories[].score_breakdown` mostra la decomposizione del ranking.
```

---

## Tool coding disponibili (v2.5.0)

| Tool MCP | Categoria | Descrizione |
|----------|-----------|-------------|
| `memory_save_decision` | `architectural_decision` | ADR con rationale e alternative |
| `memory_log_root_cause` | `root_cause` | Bug investigation e fix |
| `memory_log_regression` | `regression_note` | Regressions tracciate con test_ref |
| `memory_get_runbook` | `runbook` | Recupera procedure per trigger/componente |
| `memory_get_context` | — | Context package con `ranking_profile: "coding"` |
| `memory_search` | — | Ricerca semantica su tutte le categorie |
| `memory_save` | qualsiasi | Salvataggio generico |

## Installazione watcher (opzionale)

Per la sincronizzazione automatica dei file (es. CLAUDE.md):

```bash
pip install 'kore-memory[watcher]'
# Poi via REST o MCP:
POST /overlay/watch  {"base_path": "/path/to/project"}
```

Ogni modifica a file `.md`, `.py`, `.toml` ecc. viene re-indicizzata entro 1 secondo.
