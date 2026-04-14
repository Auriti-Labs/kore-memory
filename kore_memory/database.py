"""
Kore - Database layer
Handles SQLite connection pool and schema initialization.
"""

import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from queue import Empty, Queue

from . import config

_POOL_SIZE = 4

# --- sqlite-vec availability ---
try:
    import sqlite_vec

    _HAS_SQLITE_VEC = True
except ImportError:
    _HAS_SQLITE_VEC = False


def _load_sqlite_vec(conn: sqlite3.Connection) -> None:
    """Load sqlite-vec extension on a connection (idempotent, silent if unavailable)."""
    if not _HAS_SQLITE_VEC:
        return
    try:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
    except Exception:
        pass  # already loaded or not available


def _get_db_path() -> Path:
    """Risolve il path del DB a runtime (supporta override via KORE_DB_PATH)."""
    # Controlla env var a runtime per supporto test con DB temporaneo
    return Path(os.getenv("KORE_DB_PATH", config.DEFAULT_DB_PATH))


# ── Connection Pool ─────────────────────────────────────────────────────────


class _ConnectionPool:
    """Simple thread-safe SQLite connection pool."""

    def __init__(self) -> None:
        self._pools: dict[str, Queue] = {}
        self._lock = threading.Lock()

    def _get_pool(self, db_path: str) -> Queue:
        with self._lock:
            if db_path not in self._pools:
                self._pools[db_path] = Queue(maxsize=_POOL_SIZE)
            return self._pools[db_path]

    def acquire(self, db_path: str) -> sqlite3.Connection:
        pool = self._get_pool(db_path)
        try:
            conn = pool.get_nowait()
            # Verifica che la connessione sia ancora valida
            conn.execute("SELECT 1")
            return conn
        except Empty:
            pass
        except Exception:
            # Connessione corrotta — chiudi per evitare fd leak
            try:
                conn.close()  # noqa: F821 — conn definita da get_nowait() sopra
            except (Exception, NameError):
                pass
        # Crea nuova connessione
        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        # Ottimizzazioni performance: 5-10x miglioramento write
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA temp_store=MEMORY")
        conn.execute("PRAGMA mmap_size=268435456")  # 256MB mmap
        conn.execute("PRAGMA cache_size=-32000")  # 32MB cache
        # Load sqlite-vec extension if available
        _load_sqlite_vec(conn)
        return conn

    def release(self, db_path: str, conn: sqlite3.Connection) -> None:
        pool = self._get_pool(db_path)
        try:
            pool.put_nowait(conn)
        except Exception:
            # Pool pieno — chiudi la connessione
            conn.close()

    def clear(self) -> None:
        """Chiudi tutte le connessioni nel pool (per test cleanup)."""
        with self._lock:
            for pool in self._pools.values():
                while not pool.empty():
                    try:
                        conn = pool.get_nowait()
                        conn.close()
                    except Empty:
                        break
            self._pools.clear()


_pool = _ConnectionPool()


