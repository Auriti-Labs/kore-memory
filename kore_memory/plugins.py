"""
Kore — Plugin System
Hook points for extending memory operations.
Supports pre/post hooks for save, search, delete, and compress.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger("kore.plugins")


class KorePlugin(ABC):
    """Base class for Kore plugins. Override the hooks you need."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique plugin name."""
        ...

    def pre_save(self, content: str, category: str, importance: int | None, agent_id: str) -> dict[str, Any] | None:
        """Called before saving. Return dict to override fields, or None to keep original."""
        return None

    def post_save(self, memory_id: int, content: str, category: str, importance: int, agent_id: str) -> None:  # noqa: B027
        """Called after saving a memory."""

    def pre_search(self, query: str, agent_id: str, semantic: bool) -> dict[str, Any] | None:
        """Called before search. Return dict to override query params, or None."""
        return None

    def post_search(self, query: str, results: list[dict], agent_id: str) -> list[dict]:
        """Called after search. Can filter/reorder results. Must return results list."""
        return results

    def pre_delete(self, memory_id: int, agent_id: str) -> bool:
        """Called before delete. Return False to block deletion."""
        return True

    def post_delete(self, memory_id: int, agent_id: str) -> None:  # noqa: B027
        """Called after deletion."""

    def pre_compress(self, agent_id: str) -> bool:
        """Called before compression. Return False to block."""
        return True

    def post_compress(self, clusters_found: int, merged: int, agent_id: str) -> None:  # noqa: B027
        """Called after compression."""


# Plugin registry
_plugins: dict[str, KorePlugin] = {}


def register_plugin(plugin: KorePlugin) -> None:
    """Register a plugin. Replaces existing plugin with same name."""
    _plugins[plugin.name] = plugin
    logger.info("Plugin registered: %s", plugin.name)


def unregister_plugin(name: str) -> bool:
    """Unregister a plugin by name. Returns True if found."""
    return _plugins.pop(name, None) is not None


def list_plugins() -> list[str]:
    """Return registered plugin names."""
    return list(_plugins.keys())


def clear_plugins() -> None:
    """Remove all plugins (for testing)."""
    _plugins.clear()


# Hook dispatch functions


def run_pre_save(content: str, category: str, importance: int | None, agent_id: str) -> dict[str, Any]:
    """Run all pre_save hooks. Returns merged overrides."""
    overrides: dict[str, Any] = {}
    for plugin in _plugins.values():
        try:
            result = plugin.pre_save(content, category, importance, agent_id)
            if result:
                overrides.update(result)
        except Exception:
            logger.exception("Plugin %s pre_save error", plugin.name)
    return overrides


def run_post_save(memory_id: int, content: str, category: str, importance: int, agent_id: str) -> None:
    """Run all post_save hooks."""
    for plugin in _plugins.values():
        try:
            plugin.post_save(memory_id, content, category, importance, agent_id)
        except Exception:
            logger.exception("Plugin %s post_save error", plugin.name)


def run_pre_search(query: str, agent_id: str, semantic: bool) -> dict[str, Any]:
    """Run all pre_search hooks. Returns merged overrides."""
    overrides: dict[str, Any] = {}
    for plugin in _plugins.values():
        try:
            result = plugin.pre_search(query, agent_id, semantic)
            if result:
                overrides.update(result)
        except Exception:
            logger.exception("Plugin %s pre_search error", plugin.name)
    return overrides


def run_post_search(query: str, results: list[dict], agent_id: str) -> list[dict]:
    """Run all post_search hooks. Each plugin can filter/reorder results."""
    for plugin in _plugins.values():
        try:
            results = plugin.post_search(query, results, agent_id)
        except Exception:
            logger.exception("Plugin %s post_search error", plugin.name)
    return results


def run_pre_delete(memory_id: int, agent_id: str) -> bool:
    """Run all pre_delete hooks. Returns False if any plugin blocks deletion."""
    for plugin in _plugins.values():
        try:
            if not plugin.pre_delete(memory_id, agent_id):
                return False
        except Exception:
            logger.exception("Plugin %s pre_delete error", plugin.name)
    return True


def run_post_delete(memory_id: int, agent_id: str) -> None:
    """Run all post_delete hooks."""
    for plugin in _plugins.values():
        try:
            plugin.post_delete(memory_id, agent_id)
        except Exception:
            logger.exception("Plugin %s post_delete error", plugin.name)
