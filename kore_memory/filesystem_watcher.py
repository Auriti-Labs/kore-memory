"""
Kore — Filesystem Watcher (Wave 3, issue #025)

Auto-aggiorna il filesystem overlay quando i file monitorati cambiano su disco.
Richiede: pip install kore-memory[watcher]  (watchdog>=4.0.0)

Funzionalità:
- Monitora i file dell'overlay in background via watchdog Observer
- CREATE/MODIFY → re-index con debounce da 1s (evita burst da auto-save IDE)
- DELETE → remove_file_from_overlay()
- Watcher per (base_path, agent_id) — multipli watchers supportati
- Graceful degradation: se watchdog non disponibile, errori chiari senza crash
"""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .filesystem_overlay import DEFAULT_PATTERNS, index_files, remove_file_from_overlay

logger = logging.getLogger(__name__)

# ── Disponibilità watchdog ────────────────────────────────────────────────────

try:
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer as _Observer

    _HAS_WATCHDOG = True
except ImportError:
    _HAS_WATCHDOG = False
    # Fallback per type annotations senza watchdog installato
    FileSystemEventHandler = object  # type: ignore[assignment,misc]

_DEBOUNCE_SECONDS = 1.0  # attesa dopo l'ultimo evento prima di re-indexare

# Estensioni di file monitorate dal watcher
_WATCHED_EXTENSIONS = frozenset({".md", ".rst", ".toml", ".txt", ".json", ".yaml", ".yml", ".py", ".cfg"})


# ── Struttura dati watcher ────────────────────────────────────────────────────


class _WatcherEntry:
    """Rappresenta un watcher attivo per una (base_path, agent_id)."""

    def __init__(
        self,
        base_path: str,
        agent_id: str,
        patterns: list[str] | None,
        include_extra_md: bool,
        max_depth: int,
    ) -> None:
        self.base_path = base_path
        self.agent_id = agent_id
        self.patterns = patterns
        self.include_extra_md = include_extra_md
        self.max_depth = max_depth
        self.observer: Any = None
        self.started_at: str = datetime.now(UTC).isoformat()
        self.events_processed: int = 0


class _WatcherRegistry:
    """Registry thread-safe dei watcher attivi."""

    def __init__(self) -> None:
        self._watchers: dict[str, _WatcherEntry] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _key(base_path: str, agent_id: str) -> str:
        return f"{agent_id}:{base_path}"

    def add(self, entry: _WatcherEntry) -> None:
        with self._lock:
            self._watchers[self._key(entry.base_path, entry.agent_id)] = entry

    def remove(self, base_path: str, agent_id: str) -> _WatcherEntry | None:
        with self._lock:
            return self._watchers.pop(self._key(base_path, agent_id), None)

    def get(self, base_path: str, agent_id: str) -> _WatcherEntry | None:
        with self._lock:
            return self._watchers.get(self._key(base_path, agent_id))

    def list_all(self) -> list[dict]:
        with self._lock:
            return [
                {
                    "base_path": e.base_path,
                    "agent_id": e.agent_id,
                    "started_at": e.started_at,
                    "events_processed": e.events_processed,
                    "active": e.observer is not None and e.observer.is_alive(),
                }
                for e in self._watchers.values()
            ]

    def stop_all(self) -> int:
        """Ferma tutti i watcher — chiamato al shutdown del server."""
        with self._lock:
            entries = list(self._watchers.values())
            self._watchers.clear()
        count = 0
        for entry in entries:
            if entry.observer:
                try:
                    entry.observer.stop()
                    entry.observer.join(timeout=2)
                    count += 1
                except Exception:
                    pass
        return count


_registry = _WatcherRegistry()


# ── Event handler ─────────────────────────────────────────────────────────────


