"""
Kore — Filesystem Overlay (Wave 3, issue #024)

Indicizzazione automatica di file tecnici del progetto nel sistema di memoria.
Supporta CLAUDE.md, README.md, pyproject.toml e altri file di configurazione.

Strategia:
- File <= MAX_CHUNK_CHARS: una singola memoria
- File > MAX_CHUNK_CHARS: suddiviso in chunk da MAX_CHUNK_CHARS (split per newline)
- Dedup via tag `__file__:<path_hash>`: re-index aggiorna o crea memories
- Tag `__overlay__` marca tutte le memories indicizzate da file
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

# Dimensione massima per chunk di memoria (sotto il limite di 4000 chars del modello)
MAX_CHUNK_CHARS = 3500

# Pattern di file da indicizzare (relativi alla root del progetto)
DEFAULT_PATTERNS: list[str] = [
    "CLAUDE.md",
    "README.md",
    "README.rst",
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "package.json",
    "go.mod",
    "Cargo.toml",
    "pom.xml",
    "requirements.txt",
    "Makefile",
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
]

# Estensioni aggiuntive (file .md nella root e in docs/)
EXTRA_EXTENSIONS: list[str] = [".md", ".rst"]
EXTRA_DIRS: list[str] = ["docs", "doc", "."]

# Category inference per tipo di file
_CATEGORY_MAP: dict[str, str] = {
    "CLAUDE.md": "project",
    "README.md": "project",
    "README.rst": "project",
    "pyproject.toml": "project",
    "setup.py": "project",
    "setup.cfg": "project",
    "package.json": "project",
    "go.mod": "project",
    "Cargo.toml": "project",
    "pom.xml": "project",
    "requirements.txt": "project",
    "Makefile": "runbook",
    "Dockerfile": "runbook",
    "docker-compose.yml": "runbook",
    "docker-compose.yaml": "runbook",
}


def _file_path_hash(filepath: str) -> str:
    """Hash SHA256 breve del path assoluto per uso come tag unico."""
    return hashlib.sha256(filepath.encode()).hexdigest()[:16]


def _infer_category(filepath: str) -> str:
    """Inferisce la category dalla filename."""
    name = Path(filepath).name
    return _CATEGORY_MAP.get(name, "project")


def _infer_importance(filepath: str) -> int:
    """Inferisce l'importanza del file: CLAUDE.md e README.md sono più importanti."""
    name = Path(filepath).name
    if name in ("CLAUDE.md", "README.md"):
        return 5
    if name in ("pyproject.toml", "package.json", "go.mod", "Cargo.toml"):
        return 4
    return 3


def _chunk_content(content: str, source_ref: str) -> list[tuple[str, int]]:
    """
    Suddivide il contenuto in chunk da MAX_CHUNK_CHARS.
    Ritorna lista di (chunk_content, chunk_index) a partire da 0.
    Split per righe per preservare la coerenza del testo.
    """
    if len(content) <= MAX_CHUNK_CHARS:
        return [(content, 0)]

    chunks: list[tuple[str, int]] = []
    current_lines: list[str] = []
    current_len = 0
    chunk_idx = 0

    for line in content.splitlines(keepends=True):
        if current_len + len(line) > MAX_CHUNK_CHARS and current_lines:
            chunks.append(("".join(current_lines), chunk_idx))
            chunk_idx += 1
            current_lines = [line]
            current_len = len(line)
        else:
            current_lines.append(line)
            current_len += len(line)

    if current_lines:
        chunks.append(("".join(current_lines), chunk_idx))

    return chunks


def _build_tag_overlay() -> str:
    return "__overlay__"


def _build_tag_file(filepath: str) -> str:
    return f"__file__{_file_path_hash(filepath)}"


def _build_tag_chunk(filepath: str, chunk_idx: int) -> str:
    return f"__chunk__{_file_path_hash(filepath)}_{chunk_idx}"


def _read_file_safe(filepath: str, max_bytes: int = 128_000) -> str | None:
    """
    Legge un file in modo sicuro:
    - Ignora file binari
    - Tronca a max_bytes per proteggere da file enormi
    - Ritorna None se non leggibile
    """
    try:
        path = Path(filepath)
        if not path.is_file():
            return None
        # Legge max_bytes byte per proteggere da file enormi
        raw = path.read_bytes()[:max_bytes]
        # Tenta decodifica UTF-8, poi latin-1 come fallback
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            try:
                return raw.decode("latin-1")
            except UnicodeDecodeError:
                return None  # file binario
    except (OSError, PermissionError):
        return None


