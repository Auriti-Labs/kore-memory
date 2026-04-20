"""
Kore — Event System
Simple in-process event dispatch for memory lifecycle events.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("kore.events")

EventHandler = Callable[[str, dict[str, Any]], None]

_handlers: dict[str, list[EventHandler]] = defaultdict(list)

# Event types
MEMORY_SAVED = "memory.saved"
MEMORY_DELETED = "memory.deleted"
MEMORY_UPDATED = "memory.updated"
MEMORY_COMPRESSED = "memory.compressed"
MEMORY_DECAYED = "memory.decayed"
MEMORY_ARCHIVED = "memory.archived"
MEMORY_RESTORED = "memory.restored"
POLICY_APPLIED = "policy.applied"


def on(event: str, handler: EventHandler) -> None:
    """Register a handler for an event type. Evita duplicati."""
    if handler not in _handlers[event]:
        _handlers[event].append(handler)


def emit(event: str, data: dict[str, Any] | None = None) -> None:
    """Emit an event to all registered handlers."""
    payload = data or {}
    for handler in _handlers.get(event, []):
        try:
            handler(event, payload)
        except Exception:
            logger.exception("Event handler error for %s", event)


def clear() -> None:
    """Remove all handlers (for testing)."""
    _handlers.clear()
