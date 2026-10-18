"""Read models for the Forte history and completion announcements."""

import re
import sqlite3

from app.db import transaction

PAGE_SIZE = 20
ACTIVITY_SIZE = 50
CURSOR = re.compile(r"^(\d{1,16})\.([a-f0-9]{0,32})$")
# The cursor is opaque to the browser. Preserve microseconds between polls;
# the id breaks ties when several jobs finish at the same time.
COMPLETED_AT = "CAST(finished_at * 1000000 AS INTEGER)"


def list_history(conn: sqlite3.Connection, q: str, status: str, page: int) -> dict:
    conditions, params = [], []
    if status != "all":
        conditions.append("j.state = ?")
        params.append("running" if status == "processing" else status)
    if q.strip():
        pattern = "%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        conditions.append("(j.filename LIKE ? ESCAPE '\\' OR j.id LIKE ? ESCAPE '\\')")
        params.extend([pattern, pattern])
    where = " AND ".join(conditions) or "1=1"
    # Count and page share a read snapshot while the worker updates jobs.
    with transaction(conn):
        total = conn.execute(f"SELECT COUNT(*) FROM jobs j WHERE {where}", params).fetchone()[0]
        page_count = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        page = min(page, page_count)
        rows = conn.execute(
            f"SELECT j.*, (SELECT COUNT(*) FROM events e WHERE e.job_id = j.id) AS event_count "
            f"FROM jobs j WHERE {where} ORDER BY j.created_at DESC, j.id DESC LIMIT ? OFFSET ?",
            [*params, PAGE_SIZE, (page - 1) * PAGE_SIZE],
        ).fetchall()
    return {
        "items": [
            {
                "id": row["id"], "filename": row["filename"], "sizeBytes": row["size_bytes"],
                "status": "processing" if row["state"] == "running" else row["state"],
                "progress": row["progress"], "verdict": row["verdict"],
                "violationCount": row["event_count"] if row["state"] == "done" else None,
                "createdAt": round(row["created_at"] * 1000),
                "finishedAt": round(row["finished_at"] * 1000) if row["finished_at"] is not None else None,
            } for row in rows
        ],
        "total": total, "page": page, "pageSize": PAGE_SIZE, "pageCount": page_count,
    }


def list_completions(conn: sqlite3.Connection, after: str | None) -> dict:
    if after is None:
        latest = conn.execute(
            f"SELECT id, {COMPLETED_AT} AS completed_at FROM jobs "
            "WHERE state = 'done' AND finished_at IS NOT NULL ORDER BY completed_at DESC, id DESC LIMIT 1"
        ).fetchone()
        cursor = f"{latest['completed_at']}.{latest['id']}" if latest else "0."
        return {"items": [], "cursor": cursor, "hasMore": False}
    match = CURSOR.fullmatch(after)
    if not match or int(match[1]) > 2**53 - 1:
        raise ValueError("Invalid activity cursor")
    timestamp, job_id = int(match[1]), match[2]
    rows = conn.execute(
        f"SELECT id, filename, finished_at, {COMPLETED_AT} AS completed_at FROM jobs "
        f"WHERE state = 'done' AND ({COMPLETED_AT} > ? OR ({COMPLETED_AT} = ? AND id > ?)) "
        "ORDER BY completed_at, id LIMIT ?",
        (timestamp, timestamp, job_id, ACTIVITY_SIZE + 1),
    ).fetchall()
    items = rows[:ACTIVITY_SIZE]
    cursor = f"{items[-1]['completed_at']}.{items[-1]['id']}" if items else after
    return {
        "items": [{"id": r["id"], "filename": r["filename"], "finishedAt": round(r["finished_at"] * 1000)} for r in items],
        "cursor": cursor, "hasMore": len(rows) > ACTIVITY_SIZE,
    }
