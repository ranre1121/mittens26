"""SQLite access shared by the API and the worker.

WAL mode lets the API read job status while the worker writes; the busy
timeout makes a writer wait briefly instead of failing when the other process
holds the write lock. Keep transactions short.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id            TEXT PRIMARY KEY,
    state         TEXT NOT NULL CHECK (state IN ('queued', 'running', 'done', 'failed')),
    filename      TEXT NOT NULL,
    size_bytes    INTEGER NOT NULL,
    input_path    TEXT NOT NULL,
    duration_sec  REAL,
    created_at    REAL NOT NULL,
    started_at    REAL,
    finished_at   REAL,
    attempts      INTEGER NOT NULL DEFAULT 0,
    stage         TEXT,
    progress      INTEGER NOT NULL DEFAULT 0,
    error         TEXT,
    verdict       TEXT CHECK (verdict IN ('violation', 'no_violation')),
    result_path   TEXT
);
CREATE INDEX IF NOT EXISTS jobs_state_created ON jobs (state, created_at);

CREATE TABLE IF NOT EXISTS events (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id              TEXT NOT NULL REFERENCES jobs (id) ON DELETE CASCADE,
    event_id            TEXT NOT NULL,
    event_type          TEXT NOT NULL,
    start_sec           REAL NOT NULL,
    end_sec             REAL NOT NULL,
    representative_sec  REAL NOT NULL,
    confidence          REAL,
    explanation         TEXT NOT NULL,
    rule_ids            TEXT NOT NULL,   -- JSON array
    evidence_paths      TEXT NOT NULL,   -- JSON array, relative to the data dir
    UNIQUE (job_id, event_id)
);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # isolation_level=None: we open transactions explicitly with BEGIN.
    conn = sqlite3.connect(db_path, timeout=10, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 10000")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def init_db(db_path: Path) -> None:
    conn = connect(db_path)
    try:
        conn.executescript(SCHEMA)
    finally:
        conn.close()


@contextmanager
def transaction(conn: sqlite3.Connection, immediate: bool = False) -> Iterator[sqlite3.Connection]:
    """BEGIN IMMEDIATE takes the write lock up front, so a read-then-write
    (like claiming a job) cannot race another writer."""
    conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")
