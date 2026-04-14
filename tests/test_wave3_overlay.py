"""
Kore — Wave 3 test: Filesystem Overlay.
Issue #024.

Testa:
- scan_directory: trova i file corretti per pattern
- index_files: crea memories con tag overlay
- POST /overlay/index: endpoint HTTP
- GET /overlay/files: lista file indicizzati
- DELETE /overlay/files: rimozione file dall'overlay
- Chunking di file grandi
- Dedup / replace_existing
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kore_memory.filesystem_overlay import (
    _chunk_content,
    _file_path_hash,
    index_files,
    list_overlay_files,
    remove_file_from_overlay,
    scan_directory,
)
from kore_memory.main import app

HEADERS = {"X-Agent-Id": "overlay-test-agent"}

client = TestClient(app)


# ── Helpers ───────────────────────────────────────────────────────────────────


@pytest.fixture()
def tmp_project(tmp_path: Path) -> Path:
    """Crea una struttura di progetto temporanea con file tecnici."""
    (tmp_path / "CLAUDE.md").write_text("# CLAUDE\nIstruzioni per Claude.")
    (tmp_path / "README.md").write_text("# Progetto\nDescrizione breve.")
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "test-project"\nversion = "1.0.0"')
    (tmp_path / "requirements.txt").write_text("fastapi\npytest\n")
    # Subdirectory docs/
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "architecture.md").write_text("# Architettura\nDiagramma dell'architettura.")
    # File binario — deve essere ignorato
    (tmp_path / "binary.bin").write_bytes(b"\x00\x01\x02\x03" * 100)
    # Sotto-directory nascosta — deve essere ignorata
    hidden = tmp_path / ".hidden"
    hidden.mkdir()
    (hidden / "secret.md").write_text("segreto")
    return tmp_path


# ── Test scan_directory ────────────────────────────────────────────────────────


class TestScanDirectory:
    def test_trova_pattern_default(self, tmp_project: Path) -> None:
        """scan_directory trova CLAUDE.md, README.md, pyproject.toml, requirements.txt."""
        files = scan_directory(str(tmp_project))
        filenames = [Path(f).name for f in files]
        assert "CLAUDE.md" in filenames
        assert "README.md" in filenames
        assert "pyproject.toml" in filenames
        assert "requirements.txt" in filenames

    def test_include_md_in_docs(self, tmp_project: Path) -> None:
        """Con include_extra_md=True include docs/architecture.md."""
        files = scan_directory(str(tmp_project), include_extra_md=True)
        filenames = [Path(f).name for f in files]
        assert "architecture.md" in filenames

    def test_esclude_md_in_docs_quando_disabilitato(self, tmp_project: Path) -> None:
        """Con include_extra_md=False esclude docs/architecture.md."""
        files = scan_directory(str(tmp_project), include_extra_md=False)
        filenames = [Path(f).name for f in files]
        assert "architecture.md" not in filenames

    def test_esclude_directory_nascoste(self, tmp_project: Path) -> None:
        """scan_directory non entra in .hidden/."""
        files = scan_directory(str(tmp_project))
        paths = [str(f) for f in files]
        assert not any(".hidden" in p for p in paths)

    def test_path_inesistente_ritorna_lista_vuota(self) -> None:
        """Se base_path non esiste, ritorna lista vuota."""
        files = scan_directory("/tmp/__inesistente_kore_test__")
        assert files == []

    def test_patterns_custom(self, tmp_project: Path) -> None:
        """Con patterns custom e include_extra_md=False cerca solo i file specificati."""
        files = scan_directory(
            str(tmp_project),
            patterns=["requirements.txt"],
            include_extra_md=False,
        )
        filenames = [Path(f).name for f in files]
        assert "requirements.txt" in filenames
        assert "CLAUDE.md" not in filenames

    def test_no_duplicati(self, tmp_project: Path) -> None:
        """Nessun file appare due volte nella lista."""
        files = scan_directory(str(tmp_project))
        assert len(files) == len(set(files))


# ── Test _chunk_content ────────────────────────────────────────────────────────


class TestChunkContent:
    def test_file_piccolo_un_solo_chunk(self) -> None:
        content = "Riga breve\n" * 10
        chunks = _chunk_content(content, "test.md")
        assert len(chunks) == 1
        assert chunks[0][1] == 0  # chunk_idx = 0

    def test_file_grande_chunked(self) -> None:
        """File da 8000 chars → almeno 2 chunk."""
        content = ("A" * 100 + "\n") * 80  # 8080 chars
        chunks = _chunk_content(content, "big.md")
        assert len(chunks) >= 2

    def test_nessun_chunk_supera_max(self) -> None:
        """Ogni chunk ha dimensione <= MAX_CHUNK_CHARS."""
        from kore_memory.filesystem_overlay import MAX_CHUNK_CHARS

        content = ("B" * 50 + "\n") * 200
        chunks = _chunk_content(content, "large.md")
        for text, _ in chunks:
            assert len(text) <= MAX_CHUNK_CHARS

    def test_indici_sequenziali(self) -> None:
        """chunk_idx sono 0, 1, 2, ... senza buchi."""
        content = ("C" * 100 + "\n") * 100
        chunks = _chunk_content(content, "seq.md")
        indices = [idx for _, idx in chunks]
        assert indices == list(range(len(chunks)))

    def test_contenuto_preservato(self) -> None:
        """La concatenazione dei chunk ricostruisce il contenuto originale."""
        content = "linea uno\nlinea due\nlinea tre\n" * 200
        chunks = _chunk_content(content, "pres.md")
        reconstructed = "".join(text for text, _ in chunks)
        assert reconstructed == content


# ── Test index_files / list_overlay_files / remove_file_from_overlay ─────────


class TestIndexFiles:
    def test_indicizza_file_base(self, tmp_project: Path) -> None:
        """index_files crea almeno una memoria per CLAUDE.md."""
        agent = "overlay-unit-test"
        filepath = str(tmp_project / "CLAUDE.md")
        stats = index_files([filepath], agent_id=agent, replace_existing=True)
        assert stats["indexed"] + stats["updated"] >= 1
        assert stats["errors"] == 0

    def test_replace_existing_aggiorna(self, tmp_project: Path) -> None:
        """Seconda chiamata con replace_existing=True → status 'updated'."""
        agent = "overlay-replace-test"
        filepath = str(tmp_project / "README.md")
        index_files([filepath], agent_id=agent, replace_existing=True)
        stats2 = index_files([filepath], agent_id=agent, replace_existing=True)
        assert stats2["updated"] == 1
        assert stats2["indexed"] == 0

    def test_replace_false_salta_esistenti(self, tmp_project: Path) -> None:
        """Con replace_existing=False la seconda chiamata salta il file."""
        agent = "overlay-noreplace-test"
        filepath = str(tmp_project / "pyproject.toml")
        index_files([filepath], agent_id=agent, replace_existing=True)
        stats2 = index_files([filepath], agent_id=agent, replace_existing=False)
        assert stats2["skipped"] == 1

    def test_file_inesistente_skippato(self) -> None:
        """Un file inesistente viene skippato senza errori."""
        stats = index_files(["/tmp/__kore_inesistente_file_xyz.md"], agent_id="skip-test")
        assert stats["skipped"] == 1
        assert stats["errors"] == 0

    def test_file_vuoto_skippato(self, tmp_path: Path) -> None:
        """File vuoto viene skippato."""
        empty = tmp_path / "empty.md"
        empty.write_text("")
        stats = index_files([str(empty)], agent_id="empty-test")
        assert stats["skipped"] == 1

    def test_list_overlay_files_ritorna_file_indicizzati(self, tmp_project: Path) -> None:
        """list_overlay_files ritorna il file appena indicizzato."""
        agent = "overlay-list-test"
        filepath = str(tmp_project / "CLAUDE.md")
        index_files([filepath], agent_id=agent, replace_existing=True)
        files = list_overlay_files(agent_id=agent)
        paths = [f["path"] for f in files]
        assert filepath in paths

    def test_remove_file_from_overlay(self, tmp_project: Path) -> None:
        """remove_file_from_overlay elimina le memories del file."""
        agent = "overlay-remove-test"
        filepath = str(tmp_project / "README.md")
        index_files([filepath], agent_id=agent, replace_existing=True)
        removed = remove_file_from_overlay(filepath, agent_id=agent)
        assert removed >= 1
        # Dopo la rimozione non compare più nella lista
        files = list_overlay_files(agent_id=agent)
        paths = [f["path"] for f in files]
        assert filepath not in paths

    def test_isolamento_tra_agent(self, tmp_project: Path) -> None:
        """Le memories dell'overlay sono isolate per agent_id."""
        agent_a = "overlay-iso-a"
        agent_b = "overlay-iso-b"
        filepath = str(tmp_project / "CLAUDE.md")
        index_files([filepath], agent_id=agent_a, replace_existing=True)
        files_b = list_overlay_files(agent_id=agent_b)
        paths_b = [f["path"] for f in files_b]
        assert filepath not in paths_b


