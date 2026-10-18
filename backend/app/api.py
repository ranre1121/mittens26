"""HTTP process: jury API, interface API and the static frontend.

    uvicorn app.api:app

The API never runs inference. It validates and stores uploads, creates queued
jobs, and reads job state and results written by the worker.

No `from __future__ import annotations` here: FastAPI must resolve the
dependency aliases declared inside create_app() at definition time.
"""

import base64
import json
import re
import secrets
import sqlite3
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Iterator, Literal

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from fastapi.security import APIKeyHeader
from fastapi.staticfiles import StaticFiles

from app import jobs
from app.config import Settings, load_settings
from app.db import connect, init_db
from app.history import list_completions, list_history
from app.storage import Storage
from app.video import InvalidVideo, probe

CHUNK = 1024 * 1024
EVIDENCE_NAME = re.compile(r"^e\d+-[\d.]+\.jpg$")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    storage = Storage(settings.data_dir)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        storage.ensure()
        init_db(settings.db_path)
        yield

    app = FastAPI(title="Контроль кассы", version="0.1.0", lifespan=lifespan)

    def db() -> Iterator[sqlite3.Connection]:
        conn = connect(settings.db_path)
        try:
            yield conn
        finally:
            conn.close()

    api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

    def require_api_key(key: Annotated[str | None, Depends(api_key_header)]) -> None:
        # Constant-time comparison; the key itself is never logged.
        if not key or not any(secrets.compare_digest(key, k) for k in settings.api_keys):
            raise HTTPException(status_code=401, detail="Missing or invalid X-API-Key")

    Db = Annotated[sqlite3.Connection, Depends(db)]

    # ---------- shared helpers ----------

    async def accept_upload(request: Request, file: UploadFile, conn: sqlite3.Connection) -> str:
        declared = int(request.headers.get("content-length") or 0)
        if declared > settings.max_upload_bytes + CHUNK:
            raise HTTPException(413, f"File is larger than {settings.max_upload_bytes // 2**20} MB")
        name = Path(file.filename or "video.mov").name
        ext = Path(name).suffix.lower()
        if ext not in settings.allowed_extensions:
            raise HTTPException(400, f"Unsupported file type {ext or '(none)'}; upload a .mov video")

        # Stream into data/tmp; only a complete, valid file is moved into uploads/.
        tmp = tempfile.NamedTemporaryFile(dir=storage.tmp_dir, suffix=ext, delete=False)
        tmp_path = Path(tmp.name)
        size = 0
        try:
            with tmp:
                while chunk := await file.read(CHUNK):
                    size += len(chunk)
                    if size > settings.max_upload_bytes:
                        raise HTTPException(413, f"File is larger than {settings.max_upload_bytes // 2**20} MB")
                    tmp.write(chunk)
            if size == 0:
                raise HTTPException(400, "File is empty")
            try:
                info = await run_in_threadpool(probe, tmp_path)
            except InvalidVideo as exc:
                raise HTTPException(400, f"Not a readable video: {exc}") from exc

            job_id = jobs.new_job_id()
            final = storage.finalize_upload(tmp_path, job_id, ext)
            jobs.create_job(conn, job_id, name, size, storage.relative(final), info.duration_sec)
            return job_id
        finally:
            tmp_path.unlink(missing_ok=True)

    def load_job(conn: sqlite3.Connection, job_id: str) -> sqlite3.Row:
        job = jobs.get_job(conn, job_id)
        if job is None:
            raise HTTPException(404, "Job not found")
        return job

    def load_result(job: sqlite3.Row) -> dict:
        try:
            return json.loads(storage.resolve(job["result_path"]).read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as exc:
            raise HTTPException(500, "Result is missing for a finished job") from exc

    def public_view(job: sqlite3.Row) -> dict:
        """Exactly the jury schema (case section 10)."""
        if job["state"] in ("queued", "running"):
            return {"job_id": job["id"], "status": "processing"}
        if job["state"] == "failed":
            return {"job_id": job["id"], "status": "failed", "error": job["error"] or "Analysis failed"}
        result = load_result(job)
        violations = []
        for v in result["violations"]:
            frames = []
            for f in v["evidence_frames"]:
                data = storage.resolve(f["path"]).read_bytes()
                frames.append(
                    {"timestamp_sec": f["timestamp_sec"], "image_base64": base64.b64encode(data).decode()}
                )
            violations.append(
                {
                    "type": v["type"],
                    "timecode_sec": v["timecode_sec"],
                    # The schema has no rule field, so the reference is part of the description.
                    "description": v["description"],
                    "evidence_frames": frames,
                }
            )
        return {"job_id": job["id"], "status": "done", "verdict": result["verdict"], "violations": violations}

    def ui_view(conn: sqlite3.Connection, job: sqlite3.Row) -> dict:
        """The interface needs more than the jury: stage, progress, structured
        rules and evidence URLs instead of inline base64."""
        base = {
            "job_id": job["id"],
            "state": job["state"],
            "status": "processing" if job["state"] in ("queued", "running") else job["state"],
            "stage": job["stage"],
            "progress": job["progress"],
            "queue_position": jobs.queue_position(conn, job) if job["state"] == "queued" else None,
            "filename": job["filename"],
            "size_bytes": job["size_bytes"],
            "duration_sec": job["duration_sec"],
            "created_at": job["created_at"],
            "finished_at": job["finished_at"],
            "error": job["error"],
        }
        if job["state"] != "done":
            return base
        result = load_result(job)
        base["verdict"] = result["verdict"]
        if result.get("pipeline", {}).get("detector", "").startswith("stub:"):
            base["analysis"] = {
                "is_demo": True,
                "checked_types": [],
                "limitations": ["Тестовый детектор не анализирует содержимое кадров. Результат не является проверкой кассовых операций."],
            }
        base["violations"] = [
            {
                "event_id": v["event_id"],
                "event_type": v["event_type"],
                "type": v["type"],
                "timecode_sec": v["timecode_sec"],
                "start_sec": v["start_sec"],
                "end_sec": v["end_sec"],
                "description": v["description"],
                "rules": v["rules"],
                "evidence_frames": [
                    {
                        "timestamp_sec": f["timestamp_sec"],
                        "url": f"/api/jobs/{job['id']}/evidence/{Path(f['path']).name}",
                    }
                    for f in v["evidence_frames"]
                ],
            }
            for v in result["violations"]
        ]
        return base

    # ---------- jury API ----------

    @app.post("/api/v1/analyze", dependencies=[Depends(require_api_key)])
    async def analyze(request: Request, file: Annotated[UploadFile, File()], conn: Db) -> dict:
        return {"job_id": await accept_upload(request, file, conn)}

    @app.get("/api/v1/jobs/{job_id}", dependencies=[Depends(require_api_key)])
    def jury_job(job_id: str, conn: Db) -> dict:
        return public_view(load_job(conn, job_id))

    # ---------- interface API (no key: the interface has no login) ----------

    @app.post("/api/jobs")
    async def ui_upload(request: Request, file: Annotated[UploadFile, File()], conn: Db) -> dict:
        return {"job_id": await accept_upload(request, file, conn)}

    @app.get("/api/jobs")
    def ui_history(
        conn: Db,
        q: Annotated[str, Query(max_length=120)] = "",
        status: Literal["all", "queued", "processing", "done", "failed"] = "all",
        page: Annotated[int, Query(ge=1, le=9999999)] = 1,
    ):
        return list_history(conn, q, status, page)

    # Static path must precede /api/jobs/{job_id}.
    @app.get("/api/jobs/activity")
    def ui_activity(conn: Db, after: str | None = None):
        try:
            return list_completions(conn, after)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/jobs/{job_id}")
    def ui_job(job_id: str, conn: Db) -> dict:
        return ui_view(conn, load_job(conn, job_id))

    @app.get("/api/jobs/{job_id}/video")
    def ui_video(job_id: str, conn: Db) -> FileResponse:
        job = load_job(conn, job_id)
        # Browsers decline "video/quicktime" even for H.264 .mov files, so serve it as mp4.
        return FileResponse(storage.resolve(job["input_path"]), media_type="video/mp4")

    @app.get("/api/jobs/{job_id}/evidence/{name}")
    def ui_evidence(job_id: str, name: str, conn: Db) -> FileResponse:
        load_job(conn, job_id)
        path = storage.evidence_dir(job_id) / name
        if not EVIDENCE_NAME.match(name) or not path.is_file():
            raise HTTPException(404, "Evidence frame not found")
        return FileResponse(path, media_type="image/jpeg")

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True}

    # ---------- static frontend (Next.js export), mounted last ----------

    if settings.frontend_dir.is_dir():
        app.mount("/", StaticFiles(directory=settings.frontend_dir, html=True), name="frontend")

    return app


app = create_app()
