"""HTTP process: jury API, interface API and the static frontend.

    uvicorn app.api:app

The API never runs inference. It validates and stores uploads, creates queued
jobs, and reads job state and results written by the worker.

No `from __future__ import annotations` here: FastAPI must resolve the
dependency aliases declared inside create_app() at definition time.
"""

import base64
import asyncio
import json
import re
import secrets
import sqlite3
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Iterator, Literal

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.security import APIKeyHeader
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import jobs, session
from app.config import Settings, load_settings
from app.db import connect, init_db
from app.history import list_completions, list_history
from app.storage import Storage
from app.video import InvalidVideo, probe_bounded
from app.pipeline.providers import capabilities
from app.limits import UploadLimitMiddleware

CHUNK = 1024 * 1024
EVIDENCE_NAME = re.compile(r"^e\d+-[\d.]+\.jpg$")
# Reachable without a key: probes, and the login endpoint itself.
OPEN_PATHS = {"/api/health", "/api/ready", "/api/session"}
# Cookie-authenticated writes must carry this header. Cross-site forms can't
# set custom headers, so this blocks request forgery on top of SameSite=Strict.
CSRF_HEADER = "x-requested-with"
CSRF_VALUE = "kassa-control"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


class LoginBody(BaseModel):
    key: str


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    storage = Storage(settings.data_dir)
    upload_slots = asyncio.Semaphore(2)

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

    session_key = session.signing_key(settings.session_secret, settings.api_keys)
    login_throttle = session.LoginThrottle()

    def header_key_ok(request: Request) -> bool:
        key = request.headers.get("x-api-key")
        return bool(key) and any(secrets.compare_digest(key, k) for k in settings.api_keys)

    def session_expiry(request: Request) -> int | None:
        return session.verify(session_key, request.cookies.get(session.COOKIE_NAME))

    def is_loopback(request: Request) -> bool:
        return bool(request.client) and request.client.host in {"127.0.0.1", "::1", "testclient"}

    @app.middleware("http")
    async def private_api(request: Request, call_next):
        # UI routes include footage; protect them whenever API keys are configured.
        # Scripts and the jury send X-API-Key; the browser interface logs in once
        # with the same key and then sends the session cookie.
        # With no keys, permit loopback only (local demo). Never trust forwarded IP.
        if request.url.path.startswith("/api/") and request.url.path not in OPEN_PATHS:
            if settings.api_keys:
                if header_key_ok(request):
                    pass
                elif session_expiry(request) is not None:
                    if request.method not in SAFE_METHODS and request.headers.get(CSRF_HEADER) != CSRF_VALUE:
                        return JSONResponse({"detail": "Missing X-Requested-With header"}, status_code=403)
                else:
                    return JSONResponse({"detail": "Missing or invalid X-API-Key"}, status_code=401)
            elif not is_loopback(request):
                return JSONResponse({"detail": "Local access only; configure API_KEYS"}, status_code=403)
        if request.method == "POST":
            try:
                declared = int(request.headers.get("content-length", "0"))
                if declared < 0:
                    raise ValueError()
            except ValueError:
                return JSONResponse({"detail": "Invalid Content-Length"}, status_code=400)
            if declared > settings.max_upload_bytes + CHUNK:
                return JSONResponse({"detail": "Upload too large"}, status_code=413)
        return await call_next(request)

    # ---------- shared helpers ----------

    async def accept_upload(request: Request, file: UploadFile, conn: sqlite3.Connection) -> str:
        if conn.execute("SELECT COUNT(*) FROM jobs WHERE state IN ('queued','running')").fetchone()[0] >= settings.max_pending_jobs:
            raise HTTPException(429, "Inference queue is full")
        if file.content_type not in {"video/mp4", "video/quicktime", "video/x-m4v", "application/octet-stream"}:
            raise HTTPException(400, "Unsupported video media type")
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
                async with upload_slots:
                    info = await run_in_threadpool(probe_bounded, tmp_path)
            except InvalidVideo as exc:
                raise HTTPException(400, f"Not a readable video: {exc}") from exc

            if info.duration_sec > settings.max_video_seconds or info.width * info.height > settings.max_video_pixels:
                raise HTTPException(400, "Video exceeds configured duration or pixel limits")
            job_id = jobs.new_job_id()
            final = storage.finalize_upload(tmp_path, job_id, ext)
            try:
                jobs.create_job(conn, job_id, name, size, storage.relative(final), info.duration_sec, settings.max_pending_jobs)
            except jobs.QueueFull:
                final.unlink(missing_ok=True)
                final.parent.rmdir()
                raise HTTPException(429, "Inference queue is full")
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
            return {"job_id": job["id"], "status": "failed", "error": job["error"] or "Analysis failed", "models": capabilities(settings)}
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
        return {"job_id": job["id"], "status": "done", "verdict": result["verdict"], "violations": violations, "analysis": result.get("analysis", {})}

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
        base["analysis"] = result.get("analysis", {})
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

    # ---------- interface API (X-API-Key header or the login session cookie) ----------

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

    # ---------- browser sessions (interface login with the API key) ----------

    @app.get("/api/session")
    def session_status(request: Request):
        if not settings.api_keys:
            if is_loopback(request):
                return {"authenticated": True, "method": "local"}
            return JSONResponse({"detail": "Local access only; configure API_KEYS"}, status_code=403)
        if header_key_ok(request):
            return {"authenticated": True, "method": "header"}
        if (exp := session_expiry(request)) is not None:
            return {"authenticated": True, "method": "cookie", "expires_at": exp}
        return JSONResponse({"detail": "Login required"}, status_code=401)

    @app.post("/api/session")
    def session_login(body: LoginBody, request: Request):
        if not settings.api_keys:
            raise HTTPException(400, "Login is not configured; set API_KEYS")
        if not login_throttle.allow(request.client.host if request.client else "unknown"):
            raise HTTPException(429, "Too many login attempts; try again later")
        if not any(secrets.compare_digest(body.key, k) for k in settings.api_keys):
            raise HTTPException(401, "Invalid key")
        token, exp = session.issue(session_key, settings.session_hours)
        response = JSONResponse({"authenticated": True, "method": "cookie", "expires_at": exp})
        response.set_cookie(
            session.COOKIE_NAME,
            token,
            max_age=int(settings.session_hours * 3600),
            path="/api",
            httponly=True,
            samesite="strict",
            secure=request.url.scheme == "https",
        )
        return response

    @app.delete("/api/session", status_code=204)
    def session_logout():
        response = Response(status_code=204)
        response.delete_cookie(session.COOKIE_NAME, path="/api", httponly=True, samesite="strict")
        return response

    @app.get("/api/ready")
    def readiness(conn: Db):
        conn.execute("SELECT 1").fetchone()
        try:
            heartbeat = json.loads((settings.data_dir / "worker-status.json").read_text())
            alive = time.time() - heartbeat["timestamp"] < max(10, settings.worker_poll_seconds * 3)
            os_pid = heartbeat["pid"]
            import os
            os.kill(os_pid, 0)
        except (OSError, ValueError, KeyError, TypeError):
            alive = False
        body = {"ready": alive, "models": capabilities(settings), "worker_available": alive}
        return JSONResponse(body, status_code=200 if alive else 503)

    @app.get("/api/v1/models", dependencies=[Depends(require_api_key)])
    def models():
        return capabilities(settings)

    @app.get("/api/v1/jobs/{job_id}/results", dependencies=[Depends(require_api_key)])
    def results(job_id: str, conn: Db):
        job = load_job(conn, job_id)
        if job["state"] != "done":
            raise HTTPException(409, "Result is not available")
        return load_result(job)

    # ---------- static frontend (Next.js export), mounted last ----------

    if settings.frontend_dir.is_dir():
        app.mount("/", StaticFiles(directory=settings.frontend_dir, html=True), name="frontend")

    app.add_middleware(UploadLimitMiddleware, limit=settings.max_upload_bytes + CHUNK)
    return app


app = create_app()
