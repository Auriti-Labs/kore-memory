"""
Kore — Authentication & Authorization
API key validation + agent namespace isolation.

Config via environment variables:
  KORE_API_KEY   — master key (required in non-local mode)
  KORE_LOCAL_ONLY — if "1", skip auth for 127.0.0.1 requests (default: "1")
"""

import os
import secrets

from fastapi import Header, HTTPException, Request, status

from . import config

_KEY_FILE = config.API_KEY_FILE

# ── Key management ────────────────────────────────────────────────────────────


def get_or_create_api_key() -> str:
    """
    Load API key from env or file. Generate and persist one if missing.
    Priority: KORE_API_KEY env → data/.api_key file → auto-generate
    """
    env_key = os.getenv("KORE_API_KEY")
    if env_key:
        return env_key

    if _KEY_FILE.exists():
        return _KEY_FILE.read_text().strip()

    # Auto-generate a secure key on first run
    new_key = secrets.token_urlsafe(32)
    _KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    _KEY_FILE.write_text(new_key)
    _KEY_FILE.chmod(0o600)  # owner read/write only

    # Log key creation with masked value (security: never log full keys)
    import logging

    masked_key = f"{new_key[:4]}{'*' * 8}"
    logging.warning(f"🔑 Kore API key generated: {masked_key}")
    logging.warning(f"   Full key saved to: {_KEY_FILE}")
    logging.warning("   Read the key from the file above or set KORE_API_KEY env var.")

    return new_key


_API_KEY: str | None = None


def _loaded_key() -> str:
    global _API_KEY
    if _API_KEY is None:
        _API_KEY = get_or_create_api_key()
    return _API_KEY


def _is_local(request: Request) -> bool:
    client_host = request.client.host if request.client else ""
    trusted = {"127.0.0.1", "::1", "localhost"}
    # "testclient" only in explicit test environments
    if os.getenv("KORE_TEST_MODE", "0") == "1":
        trusted.add("testclient")
    if client_host not in trusted:
        return False
    # Detect reverse proxy: if X-Forwarded-For is present, the real client
    # is NOT local — require auth even if socket peer is localhost
    if request.headers.get("X-Forwarded-For") or request.headers.get("X-Real-IP"):
        return False
    return True


def _local_only_mode() -> bool:
    # Re-read at runtime to support override in tests (KORE_LOCAL_ONLY=1)
    # Default "1" = skip auth for localhost (consistent with config.LOCAL_ONLY)
    return os.getenv("KORE_LOCAL_ONLY", "1") == "1"


# ── FastAPI dependency ────────────────────────────────────────────────────────


async def require_auth(
    request: Request,
    x_kore_key: str | None = Header(default=None, alias="X-Kore-Key"),
) -> str:
    """
    FastAPI dependency: validates API key.
    In local-only mode, skips auth for 127.0.0.1 requests.
    Returns the validated API key (or 'local' for unauthenticated local requests).
    """
    if _local_only_mode() and _is_local(request):
        return "local"

    if not x_kore_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API key. Pass X-Kore-Key header.",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    if not secrets.compare_digest(x_kore_key, _loaded_key()):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API key.",
        )

    return x_kore_key


async def get_agent_id(
    request: Request,
    x_agent_id: str | None = Header(default=None, alias="X-Agent-Id"),
) -> str:
    """
    FastAPI dependency: extracts agent namespace.
    Defaults to 'default' when not provided.
    Agent IDs are sanitized to alphanumeric + dash/underscore only.
    """
    agent_id = (x_agent_id or "default").strip()
    # Sanitize: only allow safe chars
    safe = "".join(c for c in agent_id if c.isalnum() or c in "-_")
    if not safe:
        safe = "default"
    return safe[:64]  # max 64 chars
