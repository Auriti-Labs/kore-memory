"""
Configurazione condivisa per i benchmark.
Crea un DB temporaneo isolato da quelli dei test unitari.

IMPORTANTE: salva e ripristina KORE_DB_PATH per non contaminare i test unitari
che vengono eseguiti nello stesso processo pytest.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def bench_client():
    """TestClient con DB temporaneo dedicato ai benchmark.

    Salva KORE_DB_PATH originale e lo ripristina al teardown per
    evitare contaminazione dei test unitari eseguiti dopo i benchmark.
    """
    original_db_path = os.environ.get("KORE_DB_PATH")

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    os.environ["KORE_DB_PATH"] = db_path
    os.environ["KORE_TEST_MODE"] = "1"

    from kore_memory.database import _pool, init_db

    _pool.clear()
    init_db()

    from kore_memory.main import _rate_buckets, app

    # Resetta rate limiter: i benchmark caricano molte memorie in rapida sequenza
    _rate_buckets.clear()

    client = TestClient(app)

    yield client

    # Teardown: ripristina env originale per non rompere test successivi
    _pool.clear()
    _rate_buckets.clear()

    if original_db_path is not None:
        os.environ["KORE_DB_PATH"] = original_db_path
    else:
        os.environ.pop("KORE_DB_PATH", None)

    # Re-inizializza il pool sul DB originale
    try:
        init_db()
    except Exception:
        pass

    Path(db_path).unlink(missing_ok=True)
