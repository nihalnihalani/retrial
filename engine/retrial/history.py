"""RunHistory: lightweight SQLite persistence of completed runs.

PERSISTENCE MUST NEVER BREAK A RUN — the same rule that governs the registry.
Every write goes through registry's `_safe` (swallow everything, return None);
every read returns [] on ANY failure (missing db, corrupt file, locked writer,
unwritable path). No ORM: stdlib sqlite3, one connection per operation
(open -> do -> close) under a module lock — completed runs land seconds apart,
so contention is nil.

Every connect (read AND write) sets `PRAGMA journal_mode=WAL` and `PRAGMA
busy_timeout=250` first: a `GET /runs` that lands exactly as a run's INSERT
commits reads the last-committed state instead of queueing behind the writer,
and worst-case reader latency stays well under 10ms (single-row INSERT on WAL).
WAL's -wal/-shm sidecar files live inside .retrial/ (gitignored). The PRAGMAs
run inside the same _safe / returns-[] envelopes as everything else.
"""
import sqlite3
import threading
import uuid
from pathlib import Path

from .registry import _safe
from .settings import get_settings

# Runs finish seconds apart; one process-wide lock over the whole connect/write
# (or connect/read) keeps sqlite access strictly serial with negligible cost.
_LOCK = threading.Lock()

_SCHEMA = """CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY, kind TEXT NOT NULL,            -- 'tournament'|'bisect'
    test_name TEXT, verdict TEXT,
    orig_flake_rate REAL, final_flake_rate REAL,
    winner_model TEXT, braintrust_url TEXT,
    started_at REAL, finished_at REAL)"""              # wall-clock time.time()


def _connect(db_path):
    conn = sqlite3.connect(db_path, timeout=1.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=250")
    return conn


class RunHistory:
    """One tiny table, two operations: `record` (safe write) and `recent`
    (safe read). db_path is resolved at CALL time (None -> get_settings) so a
    monkeypatched RETRIAL_DB is honored without reconstructing the singleton."""

    def __init__(self, db_path=None):
        self._db_path = db_path

    def _path(self):
        return self._db_path or get_settings().retrial_db

    @_safe  # never break a run: a write failure is swallowed to a no-op (None)
    def record(self, kind, test_name, verdict, orig_flake_rate=None,
               final_flake_rate=None, winner_model=None, braintrust_url=None,
               started_at=None, finished_at=None):
        path = Path(self._path())
        path.parent.mkdir(parents=True, exist_ok=True)
        with _LOCK:
            conn = _connect(str(path))
            try:
                conn.executescript(_SCHEMA)
                conn.execute(
                    "INSERT INTO runs (id, kind, test_name, verdict, "
                    "orig_flake_rate, final_flake_rate, winner_model, "
                    "braintrust_url, started_at, finished_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (uuid.uuid4().hex, kind, test_name, verdict, orig_flake_rate,
                     final_flake_rate, winner_model, braintrust_url,
                     started_at, finished_at))
                conn.commit()
            finally:
                conn.close()

    def recent(self, limit=20):
        """Newest-first list of run rows as dicts; [] on ANY failure (missing
        or corrupt db). Pure reader — never raises."""
        try:
            path = Path(self._path())
            if not path.exists():
                return []
            with _LOCK:
                conn = _connect(str(path))
                try:
                    conn.row_factory = sqlite3.Row
                    cur = conn.execute(
                        "SELECT id, kind, test_name, verdict, orig_flake_rate, "
                        "final_flake_rate, winner_model, braintrust_url, "
                        "started_at, finished_at FROM runs "
                        "ORDER BY finished_at DESC, rowid DESC LIMIT ?",
                        (int(limit),))
                    return [dict(r) for r in cur.fetchall()]
                finally:
                    conn.close()
        except Exception:
            return []


# Process-wide singleton (like REGISTRY): the server records through it and the
# /runs endpoint reads through it.
HISTORY = RunHistory()