# Directories that are never allowed as overlay base paths
_SENSITIVE_DIRS = frozenset({"/etc", "/var", "/usr", "/bin", "/sbin", "/boot", "/proc", "/sys", "/dev", "/root"})


def _validate_base_path(resolved: Path) -> None:
    """Reject base paths that point to sensitive system directories.

    If KORE_OVERLAY_ALLOWED_DIRS is set (comma-separated list of absolute
    paths), the resolved path must be under one of them.  Otherwise, a
    blocklist of known sensitive directories is enforced.

    Raises:
        ValueError: If the path is not allowed.
    """
    allowed_raw = os.getenv("KORE_OVERLAY_ALLOWED_DIRS", "")
    resolved_str = str(resolved)

    if allowed_raw.strip():
        allowed = [str(Path(d.strip()).resolve()) for d in allowed_raw.split(",") if d.strip()]
        if not any(resolved_str == a or resolved_str.startswith(a + os.sep) for a in allowed):
            raise ValueError(
                f"Path '{resolved}' is outside allowed overlay directories. "
                f"Allowed: {', '.join(allowed)}"
            )
        return

    # Default blocklist: reject known sensitive system directories
    for sensitive in _SENSITIVE_DIRS:
        if resolved_str == sensitive or resolved_str.startswith(sensitive + os.sep):
            raise ValueError(f"Path '{resolved}' is inside a sensitive system directory ({sensitive})")


def scan_directory(
    base_path: str,
    patterns: list[str] | None = None,
    include_extra_md: bool = True,
    max_depth: int = 2,
) -> list[str]:
    """
    Scansiona una directory e restituisce i path dei file da indicizzare.

    Args:
        base_path: Directory radice da scansionare
        patterns: Lista di filename/glob pattern (default: DEFAULT_PATTERNS)
        include_extra_md: Se True, include anche file .md nelle subdirectory
        max_depth: Profondità massima di ricerca (default: 2)

    Raises:
        ValueError: If base_path is outside allowed directories.
    """
    base = Path(base_path).resolve()
    if not base.is_dir():
        return []

    _validate_base_path(base)

    effective_patterns = set(patterns or DEFAULT_PATTERNS)
    found: list[str] = []
    seen: set[str] = set()

    def _should_skip(p: Path) -> bool:
        """Salta directory nascoste, node_modules, .venv, ecc."""
        name = p.name
        return name.startswith(".") or name in (
            "node_modules",
            ".venv",
            "venv",
            "__pycache__",
            "dist",
            "build",
            ".git",
        )

    def _scan_recursive(current: Path, depth: int) -> None:
        if depth > max_depth:
            return
        try:
            for entry in sorted(current.iterdir()):
                if entry.is_dir() and not _should_skip(entry):
                    _scan_recursive(entry, depth + 1)
                elif entry.is_file():
                    abs_path = str(entry)
                    if abs_path in seen:
                        continue
                    # Match per nome esatto
                    if entry.name in effective_patterns:
                        found.append(abs_path)
                        seen.add(abs_path)
                    # Match per estensione extra (solo in directory esplicite)
                    elif include_extra_md and entry.suffix in EXTRA_EXTENSIONS:
                        # Solo nella root o in dir doc/docs
                        rel = entry.parent.relative_to(base) if entry.parent != base else Path(".")
                        if str(rel) == "." or entry.parent.name in EXTRA_DIRS:
                            found.append(abs_path)
                            seen.add(abs_path)
        except PermissionError:
            pass

    _scan_recursive(base, 0)
    return found


