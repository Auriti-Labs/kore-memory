"""
Configurazione condivisa per i benchmark.
Crea un DB temporaneo isolato da quelli dei test unitari.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def bench_client():
    """TestClient con DB temporaneo dedicato ai benchmark."""
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

    _pool.clear()
    Path(db_path).unlink(missing_ok=True)
