"""
Kore — Benchmark Dataset Loaders (Wave 2, issue #021)

Carica i dataset sintetici per i benchmark di qualità.
Ogni loader restituisce i dati pronti per l'import via API.
"""

from __future__ import annotations

import json
from pathlib import Path

_DATASETS_DIR = Path(__file__).parent


def load_dataset_a() -> dict:
    """
    Dataset A — Temporal Coherence (200 memorie).
    Testa la corretta gestione di supersessioni, scadenze e conflitti temporali.
    """
    path = _DATASETS_DIR / "dataset_a_temporal.json"
    with path.open() as f:
        return json.load(f)


def load_dataset_b() -> dict:
    """
    Dataset B — Conflict Detection (100 coppie).
    Testa il rilevamento di conflitti fattuali e temporali.
    """
    path = _DATASETS_DIR / "dataset_b_conflicts.json"
    with path.open() as f:
        return json.load(f)


def load_dataset_c() -> dict:
    """
    Dataset C — Coding Scenarios (300 memorie + 50 query).
    Testa il ranking con profilo coding su task di sviluppo software.
    """
    path = _DATASETS_DIR / "dataset_c_coding.json"
    with path.open() as f:
        return json.load(f)


def memories_for_import(dataset: dict) -> list[dict]:
    """Estrae la lista di memorie dal dataset per import via API."""
    return dataset.get("memories", [])


def ground_truth(dataset: dict) -> dict:
    """Estrae il ground truth dal dataset (query → expected IDs per search, label per conflict)."""
    return dataset.get("ground_truth", {})
