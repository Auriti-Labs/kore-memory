"""
Tests per Filesystem Watcher (Wave 3, issue #025).
Testa la logica del watcher, il debounce, e gli endpoint REST.
"""

from __future__ import annotations

import os
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def clean_watchers():
    """Pulisce tutti i watcher attivi prima e dopo ogni test."""
    from kore_memory.filesystem_watcher import _registry

    yield
    _registry.stop_all()


@pytest.fixture
def tmp_dir():
    """Directory temporanea per i test del watcher."""
    with tempfile.TemporaryDirectory() as d:
        yield d


# ── Test: disponibilità watchdog ─────────────────────────────────────────────


class TestWatcherAvailability:
    def test_is_available_returns_bool(self):
        from kore_memory.filesystem_watcher import is_available

        result = is_available()
        assert isinstance(result, bool)

    def test_has_watchdog_flag_exists(self):
        from kore_memory import filesystem_watcher

        assert hasattr(filesystem_watcher, "_HAS_WATCHDOG")

    def test_start_without_watchdog_raises(self, tmp_dir):
        """Se watchdog non è installato, start_watcher solleva ImportError."""
        from kore_memory import filesystem_watcher

        if filesystem_watcher._HAS_WATCHDOG:
            pytest.skip("watchdog installato — test non applicabile")

        with pytest.raises(ImportError, match="watchdog"):
            filesystem_watcher.start_watcher(base_path=tmp_dir)


# ── Test: WatcherRegistry ─────────────────────────────────────────────────────


class TestWatcherRegistry:
    def test_key_format(self):
        from kore_memory.filesystem_watcher import _WatcherRegistry

        assert _WatcherRegistry._key("/tmp/foo", "default") == "default:/tmp/foo"

    def test_add_and_get(self):
        from kore_memory.filesystem_watcher import _WatcherEntry, _WatcherRegistry

        reg = _WatcherRegistry()
        entry = _WatcherEntry("/tmp/test", "agent1", None, True, 2)
        reg.add(entry)

        retrieved = reg.get("/tmp/test", "agent1")
        assert retrieved is entry

    def test_get_missing_returns_none(self):
        from kore_memory.filesystem_watcher import _WatcherRegistry

        reg = _WatcherRegistry()
        assert reg.get("/nonexistent", "default") is None

    def test_remove_existing(self):
        from kore_memory.filesystem_watcher import _WatcherEntry, _WatcherRegistry

        reg = _WatcherRegistry()
        entry = _WatcherEntry("/tmp/x", "def", None, True, 2)
        reg.add(entry)

        removed = reg.remove("/tmp/x", "def")
        assert removed is entry
        assert reg.get("/tmp/x", "def") is None

    def test_remove_missing_returns_none(self):
        from kore_memory.filesystem_watcher import _WatcherRegistry

        reg = _WatcherRegistry()
        assert reg.remove("/nonexistent", "default") is None

    def test_list_all_empty(self):
        from kore_memory.filesystem_watcher import _WatcherRegistry

        reg = _WatcherRegistry()
        assert reg.list_all() == []

    def test_list_all_multiple_entries(self):
        from kore_memory.filesystem_watcher import _WatcherEntry, _WatcherRegistry

        reg = _WatcherRegistry()
        for i in range(3):
            entry = _WatcherEntry(f"/tmp/path{i}", f"agent{i}", None, True, 2)
            entry.observer = MagicMock()
            entry.observer.is_alive.return_value = True
            reg.add(entry)

        result = reg.list_all()
        assert len(result) == 3
        paths = {r["base_path"] for r in result}
        assert paths == {"/tmp/path0", "/tmp/path1", "/tmp/path2"}

    def test_stop_all_calls_observer_stop(self):
        from kore_memory.filesystem_watcher import _WatcherEntry, _WatcherRegistry

        reg = _WatcherRegistry()
        mock_obs = MagicMock()
        entry = _WatcherEntry("/tmp/z", "ag", None, True, 2)
        entry.observer = mock_obs
        reg.add(entry)

        count = reg.stop_all()
        assert count == 1
        mock_obs.stop.assert_called_once()
        mock_obs.join.assert_called_once()
        assert reg.list_all() == []


# ── Test: KoreFileHandler ─────────────────────────────────────────────────────