class _KoreFileHandler(FileSystemEventHandler):  # type: ignore[misc]
    """Gestisce gli eventi del filesystem e aggiorna l'overlay con debounce."""

    def __init__(self, entry: _WatcherEntry) -> None:
        if _HAS_WATCHDOG:
            super().__init__()
        self._entry = entry
        self._timers: dict[str, threading.Timer] = {}
        self._timers_lock = threading.Lock()

    # ── Debounce ──────────────────────────────────────────────────────────────

    def _schedule_reindex(self, filepath: str) -> None:
        """Annulla il timer pendente e ne avvia uno nuovo (debounce 1s)."""
        with self._timers_lock:
            existing = self._timers.get(filepath)
            if existing:
                existing.cancel()
            timer = threading.Timer(_DEBOUNCE_SECONDS, self._do_reindex, args=[filepath])
            self._timers[filepath] = timer
            timer.start()

    def _do_reindex(self, filepath: str) -> None:
        """Re-indicizza il file nel filesystem overlay."""
        with self._timers_lock:
            self._timers.pop(filepath, None)

        try:
            path = Path(filepath)
            if not path.exists() or not path.is_file():
                return
            stats = index_files(
                filepaths=[path],
                agent_id=self._entry.agent_id,
                replace_existing=True,
            )
            self._entry.events_processed += 1
            logger.info(
                "Watcher re-indexed %s (agent=%s, updated=%d, indexed=%d)",
                filepath,
                self._entry.agent_id,
                stats.get("updated", 0),
                stats.get("indexed", 0),
            )
        except Exception as exc:
            logger.error("Watcher: errore re-index %s: %s", filepath, exc)

    def _do_remove(self, filepath: str) -> None:
        """Rimuove le memories del file eliminato dall'overlay."""
        try:
            removed = remove_file_from_overlay(filepath, agent_id=self._entry.agent_id)
            if removed:
                self._entry.events_processed += 1
                logger.info(
                    "Watcher: rimosso overlay per file eliminato %s (agent=%s, memories=%d)",
                    filepath,
                    self._entry.agent_id,
                    removed,
                )
        except Exception as exc:
            logger.error("Watcher: errore rimozione overlay %s: %s", filepath, exc)

    # ── Filtro file ───────────────────────────────────────────────────────────

    def _is_relevant(self, filepath: str) -> bool:
        """
        True se il file è dentro base_path e ha un'estensione o nome rilevante.
        """
        path = Path(filepath)
        try:
            path.relative_to(self._entry.base_path)
        except ValueError:
            return False

        if path.suffix.lower() in _WATCHED_EXTENSIONS:
            return True

        watched_names = set(self._entry.patterns or DEFAULT_PATTERNS)
        return path.name in watched_names

    # ── Handlers watchdog ─────────────────────────────────────────────────────

    def on_modified(self, event: Any) -> None:
        if not event.is_directory and self._is_relevant(event.src_path):
            self._schedule_reindex(event.src_path)

    def on_created(self, event: Any) -> None:
        if not event.is_directory and self._is_relevant(event.src_path):
            self._schedule_reindex(event.src_path)

    def on_deleted(self, event: Any) -> None:
        if not event.is_directory:
            self._do_remove(event.src_path)

    def on_moved(self, event: Any) -> None:
        """Rinomina = elimina vecchio + indicizza nuovo."""
        if not event.is_directory:
            self._do_remove(event.src_path)
            if self._is_relevant(event.dest_path):
                self._schedule_reindex(event.dest_path)


# ── API pubblica ──────────────────────────────────────────────────────────────


def start_watcher(
    base_path: str,
    agent_id: str = "default",
    patterns: list[str] | None = None,
    include_extra_md: bool = True,
    max_depth: int = 2,
) -> dict:
    """
    Avvia un watcher per base_path. Richiede watchdog installato.

    Raises:
        ImportError: se watchdog non è installato
        ValueError: se base_path non è una directory esistente
    """
    if not _HAS_WATCHDOG:
        raise ImportError("watchdog non installato. Eseguire: pip install 'kore-memory[watcher]'")

    resolved = str(Path(base_path).resolve())
    if not Path(resolved).is_dir():
        raise ValueError(f"base_path deve essere una directory esistente: {resolved!r}")

    # Watcher già attivo per questa coppia (path, agent)?
    existing = _registry.get(resolved, agent_id)
    if existing and existing.observer and existing.observer.is_alive():
        return {
            "watching": True,
            "already_running": True,
            "base_path": resolved,
            "agent_id": agent_id,
            "started_at": existing.started_at,
            "message": "Watcher già attivo",
        }

    entry = _WatcherEntry(
        base_path=resolved,
        agent_id=agent_id,
        patterns=patterns,
        include_extra_md=include_extra_md,
        max_depth=max_depth,
    )

    handler = _KoreFileHandler(entry)
    observer = _Observer()
    observer.schedule(handler, resolved, recursive=True)
    observer.start()

    entry.observer = observer
    _registry.add(entry)

    logger.info("Watcher avviato: base_path=%s agent=%s", resolved, agent_id)
    return {
        "watching": True,
        "already_running": False,
        "base_path": resolved,
        "agent_id": agent_id,
        "started_at": entry.started_at,
        "message": "Watcher avviato",
    }


def stop_watcher(base_path: str, agent_id: str = "default") -> dict:
    """Ferma il watcher per (base_path, agent_id)."""
    resolved = str(Path(base_path).resolve())
    entry = _registry.remove(resolved, agent_id)

    if not entry:
        return {
            "stopped": False,
            "base_path": resolved,
            "agent_id": agent_id,
            "message": "Nessun watcher attivo trovato",
        }

    if entry.observer:
        try:
            entry.observer.stop()
            entry.observer.join(timeout=3)
        except Exception as exc:
            logger.warning("Errore stop observer %s: %s", resolved, exc)

    logger.info(
        "Watcher fermato: base_path=%s agent=%s events=%d",
        resolved,
        agent_id,
        entry.events_processed,
    )
    return {
        "stopped": True,
        "base_path": resolved,
        "agent_id": agent_id,
        "events_processed": entry.events_processed,
        "message": "Watcher fermato",
    }


def stop_all_watchers() -> int:
    """Ferma tutti i watcher attivi. Chiamato al shutdown del server."""
    count = _registry.stop_all()
    if count:
        logger.info("Watcher: fermati %d observer al shutdown", count)
    return count


def list_watchers() -> list[dict]:
    """Restituisce la lista dei watcher attivi con statistiche."""
    return _registry.list_all()


def is_available() -> bool:
    """True se watchdog è installato e il watcher è disponibile."""
    return _HAS_WATCHDOG
