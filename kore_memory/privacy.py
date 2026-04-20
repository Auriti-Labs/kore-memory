"""
Kore — Privacy filter.
Strips secrets, tokens, and credentials from content before persistence.
Configurable via KORE_PRIVACY_FILTER env var (default: 1 = enabled).
"""

from __future__ import annotations

import os
import re

_PATTERNS: list[tuple[re.Pattern, str]] = [
    # Bearer / API tokens (JWT, opaque tokens)
    (re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]{20,}"), "Bearer [REDACTED]"),
    # AWS access keys
    (re.compile(r"AKIA[0-9A-Z]{16}"), "[AWS_KEY_REDACTED]"),
    # PEM private keys
    (re.compile(r"-----BEGIN[A-Z ]*PRIVATE KEY-----[\s\S]*?-----END[A-Z ]*PRIVATE KEY-----"), "[PRIVATE_KEY_REDACTED]"),
    # Connection strings with password (://user:pass@host)
    (re.compile(r"(://[^:]+:)[^@\s]+(@)"), r"\1[REDACTED]\2"),
    # Secret assignments: password = "...", token = '...', api_key: "..."
    (re.compile(
        r'''((?:password|secret|token|api_key|apikey|api_secret)\s*[=:]\s*)["']([^"']{8,})["']''',
        re.I,
    ), r'\1"[REDACTED]"'),
]


def privacy_filter(content: str) -> str:
    """
    Strip secrets from content. Returns filtered content.
    Disabled when KORE_PRIVACY_FILTER=0.
    Graceful: returns original content on any error.
    """
    if os.getenv("KORE_PRIVACY_FILTER", "1") == "0":
        return content
    try:
        for pattern, replacement in _PATTERNS:
            content = pattern.sub(replacement, content)
    except Exception:
        pass  # graceful degradation
    return content