# ── Test API HTTP ─────────────────────────────────────────────────────────────


class TestOverlayAPI:
    def test_post_overlay_index(self, tmp_project: Path) -> None:
        """POST /overlay/index con un progetto valido ritorna 200 con stats."""
        r = client.post(
            "/overlay/index",
            json={"base_path": str(tmp_project)},
            headers=HEADERS,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert "indexed" in data
        assert "files_scanned" in data
        assert data["files_scanned"] >= 4  # CLAUDE.md, README.md, pyproject.toml, requirements.txt

    def test_get_overlay_files(self, tmp_project: Path) -> None:
        """GET /overlay/files ritorna la lista dei file indicizzati."""
        # Prima indicizza
        client.post("/overlay/index", json={"base_path": str(tmp_project)}, headers=HEADERS)
        r = client.get("/overlay/files", headers=HEADERS)
        assert r.status_code == 200, r.text
        data = r.json()
        assert "files" in data
        assert "total" in data
        assert data["total"] >= 1

    def test_delete_overlay_file(self, tmp_project: Path) -> None:
        """DELETE /overlay/files?path=... rimuove le memories del file."""
        filepath = str(tmp_project / "CLAUDE.md")
        # Indicizza
        client.post(
            "/overlay/index",
            json={"base_path": str(tmp_project), "patterns": ["CLAUDE.md"]},
            headers=HEADERS,
        )
        # Rimuovi
        r = client.delete(f"/overlay/files?path={filepath}", headers=HEADERS)
        assert r.status_code == 200, r.text
        data = r.json()
        assert "removed" in data
        assert data["removed"] >= 1

    def test_overlay_path_inesistente_ok(self) -> None:
        """POST /overlay/index con path inesistente → 200 con files_scanned=0."""
        r = client.post(
            "/overlay/index",
            json={"base_path": "/tmp/__kore_nonexistent_xyz__"},
            headers=HEADERS,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["files_scanned"] == 0
        assert data["indexed"] == 0

    def test_overlay_replace_existing_false(self, tmp_project: Path) -> None:
        """Con replace_existing=False la seconda chiamata non aggiorna."""
        # Prima indicizzazione
        client.post("/overlay/index", json={"base_path": str(tmp_project)}, headers=HEADERS)
        # Seconda senza replace
        r = client.post(
            "/overlay/index",
            json={"base_path": str(tmp_project), "replace_existing": False},
            headers=HEADERS,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["indexed"] == 0  # già tutti presenti
        assert data["updated"] == 0

    def test_overlay_custom_patterns(self, tmp_project: Path) -> None:
        """Con patterns custom e include_extra_md=False indicizza solo i file richiesti."""
        r = client.post(
            "/overlay/index",
            json={
                "base_path": str(tmp_project),
                "patterns": ["requirements.txt"],
                "include_extra_md": False,
                "replace_existing": True,
            },
            headers=HEADERS,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["files_scanned"] == 1

    def test_overlay_file_record_fields(self, tmp_project: Path) -> None:
        """Ogni record in GET /overlay/files ha i campi attesi."""
        client.post("/overlay/index", json={"base_path": str(tmp_project)}, headers=HEADERS)
        r = client.get("/overlay/files", headers=HEADERS)
        assert r.status_code == 200
        files = r.json()["files"]
        assert len(files) >= 1
        for f in files:
            assert "path" in f
            assert "filename" in f
            assert "exists" in f
            assert "memory_ids" in f
            assert "chunk_count" in f
            assert "category" in f


# ── Test _file_path_hash ──────────────────────────────────────────────────────


class TestFilePathHash:
    def test_stesso_path_stesso_hash(self) -> None:
        h1 = _file_path_hash("/some/path/CLAUDE.md")
        h2 = _file_path_hash("/some/path/CLAUDE.md")
        assert h1 == h2

    def test_path_diversi_hash_diversi(self) -> None:
        h1 = _file_path_hash("/path/a/README.md")
        h2 = _file_path_hash("/path/b/README.md")
        assert h1 != h2

    def test_lunghezza_hash(self) -> None:
        """Hash è 16 chars hex."""
        h = _file_path_hash("/any/path.md")
        assert len(h) == 16
        assert all(c in "0123456789abcdef" for c in h)
