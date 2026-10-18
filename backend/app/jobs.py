"""Job records: queued → running → done | failed.

The API inserts queued jobs; the worker claims them atomically, saves result
artifacts, and only then commits the done state.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
import time

from app.db import transaction
from app.pipeline.types import Finding
from app.storage import Storage
from app.video import VideoInfo


def new_job_id() -> str:
    return secrets.token_hex(6)


def create_job(conn: sqlite3.Connection, job_id: str, filename: str, size: int, input_rel: str, duration: float) -> None:
    with transaction(conn):
        conn.execute(
            "INSERT INTO jobs (id, state, filename, size_bytes, input_path, duration_sec, created_at, stage)"
            " VALUES (?, 'queued', ?, ?, ?, ?, ?, 'queued')",
            (job_id, filename, size, input_rel, duration, time.time()),
        )


def get_job(conn: sqlite3.Connection, job_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()


def queue_position(conn: sqlite3.Connection, job: sqlite3.Row) -> int:
    """How many queued jobs are ahead of this one (0 = next to run)."""
    return conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE state = 'queued' AND created_at < ?", (job["created_at"],)
    ).fetchone()[0]


def claim_next(conn: sqlite3.Connection) -> sqlite3.Row | None:
    with transaction(conn, immediate=True):
        row = conn.execute(
            "SELECT id FROM jobs WHERE state = 'queued' ORDER BY created_at LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        conn.execute(
            "UPDATE jobs SET state = 'running', started_at = ?, attempts = attempts + 1,"
            " stage = 'claimed', progress = 0, error = NULL WHERE id = ?",
            (time.time(), row["id"]),
        )
    return get_job(conn, row["id"])


def set_stage(conn: sqlite3.Connection, job_id: str, stage: str, progress: int) -> None:
    with transaction(conn):
        conn.execute(
            "UPDATE jobs SET stage = ?, progress = ? WHERE id = ? AND state = 'running'",
            (stage, progress, job_id),
        )


def complete(
    conn: sqlite3.Connection,
    storage: Storage,
    job_id: str,
    info: VideoInfo,
    findings: list[Finding],
    pipeline_meta: dict,
) -> None:
    verdict = "violation" if findings else "no_violation"
    result = {
        "job_id": job_id,
        "verdict": verdict,
        "video": {
            "duration_sec": info.duration_sec,
            "width": info.width,
            "height": info.height,
            "avg_fps": info.avg_fps,
            "codec": info.codec,
        },
        "pipeline": pipeline_meta,
        "violations": [
            {
                "event_id": f.event_id,
                "event_type": f.event_type,
                "type": f.label,
                "timecode_sec": round(f.representative_sec, 3),
                "start_sec": round(f.start_sec, 3),
                "end_sec": round(f.end_sec, 3),
                "confidence": f.confidence,
                "description": f.explanation,
                "rules": [
                    {
                        "rule_id": r.rule_id,
                        "document": r.document,
                        "provision": r.provision,
                        "url": r.url,
                        "verified": r.verified,
                    }
                    for r in f.rules
                ],
                "evidence_frames": [
                    {"timestamp_sec": round(e.timestamp_sec, 3), "path": storage.relative(e.path)}
                    for e in f.evidence
                ],
            }
            for f in findings
        ],
    }
    # Artifacts first: the done state must never point at a missing result.
    result_path = storage.result_path(job_id)
    storage.write_json_atomic(result_path, result)

    with transaction(conn, immediate=True):
        conn.execute("DELETE FROM events WHERE job_id = ?", (job_id,))
        conn.executemany(
            "INSERT INTO events (job_id, event_id, event_type, start_sec, end_sec, representative_sec,"
            " confidence, explanation, rule_ids, evidence_paths) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    job_id,
                    f.event_id,
                    f.event_type,
                    f.start_sec,
                    f.end_sec,
                    f.representative_sec,
                    f.confidence,
                    f.explanation,
                    json.dumps([r.rule_id for r in f.rules]),
                    json.dumps([storage.relative(e.path) for e in f.evidence]),
                )
                for f in findings
            ],
        )
        conn.execute(
            "UPDATE jobs SET state = 'done', verdict = ?, result_path = ?, finished_at = ?,"
            " stage = 'done', progress = 100, duration_sec = ? WHERE id = ?",
            (verdict, storage.relative(result_path), time.time(), info.duration_sec, job_id),
        )


def fail(conn: sqlite3.Connection, job_id: str, error: str) -> None:
    with transaction(conn):
        conn.execute(
            "UPDATE jobs SET state = 'failed', error = ?, finished_at = ?, stage = 'failed' WHERE id = ?",
            (error[:2000], time.time(), job_id),
        )


def recover_interrupted(conn: sqlite3.Connection, max_attempts: int) -> tuple[int, int]:
    """Called by the worker at startup: jobs left 'running' by a crash or
    restart are requeued, unless they already used every attempt."""
    now = time.time()
    with transaction(conn, immediate=True):
        failed = conn.execute(
            "UPDATE jobs SET state = 'failed', finished_at = ?, stage = 'failed',"
            " error = 'Analysis was interrupted ' || attempts || ' times; giving up.'"
            " WHERE state = 'running' AND attempts >= ?",
            (now, max_attempts),
        ).rowcount
        requeued = conn.execute(
            "UPDATE jobs SET state = 'queued', stage = 'requeued', progress = 0 WHERE state = 'running'"
        ).rowcount
    return requeued, failed