class TestKoreFileHandler:
    def _make_handler(self, base_path: str = "/tmp/base", agent_id: str = "default"):
        from kore_memory.filesystem_watcher import _KoreFileHandler, _WatcherEntry

        entry = _WatcherEntry(base_path, agent_id, None, True, 2)
        return _KoreFileHandler(entry)

    def test_is_relevant_inside_base_with_md_extension(self, tmp_dir):
        handler = self._make_handler(base_path=tmp_dir)
        test_file = str(Path(tmp_dir) / "README.md")
        assert handler._is_relevant(test_file) is True

    def test_is_relevant_outside_base_path(self, tmp_dir):
        handler = self._make_handler(base_path=tmp_dir)
        assert handler._is_relevant("/other/path/file.md") is False

    def test_is_relevant_toml_extension(self, tmp_dir):
        handler = self._make_handler(base_path=tmp_dir)
        assert handler._is_relevant(str(Path(tmp_dir) / "pyproject.toml")) is True

    def test_is_relevant_unknown_extension(self, tmp_dir):
        handler = self._make_handler(base_path=tmp_dir)
        # .xyz non è nelle estensioni monitorate né nei DEFAULT_PATTERNS
        assert handler._is_relevant(str(Path(tmp_dir) / "file.xyz")) is False

    def test_debounce_cancels_previous_timer(self, tmp_dir):
        """Due eventi rapidi sullo stesso file → un solo timer attivo."""
        handler = self._make_handler(base_path=tmp_dir)
        filepath = str(Path(tmp_dir) / "test.md")

        with patch.object(handler, "_do_reindex") as mock_reindex:
            # Schedula due volte con intervallo < debounce
            handler._schedule_reindex(filepath)
            handler._schedule_reindex(filepath)

            # Deve esserci solo 1 timer pendente
            with handler._timers_lock:
                assert len(handler._timers) == 1

            # Attende la scadenza del timer
            time.sleep(1.2)
            mock_reindex.assert_called_once_with(filepath)

    def test_on_modified_calls_schedule_for_relevant_file(self, tmp_dir):
        handler = self._make_handler(base_path=tmp_dir)
        evt = MagicMock()
        evt.is_directory = False
        evt.src_path = str(Path(tmp_dir) / "CLAUDE.md")

        with patch.object(handler, "_schedule_reindex") as mock_sched:
            handler.on_modified(evt)
            mock_sched.assert_called_once_with(evt.src_path)

    def test_on_modified_ignores_directory(self, tmp_dir):
        handler = self._make_handler(base_path=tmp_dir)
        evt = MagicMock()
        evt.is_directory = True
        evt.src_path = tmp_dir

        with patch.object(handler, "_schedule_reindex") as mock_sched:
            handler.on_modified(evt)
            mock_sched.assert_not_called()

    def test_on_created_calls_schedule(self, tmp_dir):
        handler = self._make_handler(base_path=tmp_dir)
        evt = MagicMock()
        evt.is_directory = False
        evt.src_path = str(Path(tmp_dir) / "new_file.md")

        with patch.object(handler, "_schedule_reindex") as mock_sched:
            handler.on_created(evt)
            mock_sched.assert_called_once()

    def test_on_deleted_calls_do_remove(self, tmp_dir):
        handler = self._make_handler(base_path=tmp_dir)
        evt = MagicMock()
        evt.is_directory = False
        evt.src_path = str(Path(tmp_dir) / "old.md")

        with patch.object(handler, "_do_remove") as mock_remove:
            handler.on_deleted(evt)
            mock_remove.assert_called_once_with(evt.src_path)

    def test_on_moved_removes_old_and_reindexes_new(self, tmp_dir):
        handler = self._make_handler(base_path=tmp_dir)
        evt = MagicMock()
        evt.is_directory = False
        evt.src_path = str(Path(tmp_dir) / "old.md")
        evt.dest_path = str(Path(tmp_dir) / "new.md")

        with (
            patch.object(handler, "_do_remove") as mock_remove,
            patch.object(handler, "_schedule_reindex") as mock_sched,
        ):
            handler.on_moved(evt)
            mock_remove.assert_called_once_with(evt.src_path)
            mock_sched.assert_called_once_with(evt.dest_path)

    def test_do_reindex_skips_nonexistent_file(self, tmp_dir):
        handler = self._make_handler(base_path=tmp_dir)
        nonexistent = str(Path(tmp_dir) / "ghost.md")

        with patch("kore_memory.filesystem_overlay.index_files") as mock_idx:
            handler._do_reindex(nonexistent)
            mock_idx.assert_not_called()

    def test_do_reindex_calls_index_files_for_existing(self, tmp_dir):
        handler = self._make_handler(base_path=tmp_dir)
        test_file = Path(tmp_dir) / "test.md"
        test_file.write_text("# Test\ncontent here")

        with patch("kore_memory.filesystem_watcher.index_files") as mock_idx:
            mock_idx.return_value = {"indexed": 1, "updated": 0, "skipped": 0, "errors": 0}
            handler._do_reindex(str(test_file))
            mock_idx.assert_called_once()
            assert handler._entry.events_processed == 1

    def test_do_remove_calls_remove_file_from_overlay(self, tmp_dir):
        handler = self._make_handler(base_path=tmp_dir)

        with patch("kore_memory.filesystem_watcher.remove_file_from_overlay") as mock_rm:
            mock_rm.return_value = 2
            handler._do_remove(str(Path(tmp_dir) / "deleted.md"))
            mock_rm.assert_called_once()
            assert handler._entry.events_processed == 1


# ── Test: start_watcher / stop_watcher ───────────────────────────────────────