def index_files(
    filepaths: list[str],
    agent_id: str = "default",
    replace_existing: bool = True,
) -> dict:
    """
    Indicizza una lista di file come memories.

    Args:
        filepaths: Lista di path assoluti da indicizzare
        agent_id: Agent ID per namespace isolation
        replace_existing: Se True, sostituisce memories esistenti per lo stesso file

    Returns:
        dict con: indexed, updated, skipped, errors, file_results
    """
    from .repository import add_tags, delete_memory, save_memory
    from .repository.search import search_by_tag

    stats = {"indexed": 0, "updated": 0, "skipped": 0, "errors": 0, "file_results": []}

    for filepath in filepaths:
        filepath = str(Path(filepath).resolve())
        file_result = {"path": filepath, "status": "skipped", "memory_ids": [], "chunks": 0}

        content = _read_file_safe(filepath)
        if content is None or not content.strip():
            file_result["status"] = "skipped"
            stats["skipped"] += 1
            stats["file_results"].append(file_result)
            continue

        file_tag = _build_tag_file(filepath)
        category = _infer_category(filepath)
        importance = _infer_importance(filepath)
        provenance = {
            "source_type": "file",
            "source_ref": filepath,
            "author_agent": agent_id,
        }

        # Rimuovi memories esistenti per questo file se replace=True
        if replace_existing:
            existing = search_by_tag(file_tag, agent_id=agent_id, limit=200)
            for mem in existing:
                delete_memory(mem.id, agent_id=agent_id)
            if existing:
                file_result["status"] = "updated"
                stats["updated"] += 1
            else:
                file_result["status"] = "indexed"
                stats["indexed"] += 1
        else:
            # Controlla se esiste già
            existing = search_by_tag(file_tag, agent_id=agent_id, limit=1)
            if existing:
                file_result["status"] = "skipped"
                stats["skipped"] += 1
                stats["file_results"].append(file_result)
                continue
            file_result["status"] = "indexed"
            stats["indexed"] += 1

        # Chunking e salvataggio
        chunks = _chunk_content(content, filepath)
        file_result["chunks"] = len(chunks)
        memory_ids: list[int] = []

        from .models import MemorySaveRequest  # noqa: PLC0415

        for chunk_content_text, chunk_idx in chunks:
            chunk_label = f" [{chunk_idx + 1}/{len(chunks)}]" if len(chunks) > 1 else ""
            enriched_content = f"[File: {Path(filepath).name}{chunk_label}]\n{chunk_content_text.strip()}"
            # Tronca a 4000 chars (limite modello)
            enriched_content = enriched_content[:4000]

            req = MemorySaveRequest(
                content=enriched_content,
                category=category,
                importance=importance,
                provenance=provenance,
            )
            mem_id, _, _ = save_memory(req, agent_id=agent_id)
            # Tags: overlay marker + file identifier + chunk identifier
            add_tags(mem_id, [_build_tag_overlay(), file_tag], agent_id=agent_id)
            if len(chunks) > 1:
                add_tags(mem_id, [_build_tag_chunk(filepath, chunk_idx)], agent_id=agent_id)
            memory_ids.append(mem_id)

        file_result["memory_ids"] = memory_ids
        stats["file_results"].append(file_result)

    return stats


def list_overlay_files(agent_id: str = "default") -> list[dict]:
    """
    Restituisce la lista dei file attualmente indicizzati nell'overlay.
    Raggruppa le memories per source_ref (path file).
    """
    from .repository.search import search_by_tag

    memories = search_by_tag(_build_tag_overlay(), agent_id=agent_id, limit=1000)
    files: dict[str, dict] = {}

    for mem in memories:
        # MemoryRecord è un oggetto Pydantic — usa attribute access
        prov = mem.provenance or {}
        if isinstance(prov, str):
            import json

            try:
                prov = json.loads(prov)
            except Exception:
                prov = {}
        source_ref = prov.get("source_ref", "unknown") if isinstance(prov, dict) else "unknown"
        if source_ref not in files:
            files[source_ref] = {
                "path": source_ref,
                "filename": Path(source_ref).name,
                "exists": Path(source_ref).exists(),
                "memory_ids": [],
                "chunk_count": 0,
                "category": mem.category,
                "last_indexed": str(mem.created_at),
            }
        files[source_ref]["memory_ids"].append(mem.id)
        files[source_ref]["chunk_count"] += 1

    return list(files.values())


def remove_file_from_overlay(filepath: str, agent_id: str = "default") -> int:
    """
    Rimuove tutte le memories di un file dall'overlay.
    Ritorna il numero di memories eliminate.
    """
    from .repository import delete_memory
    from .repository.search import search_by_tag

    filepath = str(Path(filepath).resolve())
    file_tag = _build_tag_file(filepath)
    existing = search_by_tag(file_tag, agent_id=agent_id, limit=200)

    removed = 0
    for mem in existing:
        if delete_memory(mem.id, agent_id=agent_id):
            removed += 1

    return removed
