-- Same jobs/events schema as backend/app/db.py, in D1.
-- The earlier Next.js app's `job` table (migration 0000) is left untouched.
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
    rule_ids            TEXT NOT NULL,
    evidence_paths      TEXT NOT NULL,
    UNIQUE (job_id, event_id)
);