class TestStartStopWatcher:
    def test_start_with_invalid_path_raises(self):
        from kore_memory.filesystem_watcher import start_watcher

        if not __import__("kore_memory.filesystem_watcher", fromlist=["_HAS_WATCHDOG"])._HAS_WATCHDOG:
            pytest.skip("watchdog non installato")

        with pytest.raises((ValueError, Exception)):
            start_watcher(base_path="/nonexistent/path/xyz123")

    def test_stop_nonexistent_watcher(self):
        from kore_memory.filesystem_watcher import stop_watcher

        result = stop_watcher(base_path="/tmp/nonexistent", agent_id="default")
        assert result["stopped"] is False
        assert "Nessun watcher" in result["message"]

    def test_start_returns_correct_fields(self, tmp_dir):
        from kore_memory import filesystem_watcher

        if not filesystem_watcher._HAS_WATCHDOG:
            pytest.skip("watchdog non installato")

        result = filesystem_watcher.start_watcher(base_path=tmp_dir, agent_id="test-agent")
        try:
            assert result["watching"] is True
            assert result["agent_id"] == "test-agent"
            assert "started_at" in result
            assert result["already_running"] is False
        finally:
            filesystem_watcher.stop_watcher(base_path=tmp_dir, agent_id="test-agent")

    def test_start_same_watcher_twice_returns_already_running(self, tmp_dir):
        from kore_memory import filesystem_watcher

        if not filesystem_watcher._HAS_WATCHDOG:
            pytest.skip("watchdog non installato")

        filesystem_watcher.start_watcher(base_path=tmp_dir, agent_id="dup-agent")
        try:
            result = filesystem_watcher.start_watcher(base_path=tmp_dir, agent_id="dup-agent")
            assert result["already_running"] is True
        finally:
            filesystem_watcher.stop_watcher(base_path=tmp_dir, agent_id="dup-agent")

    def test_stop_existing_watcher(self, tmp_dir):
        from kore_memory import filesystem_watcher

        if not filesystem_watcher._HAS_WATCHDOG:
            pytest.skip("watchdog non installato")

        filesystem_watcher.start_watcher(base_path=tmp_dir, agent_id="stop-test")
        result = filesystem_watcher.stop_watcher(base_path=tmp_dir, agent_id="stop-test")
        assert result["stopped"] is True
        assert "events_processed" in result

    def test_list_watchers_empty_initially(self):
        from kore_memory.filesystem_watcher import list_watchers

        # clean_watchers fixture ha già pulito
        assert list_watchers() == []

    def test_stop_all_returns_count(self, tmp_dir):
        from kore_memory import filesystem_watcher

        if not filesystem_watcher._HAS_WATCHDOG:
            pytest.skip("watchdog non installato")

        filesystem_watcher.start_watcher(base_path=tmp_dir, agent_id="all-test")
        count = filesystem_watcher.stop_all_watchers()
        assert count >= 1


# ── Test: endpoint REST ───────────────────────────────────────────────────────


class TestWatcherEndpoints:
    @pytest.fixture
    def client(self):
        from kore_memory.main import app

        return TestClient(app)

    def test_get_watchers_empty(self, client):
        resp = client.get("/overlay/watchers")
        assert resp.status_code == 200
        data = resp.json()
        assert "watchers" in data
        assert "watcher_available" in data
        assert data["total"] == 0

    def test_get_watchers_includes_availability(self, client):
        from kore_memory import filesystem_watcher

        resp = client.get("/overlay/watchers")
        assert resp.json()["watcher_available"] == filesystem_watcher._HAS_WATCHDOG

    def test_post_watch_without_watchdog_returns_503(self, client, tmp_dir):
        from kore_memory import filesystem_watcher

        if filesystem_watcher._HAS_WATCHDOG:
            pytest.skip("watchdog installato — 503 non atteso")

        resp = client.post("/overlay/watch", json={"base_path": tmp_dir})
        assert resp.status_code == 503
        assert "watchdog" in resp.json()["detail"].lower()

    def test_post_watch_invalid_path_returns_400(self, client):
        from kore_memory import filesystem_watcher

        if not filesystem_watcher._HAS_WATCHDOG:
            pytest.skip("watchdog non installato")

        resp = client.post("/overlay/watch", json={"base_path": "/nonexistent/xyz999"})
        assert resp.status_code == 400

    def test_delete_watch_nonexistent_returns_stopped_false(self, client, tmp_dir):
        resp = client.delete(f"/overlay/watch?path={tmp_dir}")
        assert resp.status_code == 200
        assert resp.json()["stopped"] is False

    def test_full_watch_lifecycle(self, client, tmp_dir):
        """POST /overlay/watch → GET /overlay/watchers → DELETE /overlay/watch"""
        from kore_memory import filesystem_watcher

        if not filesystem_watcher._HAS_WATCHDOG:
            pytest.skip("watchdog non installato")

        # Avvia
        resp = client.post("/overlay/watch", json={"base_path": tmp_dir})
        assert resp.status_code == 200
        data = resp.json()
        assert data["watching"] is True

        # Lista
        resp = client.get("/overlay/watchers")
        assert resp.json()["total"] >= 1

        # Ferma
        resp = client.delete(f"/overlay/watch?path={tmp_dir}")
        assert resp.json()["stopped"] is True
