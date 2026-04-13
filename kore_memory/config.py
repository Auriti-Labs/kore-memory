"""
Kore — Centralized configuration
All environment variables and constants in a single place.
"""

import os
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────

_PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = _PROJECT_ROOT / "data"

# Database
DEFAULT_DB_PATH = str(DATA_DIR / "memory.db")
DB_PATH = os.getenv("KORE_DB_PATH", DEFAULT_DB_PATH)

# API key
API_KEY_FILE = DATA_DIR / ".api_key"

# ── Server ────────────────────────────────────────────────────────────────────

HOST = os.getenv("KORE_HOST", "127.0.0.1")
PORT = int(os.getenv("KORE_PORT", "8765"))
LOCAL_ONLY = os.getenv("KORE_LOCAL_ONLY", "1") == "1"

# ── CORS ──────────────────────────────────────────────────────────────────────

CORS_ORIGINS = [o.strip() for o in os.getenv("KORE_CORS_ORIGINS", "").split(",") if o.strip()]

# ── Rate limiting ─────────────────────────────────────────────────────────────

RATE_LIMITS: dict[str, tuple[int, int]] = {
    "/save": (30, 60),  # 30 req/min
    "/search": (60, 60),  # 60 req/min
    "/timeline": (60, 60),  # 60 req/min
    "/decay/run": (5, 3600),  # 5 req/hour
    "/compress": (2, 3600),  # 2 req/hour
    "/export": (10, 3600),  # 10 req/hour
    "/import": (5, 3600),  # 5 req/hour
    "/cleanup": (10, 3600),  # 10 req/hour
    "/delete": (120, 60),  # 120 delete/min
}

# ── Embedder ──────────────────────────────────────────────────────────────────

EMBED_MODEL = os.getenv("KORE_EMBED_MODEL", "paraphrase-multilingual-MiniLM-L12-v2")
MAX_EMBED_CHARS = int(os.getenv("KORE_MAX_EMBED_CHARS", "8000"))
EMBED_BACKEND = os.getenv("KORE_EMBED_BACKEND", "")  # "onnx" for ONNX backend

# ── Compressor ────────────────────────────────────────────────────────────────

SIMILARITY_THRESHOLD = float(os.getenv("KORE_SIMILARITY_THRESHOLD", "0.88"))

# ── Auto-tuner ───────────────────────────────────────────────────────────────

AUTO_TUNE = os.getenv("KORE_AUTO_TUNE", "0") == "1"

# ── Entity extraction ────────────────────────────────────────────────────────

ENTITY_EXTRACTION = os.getenv("KORE_ENTITY_EXTRACTION", "0") == "1"

# ── Audit log ────────────────────────────────────────────────────────────────

AUDIT_LOG = os.getenv("KORE_AUDIT_LOG", "0") == "1"

# ── Conflict Detection ───────────────────────────────────────────────────────

# Soglia similarità coseno per candidato conflitto (default calcolato empiricamente)
CONFLICT_SIMILARITY = float(os.getenv("KORE_CONFLICT_SIMILARITY", "0.75"))
# Confidence minima della memoria nuova per attivare il conflict check
CONFLICT_MIN_CONFIDENCE = float(os.getenv("KORE_CONFLICT_MIN_CONFIDENCE", "0.70"))
# Esecuzione sincrona (True) vs asincrona (False)
CONFLICT_SYNC = os.getenv("KORE_CONFLICT_SYNC", "true").lower() == "true"
# Numero massimo di memorie candidate da scansionare
CONFLICT_MAX_CANDIDATES = int(os.getenv("KORE_CONFLICT_MAX_CANDIDATES", "10"))

# ── Version ───────────────────────────────────────────────────────────────────

VERSION = "2.0.0"
