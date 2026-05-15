"""
Kore — Centralized configuration
All environment variables and constants in a single place.
"""

import ipaddress
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

# ── Trusted Proxies ───────────────────────────────────────────────────────────

# Lista di IP/subnet attendibili per X-Forwarded-For (default: RFC1918 + localhost)
TRUSTED_PROXIES_RAW = os.getenv("KORE_TRUSTED_PROXIES", "127.0.0.0/8,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16")


def _parse_trusted_proxies(raw: str) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    """Parse comma-separated list of IP networks into ipaddress objects."""
    networks = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            networks.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            pass  # Skip invalid entries
    return networks


TRUSTED_PROXIES = _parse_trusted_proxies(TRUSTED_PROXIES_RAW)

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

# ── MCP Server ───────────────────────────────────────────────────────────────

MCP_PORT = int(os.getenv("KORE_MCP_PORT", "8766"))
# Timeout in secondi per le connessioni streamable-http (usato nel client-side keepalive)
MCP_TIMEOUT_SECONDS = int(os.getenv("KORE_MCP_TIMEOUT_SECONDS", "30"))
# Bearer token per autenticazione HTTP remota (vuoto = nessun auth, solo locale)
MCP_TOKEN = os.getenv("KORE_MCP_TOKEN", "")

# ── Conflict Detection ───────────────────────────────────────────────────────

# Soglia similarità coseno per candidato conflitto (default calcolato empiricamente)
CONFLICT_SIMILARITY = float(os.getenv("KORE_CONFLICT_SIMILARITY", "0.75"))
# Confidence minima della memoria nuova per attivare il conflict check
CONFLICT_MIN_CONFIDENCE = float(os.getenv("KORE_CONFLICT_MIN_CONFIDENCE", "0.70"))
# Esecuzione sincrona (True) vs asincrona (False)
CONFLICT_SYNC = os.getenv("KORE_CONFLICT_SYNC", "true").lower() == "true"
# Numero massimo di memorie candidate da scansionare
CONFLICT_MAX_CANDIDATES = int(os.getenv("KORE_CONFLICT_MAX_CANDIDATES", "10"))

# ── Redis Cache (opzionale) ──────────────────────────────────────────────────

REDIS_ENABLED = os.getenv("KORE_REDIS_ENABLED", "0") == "1"
REDIS_HOST = os.getenv("KORE_REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("KORE_REDIS_PORT", "6379"))
REDIS_DB = int(os.getenv("KORE_REDIS_DB", "0"))
REDIS_PASSWORD = os.getenv("KORE_REDIS_PASSWORD", "")
REDIS_PREFIX = os.getenv("KORE_REDIS_PREFIX", "kore:")
# TTL default in secondi (0 = nessun expiry)
REDIS_TTL = int(os.getenv("KORE_REDIS_TTL", "300"))
# TTL specifici per tipo di cache
REDIS_SEARCH_TTL = int(os.getenv("KORE_REDIS_SEARCH_TTL", "60"))
REDIS_GRAPH_TTL = int(os.getenv("KORE_REDIS_GRAPH_TTL", "120"))
REDIS_ANALYTICS_TTL = int(os.getenv("KORE_REDIS_ANALYTICS_TTL", "300"))

# ── Version ───────────────────────────────────────────────────────────────────

VERSION = "3.0.3"