def init_db() -> None:
    """Initialize the database and create tables if they don't exist."""
    db_path = _get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # Ensure DB file exists before chmod
    db_path.touch(exist_ok=True)
    db_path.chmod(0o600)  # owner only — protects memory data

    with get_connection() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS memories (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id        TEXT    NOT NULL DEFAULT 'default',
                content         TEXT    NOT NULL,
                category        TEXT    NOT NULL DEFAULT 'general',
                importance      INTEGER NOT NULL DEFAULT 1 CHECK (importance BETWEEN 1 AND 5),
                decay_score     REAL    NOT NULL DEFAULT 1.0,
                access_count    INTEGER NOT NULL DEFAULT 0,
                last_accessed   TEXT    DEFAULT NULL,
                compressed_into INTEGER DEFAULT NULL REFERENCES memories(id),
                embedding       TEXT    DEFAULT NULL,
                expires_at      TEXT    DEFAULT NULL,
                archived_at     TEXT    DEFAULT NULL,
                session_id      TEXT    DEFAULT NULL,
                created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
                updated_at      TEXT    NOT NULL DEFAULT (datetime('now')),
                -- Campi temporali v2.1
                valid_from      TEXT    DEFAULT NULL,
                valid_to        TEXT    DEFAULT NULL,
                invalidated_at  TEXT    DEFAULT NULL,
                supersedes_id   INTEGER DEFAULT NULL REFERENCES memories(id) ON DELETE SET NULL,
                confidence      REAL    DEFAULT 1.0 CHECK (confidence >= 0.0 AND confidence <= 1.0),
                provenance      TEXT    DEFAULT NULL,
                memory_type     TEXT    DEFAULT 'semantic'
                                CHECK (memory_type IN ('episodic','semantic','procedural','meta'))
            );

            CREATE INDEX IF NOT EXISTS idx_memories_agent      ON memories (agent_id);
            CREATE INDEX IF NOT EXISTS idx_memories_decay      ON memories (decay_score DESC);
            CREATE INDEX IF NOT EXISTS idx_memories_compressed ON memories (compressed_into);

            CREATE INDEX IF NOT EXISTS idx_memories_category  ON memories (category);
            CREATE INDEX IF NOT EXISTS idx_memories_importance ON memories (importance DESC);
            CREATE INDEX IF NOT EXISTS idx_memories_created_at ON memories (created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_memories_expires ON memories (expires_at) WHERE expires_at IS NOT NULL;
            CREATE INDEX IF NOT EXISTS idx_memories_archived ON memories (archived_at) WHERE archived_at IS NOT NULL;

            -- Indice composito per query search e decay_pass (agent + attive + ordinamento)
            CREATE INDEX IF NOT EXISTS idx_agent_decay_active
                ON memories (agent_id, compressed_into, archived_at, decay_score DESC);

            CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts
            USING fts5(content, category, content='memories', content_rowid='id', tokenize='unicode61');

            CREATE TRIGGER IF NOT EXISTS memories_ai
            AFTER INSERT ON memories BEGIN
                INSERT INTO memories_fts (rowid, content, category)
                VALUES (new.id, new.content, new.category);
            END;

            CREATE TRIGGER IF NOT EXISTS memories_ad
            AFTER DELETE ON memories BEGIN
                INSERT INTO memories_fts (memories_fts, rowid, content, category)
                VALUES ('delete', old.id, old.content, old.category);
            END;

            CREATE TRIGGER IF NOT EXISTS memories_au
            AFTER UPDATE ON memories BEGIN
                INSERT INTO memories_fts (memories_fts, rowid, content, category)
                VALUES ('delete', old.id, old.content, old.category);
                INSERT INTO memories_fts (rowid, content, category)
                VALUES (new.id, new.content, new.category);
            END;

            -- Tag per memorie
            CREATE TABLE IF NOT EXISTS memory_tags (
                memory_id   INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
                tag         TEXT    NOT NULL,
                PRIMARY KEY (memory_id, tag)
            );
            CREATE INDEX IF NOT EXISTS idx_tags_tag ON memory_tags (tag);

            -- Sessioni di conversazione
            CREATE TABLE IF NOT EXISTS sessions (
                id          TEXT    PRIMARY KEY,
                agent_id    TEXT    NOT NULL DEFAULT 'default',
                title       TEXT    DEFAULT NULL,
                created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
                ended_at    TEXT    DEFAULT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_sessions_agent ON sessions (agent_id);

            -- Relazioni tra memorie (grafo)
            CREATE TABLE IF NOT EXISTS memory_relations (
                source_id   INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
                target_id   INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
                relation    TEXT    NOT NULL DEFAULT 'related',
                strength    REAL    NOT NULL DEFAULT 1.0 CHECK (strength >= 0.0 AND strength <= 1.0),
                confidence  REAL    NOT NULL DEFAULT 1.0 CHECK (confidence >= 0.0 AND confidence <= 1.0),
                created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
                updated_at  TEXT    NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (source_id, target_id, relation)
            );
            CREATE INDEX IF NOT EXISTS idx_relations_source ON memory_relations (source_id);
            CREATE INDEX IF NOT EXISTS idx_relations_target ON memory_relations (target_id);
            -- idx_relations_strength creato dopo la migrazione v2.3 (colonna strength)

            -- Audit / event log
            CREATE TABLE IF NOT EXISTS event_logs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                event       TEXT    NOT NULL,
                agent_id    TEXT    NOT NULL DEFAULT 'default',
                memory_id   INTEGER DEFAULT NULL,
                data        TEXT    DEFAULT NULL,
                created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_event_logs_agent   ON event_logs (agent_id);
            CREATE INDEX IF NOT EXISTS idx_event_logs_event   ON event_logs (event);
            CREATE INDEX IF NOT EXISTS idx_event_logs_created ON event_logs (created_at DESC);

            -- Conflitti tra memorie (v2.1)
            CREATE TABLE IF NOT EXISTS memory_conflicts (
                id            TEXT    PRIMARY KEY,
                memory_a_id   INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
                memory_b_id   INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
                conflict_type TEXT    NOT NULL DEFAULT 'factual'
                              CHECK (conflict_type IN ('factual','temporal','preference','structural')),
                detected_at   TEXT    NOT NULL DEFAULT (datetime('now')),
                resolved_at   TEXT    DEFAULT NULL,
                resolved_by   TEXT    DEFAULT NULL,
                resolution    TEXT    DEFAULT NULL,
                agent_id      TEXT    NOT NULL DEFAULT 'default'
            );
            CREATE INDEX IF NOT EXISTS idx_conflicts_agent    ON memory_conflicts (agent_id, resolved_at);
            CREATE INDEX IF NOT EXISTS idx_conflicts_memory_a ON memory_conflicts (memory_a_id);
            CREATE INDEX IF NOT EXISTS idx_conflicts_memory_b ON memory_conflicts (memory_b_id);
        """)

        # Create sqlite-vec virtual table if extension is available
        if _HAS_SQLITE_VEC:
            try:
                embed_dim = int(os.getenv("KORE_EMBED_DIM", "384"))
                conn.execute(f"""
                    CREATE VIRTUAL TABLE IF NOT EXISTS vec_memories USING vec0(
                        agent_id TEXT partition key,
                        embedding float[{embed_dim}] distance_metric=cosine
                    )
                """)
            except Exception:
                pass  # extension not loaded on this connection

        # Migrazione idempotente per DB pre-esistenti
        cols = {row[1] for row in conn.execute("PRAGMA table_info(memories)").fetchall()}

        # Colonne pre-v2.1 (backward compat)
        _legacy_migrations = {
            "expires_at": "ALTER TABLE memories ADD COLUMN expires_at TEXT DEFAULT NULL",
            "archived_at": "ALTER TABLE memories ADD COLUMN archived_at TEXT DEFAULT NULL",
            "session_id": "ALTER TABLE memories ADD COLUMN session_id TEXT DEFAULT NULL",
        }
        for col, sql in _legacy_migrations.items():
            if col not in cols:
                conn.execute(sql)

        # Colonne v2.1 — temporal memory layer
        _v21_migrations = {
            "valid_from": "ALTER TABLE memories ADD COLUMN valid_from TEXT DEFAULT NULL",
            "valid_to": "ALTER TABLE memories ADD COLUMN valid_to TEXT DEFAULT NULL",
            "invalidated_at": "ALTER TABLE memories ADD COLUMN invalidated_at TEXT DEFAULT NULL",
            "supersedes_id": "ALTER TABLE memories ADD COLUMN supersedes_id INTEGER DEFAULT NULL",
            "confidence": "ALTER TABLE memories ADD COLUMN confidence REAL DEFAULT 1.0",
            "provenance": "ALTER TABLE memories ADD COLUMN provenance TEXT DEFAULT NULL",
            "memory_type": "ALTER TABLE memories ADD COLUMN memory_type TEXT DEFAULT 'semantic'",
        }
        for col, sql in _v21_migrations.items():
            if col not in cols:
                conn.execute(sql)

        # Indici v2.1 — creati dopo la migration per evitare errori su DB pre-esistenti
        conn.executescript("""
            CREATE INDEX IF NOT EXISTS idx_memories_valid_to
                ON memories (valid_to) WHERE valid_to IS NOT NULL;
            CREATE INDEX IF NOT EXISTS idx_memories_supersedes
                ON memories (supersedes_id) WHERE supersedes_id IS NOT NULL;
            CREATE INDEX IF NOT EXISTS idx_memories_invalidated
                ON memories (invalidated_at) WHERE invalidated_at IS NOT NULL;
        """)

        # Migrazione v2.3 — relazioni tipizzate con peso e confidence
        relation_cols = {row[1] for row in conn.execute("PRAGMA table_info(memory_relations)").fetchall()}
        _v23_relation_migrations = {
            "strength": "ALTER TABLE memory_relations ADD COLUMN strength REAL NOT NULL DEFAULT 1.0",
            "confidence": "ALTER TABLE memory_relations ADD COLUMN confidence REAL NOT NULL DEFAULT 1.0",
            "updated_at": "ALTER TABLE memory_relations ADD COLUMN updated_at TEXT NOT NULL DEFAULT (datetime('now'))",
        }
        for col, sql in _v23_relation_migrations.items():
            if col not in relation_cols:
                conn.execute(sql)
        # Indice su strength — creato qui (dopo ALTER TABLE) per DB pre-esistenti
        # e per DB nuovi (la CREATE TABLE usa IF NOT EXISTS, l'indice viene saltato sopra)
        conn.executescript("""
            CREATE INDEX IF NOT EXISTS idx_relations_strength
                ON memory_relations (strength DESC);
        """)


@contextmanager
def get_connection():
    """Yield a pooled thread-safe SQLite connection with WAL mode enabled."""
    db_path = str(_get_db_path())
    conn = _pool.acquire(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _pool.release(db_path, conn)
