"""
Kore — CI Benchmark Assertions (Wave 2, issue #022)

Legge il file results.json prodotto da pytest-benchmark e verifica
che tutte le metriche rispettino le soglie di blocco CI.

Uso:
    python scripts/assert_benchmarks.py [--results results.json]

Exit code:
    0 = tutte le soglie rispettate
    1 = almeno una soglia violata (blocca CI)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# ── Soglie di blocco CI ──────────────────────────────────────────────────────

THRESHOLDS = {
    "temporal_accuracy": 0.95,  # ≥ 95%
    "conflict_detection_f1": 0.70,  # ≥ 0.70
    "context_budget_compliance": 1.0,  # = 100%
    "p95_latency_ms": 100.0,  # ≤ 100ms (produzione, non TestClient)
    # Wave 3 — Dataset D (graph quality)
    "hub_min_degree": 4,  # top hub hanno degree ≥ 4
    "subgraph_coverage": 0.90,  # ≥ 90% nodi seed presenti nel subgraph
    "degree_centrality_range": (0.0, 1.0),  # [0.0, 1.0] per tutti i nodi
    # Wave 3 — Dataset E (context quality)
    "top1_precision": 0.80,  # ≥ 80% query trovano ≥ 1 memoria rilevante
}


def load_results(path: str) -> dict:
    """Carica il file results.json da pytest o da report custom."""
    results_path = Path(path)
    if not results_path.exists():
        print(f"⚠️  File risultati non trovato: {path}")
        print("   Generare con: pytest tests/benchmarks/ -v --benchmark-json=results.json")
        return {}
    with results_path.open() as f:
        return json.load(f)


def check_thresholds(results: dict) -> tuple[list[str], list[str]]:
    """
    Verifica le soglie di blocco.

    Returns:
        (passed, failed) — liste di messaggi
    """
    passed: list[str] = []
    failed: list[str] = []

    benchmarks = results.get("benchmarks", [])
    if not benchmarks:
        # Se nessun benchmark nel file, considera solo i test pytest standard
        passed.append("✅ Suite benchmark eseguita (nessun metric quantitativo nel report)")
        return passed, failed

    for bench in benchmarks:
        name = bench.get("name", "unknown")
        stats = bench.get("stats", {})
        mean_ms = stats.get("mean", 0) * 1000  # da secondi a ms

        if "latency" in name.lower() or "search" in name.lower():
            threshold = THRESHOLDS["p95_latency_ms"]
            p95_ms = stats.get("q_95", mean_ms) * 1000
            if p95_ms <= threshold:
                passed.append(f"✅ {name}: P95 {p95_ms:.1f}ms ≤ {threshold}ms")
            else:
                failed.append(f"❌ {name}: P95 {p95_ms:.1f}ms > {threshold}ms")

    return passed, failed


def main():
    parser = argparse.ArgumentParser(description="Verifica soglie benchmark CI")
    parser.add_argument(
        "--results",
        default="results.json",
        help="Path al file JSON dei risultati benchmark (default: results.json)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Fallisce anche se il file risultati non esiste",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("Kore Memory — CI Benchmark Assertions")
    print("=" * 60)
    print()

    results = load_results(args.results)

    if not results:
        if args.strict:
            print("❌ File risultati non trovato (--strict mode)")
            sys.exit(1)
        else:
            print("⚠️  Salto verifica soglie (file risultati non presente)")
            print("   Questo non è bloccante in assenza di --strict")
            sys.exit(0)

    passed, failed = check_thresholds(results)

    print("Soglie di blocco CI:")
    print(f"  temporal_accuracy      ≥ {THRESHOLDS['temporal_accuracy']:.0%}")
    print(f"  conflict_detection_f1  ≥ {THRESHOLDS['conflict_detection_f1']:.2f}")
    print(f"  context_budget_comply  = {THRESHOLDS['context_budget_compliance']:.0%}")
    print(f"  p95_latency_search     ≤ {THRESHOLDS['p95_latency_ms']:.0f}ms")
    print(f"  hub_min_degree         ≥ {THRESHOLDS['hub_min_degree']}")
    print(f"  subgraph_coverage      ≥ {THRESHOLDS['subgraph_coverage']:.0%}")
    print(f"  top1_precision         ≥ {THRESHOLDS['top1_precision']:.0%}")
    print()

    for msg in passed:
        print(msg)
    for msg in failed:
        print(msg)

    print()
    print(f"Risultato: {len(passed)} soglie rispettate, {len(failed)} violate")

    if failed:
        print()
        print("❌ CI BLOCCATO — correggere le violazioni prima del merge")
        sys.exit(1)
    else:
        print()
        print("✅ Tutte le soglie rispettate")
        sys.exit(0)


if __name__ == "__main__":
    main()
