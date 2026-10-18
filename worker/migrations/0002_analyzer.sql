-- Remote analyzer (backend/app/remote.py): heartbeat for requeueing a job whose
-- analyzer went silent, and the analyzer's last poll for /api/health.
ALTER TABLE jobs ADD COLUMN heartbeat_at REAL;

CREATE TABLE IF NOT EXISTS analyzer_status (
    id       INTEGER PRIMARY KEY CHECK (id = 1),
    seen_at  REAL NOT NULL,
    info     TEXT NOT NULL
);
