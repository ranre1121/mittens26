"""FastAPI on a Cloudflare Python Worker: the interface API and the jury API,
with jobs in D1 and media in R2. Analysis runs on the authenticated
remote NVIDIA service; the edge runtime only submits and polls jobs.

Uploads and video playback bypass FastAPI. The ASGI adapter copies the whole
request body into Python memory, and ~30 MB videos would push the Worker
toward its 128 MB limit, so those two routes pass the browser's file straight
to and from R2 on the JavaScript side.
"""

import base64
import json
import re
import secrets
import time
from urllib.parse import urlparse

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from js import AbortSignal, fetch as js_fetch
from remote import RemoteInference, RemoteError
from js import Headers as JsHeaders
from js import Object
from js import Response as JsResponse
from pyodide.ffi import to_js
from workers import WorkerEntrypoint, asgi

ALLOWED_EXTENSIONS = (".mov", ".mp4", ".m4v")
# First box types of MP4/QuickTime files; anything else is not a video we accept.
VIDEO_BOXES = (b"ftyp", b"moov", b"mdat", b"wide", b"free", b"skip", b"pnot")
EVIDENCE_NAME = re.compile(r"^e\d+-[\d.]+\.jpg$")
UPLOAD_PATHS = {"/api/v1/analyze": True, "/api/jobs": False}  # path -> needs key
VIDEO_PATH = re.compile(r"^/api/jobs/([0-9a-f]{12})/video$")


def js_obj(d: dict):
    return to_js(d, dict_converter=Object.fromEntries)


def js_json(body: dict, status: int = 200):
    return JsResponse.new(
        json.dumps(body, ensure_ascii=False),
        status=status,
        headers=JsHeaders.new(to_js([["content-type", "application/json"]])),
    )


def api_key_ok(env, given: str | None) -> bool:
    keys = [k.strip() for k in str(getattr(env, "API_KEYS", "") or "").split(",") if k.strip()]
    return bool(given) and any(secrets.compare_digest(given, k) for k in keys)


def max_upload_bytes(env) -> int:
    return int(float(getattr(env, "MAX_UPLOAD_MB", "95") or 95) * 1024 * 1024)


def py(value):
    """The SDK already converts D1 results to Python; plain JS proxies still need to_py()."""
    return value.to_py() if hasattr(value, "to_py") else value


async def d1_first(env, sql: str, *args):
    return py(await env.DB.prepare(sql).bind(*args).first())


async def d1_all(env, sql: str, *args) -> list[dict]:
    res = py(await env.DB.prepare(sql).bind(*args).all())
    return list(res["results"] if isinstance(res, dict) else py(res.results))


async def remote_request(url, *, method, body, headers, timeout_ms, binary):
    options = {"method": method, "headers": js_obj(headers),
               "signal": AbortSignal.timeout(timeout_ms), "redirect": "error"}
    if body is not None:
        options["body"] = body
    response = await js_fetch(url, js_obj(options))
    if not response.ok:
        raise RemoteError(f"Inference service HTTP {int(response.status)}")
    limit = 2 * 1024 * 1024 if binary else 8 * 1024 * 1024
    if int(response.headers.get("content-length") or 0) > limit:
        raise RemoteError("Inference service response exceeds its size limit")
    if binary:
        content = (await response.arrayBuffer()).to_bytes()
    else:
        content = str(await response.text())
    if len(content) > limit:
        raise RemoteError("Inference service response exceeds its size limit")
    return content if binary else json.loads(content)


def remote_client(env):
    return RemoteInference(getattr(env, "INFERENCE_API_URL", ""),
                           getattr(env, "INFERENCE_API_KEY", ""), remote_request,
                           getattr(env, "INFERENCE_TIMEOUT_MS", 30000))


async def refresh_job(env, job):
    if job["state"] not in ("queued", "running") or not job.get("remote_job_id"):
        return job
    if time.time() - job["created_at"] > 3600:
        await env.DB.prepare("UPDATE jobs SET state='failed', stage='failed', error='Remote inference deadline exceeded', finished_at=? WHERE id=? AND state IN ('queued','running')").bind(time.time(), job["id"]).run()
        return await d1_first(env, "SELECT * FROM jobs WHERE id=?", job["id"])
    remote = remote_client(env)
    try:
        status = await remote.status(job["remote_job_id"])
        if status["status"] == "failed":
            await env.DB.prepare("UPDATE jobs SET state='failed', stage='failed', error='Remote inference failed', finished_at=? WHERE id=? AND state IN ('queued','running')").bind(time.time(), job["id"]).run()
        elif status["status"] == "done":
            result = await remote.results(job["remote_job_id"])
            for event in result["violations"]:
                for frame in event.get("evidence_frames", []):
                    name = str(frame["path"]).rsplit("/", 1)[-1]
                    content = await remote.evidence(job["remote_job_id"], name)
                    frame["path"] = f"evidence/{job['id']}/{name}"
                    await env.MEDIA.put(frame["path"], to_js(content))
            for frame in result.get("analysis", {}).get("frames", []):
                name = str(frame.get("frame_ref", ""))
                if EVIDENCE_NAME.fullmatch(name):
                    frame["url"] = f"/api/jobs/{job['id']}/evidence/{name}"
            result["job_id"] = job["id"]
            key = f"results/{job['id']}.json"
            await env.MEDIA.put(key, json.dumps(result))
            # Existing D1 verdict enum cannot express candidate/insufficient decisions; preserve it in R2.
            verdict = result.get("verdict")
            stored_verdict = verdict if verdict in ("violation", "no_violation") else None
            await env.DB.prepare("UPDATE jobs SET state='done', stage='done', progress=100, result_path=?, verdict=?, duration_sec=?, finished_at=? WHERE id=? AND state IN ('queued','running')").bind(key, stored_verdict, result.get("video", {}).get("duration_sec"), time.time(), job["id"]).run()
    except Exception:
        # Poll failures are retryable and never converted to successful analysis.
        raise HTTPException(502, "Inference service temporarily unavailable")
    return await d1_first(env, "SELECT * FROM jobs WHERE id=?", job["id"])


# ---------- uploads and video (JavaScript side, no Python copy of the body) ----------


async def reject(js_req, body: dict, status: int):
    """Answer before reading the upload; drain the body first, otherwise the
    client sees a dropped connection instead of the error response."""
    if js_req.body is not None and not js_req.bodyUsed:
        await js_req.arrayBuffer()
    return js_json(body, status)


async def handle_upload(js_req, env, needs_key: bool):
    if needs_key and not api_key_ok(env, js_req.headers.get("x-api-key")):
        return await reject(js_req, {"detail": "Missing or invalid X-API-Key"}, 401)

    try:
        remote = remote_client(env)
    except (RemoteError, ValueError):
        return await reject(js_req, {"detail": "Inference service is not configured"}, 503)

    limit = max_upload_bytes(env)
    try:
        declared = int(js_req.headers.get("content-length") or 0)
        if declared < 0:
            raise ValueError()
    except (TypeError, ValueError):
        return await reject(js_req, {"detail": "Invalid Content-Length"}, 400)
    if declared > limit + 1024 * 1024:
        return await reject(js_req, {"detail": f"File is larger than {limit // 2**20} MB"}, 413)

    try:
        form = await js_req.formData()
    except Exception:
        return js_json({"detail": "Expected multipart/form-data with a `file` field"}, 400)
    file = form.get("file")
    if file is None or isinstance(file, str):
        return js_json({"detail": "Missing `file` field"}, 400)

    name = str(file.name or "video.mov").rsplit("/", 1)[-1]
    ext = name[name.rfind("."):].lower() if "." in name else ""
    if ext not in ALLOWED_EXTENSIONS:
        return js_json({"detail": f"Unsupported file type {ext or '(none)'}; upload a .mov video"}, 400)
    size = int(file.size)
    if size == 0:
        return js_json({"detail": "File is empty"}, 400)
    if size > limit:
        return js_json({"detail": f"File is larger than {limit // 2**20} MB"}, 413)
    # No FFmpeg here, so check the container signature instead of decoding.
    head = (await file.slice(0, 16).arrayBuffer()).to_bytes()
    if head[4:8] not in VIDEO_BOXES:
        return js_json({"detail": "Not a readable video: not an MP4/QuickTime file"}, 400)

    job_id = secrets.token_hex(6)
    key = f"uploads/{job_id}/input{ext}"
    content_type = "video/quicktime" if ext == ".mov" else "video/mp4"
    await env.MEDIA.put(key, file, js_obj({"httpMetadata": js_obj({"contentType": content_type})}))
    await env.DB.prepare(
        "INSERT INTO jobs (id, state, filename, size_bytes, input_path, created_at, stage)"
        " VALUES (?, 'queued', ?, ?, ?, ?, 'queued')"
    ).bind(job_id, name, size, key, time.time()).run()
    try:
        remote_id = await remote.submit(form)
        await env.DB.prepare("UPDATE jobs SET remote_job_id=?, state='running', stage='remote_inference', started_at=? WHERE id=?").bind(remote_id, time.time(), job_id).run()
    except Exception:
        await env.DB.prepare("UPDATE jobs SET state='failed', stage='failed', error='Remote submission failed or timed out; outcome unknown', finished_at=? WHERE id=?").bind(time.time(), job_id).run()
        return js_json({"job_id": job_id, "detail": "Remote submission failed; no automatic retry"}, 502)
    return js_json({"job_id": job_id}, 202)


async def handle_video(js_req, env, job_id: str):
    job = await d1_first(env, "SELECT input_path FROM jobs WHERE id = ?", job_id)
    if job is None:
        return js_json({"detail": "Job not found"}, 404)
    range_header = js_req.headers.get("range")
    options = js_obj({"range": js_req.headers}) if range_header else js_obj({})
    obj = await env.MEDIA.get(job["input_path"], options)
    if obj is None:
        return js_json({"detail": "Video not found"}, 404)

    headers = [
        # Browsers decline "video/quicktime" even for H.264 .mov, so serve it as mp4.
        ["content-type", "video/mp4"],
        ["accept-ranges", "bytes"],
        ["cache-control", "private, max-age=3600"],
        ["etag", obj.httpEtag],
    ]
    rng = obj.range
    if range_header and rng is not None and getattr(rng, "offset", None) is not None:
        offset = int(rng.offset)
        length = int(rng.length) if getattr(rng, "length", None) is not None else int(obj.size) - offset
        headers += [
            ["content-range", f"bytes {offset}-{offset + length - 1}/{int(obj.size)}"],
            ["content-length", str(length)],
        ]
        return JsResponse.new(obj.body, status=206, headers=JsHeaders.new(to_js(headers)))
    headers.append(["content-length", str(int(obj.size))])
    return JsResponse.new(obj.body, status=200, headers=JsHeaders.new(to_js(headers)))


# ---------- FastAPI: everything else ----------

app = FastAPI(title="Контроль кассы", version="0.1.0")


def env_of(request: Request):
    return request.scope["env"]


async def load_job(env, job_id: str) -> dict:
    job = await d1_first(env, "SELECT * FROM jobs WHERE id = ?", job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    return await refresh_job(env, job)


async def load_result(env, job: dict) -> dict:
    obj = await env.MEDIA.get(job["result_path"])
    if obj is None:
        raise HTTPException(500, "Result is missing for a finished job")
    return json.loads(await obj.text())


async def read_bytes(env, key: str) -> bytes:
    obj = await env.MEDIA.get(key)
    if obj is None:
        raise HTTPException(500, f"Missing object {key}")
    return (await obj.arrayBuffer()).to_bytes()


@app.get("/api/health")
async def health(request: Request):
    try:
        remote_client(env_of(request))
        configured = True
    except (RemoteError, ValueError):
        configured = False
    return {"ok": True, "analysis": "remote", "remote_configured": configured,
            "live_service_verified": False}


@app.get("/api/ready")
async def ready(request: Request):
    try:
        env = env_of(request)
        await d1_first(env, "SELECT 1 AS available")
        readiness = await remote_client(env).ready()
    except Exception:
        return JSONResponse({"ready": False, "remote_available": False}, status_code=503)
    return JSONResponse({**readiness, "remote_available": True}, status_code=200 if readiness["ready"] else 503)


@app.get("/api/v1/models")
async def models(request: Request, x_api_key: str | None = Header(default=None)):
    env = env_of(request)
    if not api_key_ok(env, x_api_key):
        raise HTTPException(401, "Missing or invalid X-API-Key")
    try:
        return await remote_client(env).models()
    except Exception:
        raise HTTPException(502, "Inference model status temporarily unavailable")


@app.get("/api/v1/jobs/{job_id}/results")
async def remote_result(job_id: str, request: Request, x_api_key: str | None = Header(default=None)):
    env = env_of(request)
    if not api_key_ok(env, x_api_key):
        raise HTTPException(401, "Missing or invalid X-API-Key")
    job = await load_job(env, job_id)
    if job["state"] != "done":
        raise HTTPException(409, "Result is not available")
    return await load_result(env, job)


@app.get("/api/v1/jobs/{job_id}")
async def jury_job(job_id: str, request: Request, x_api_key: str | None = Header(default=None)):
    """Jury API: exactly the schema from section 10 of the case."""
    env = env_of(request)
    if not api_key_ok(env, x_api_key):
        return JSONResponse({"detail": "Missing or invalid X-API-Key"}, status_code=401)
    job = await load_job(env, job_id)
    if job["state"] in ("queued", "running"):
        return {"job_id": job_id, "status": "processing"}
    if job["state"] == "failed":
        return {"job_id": job_id, "status": "failed", "error": job["error"] or "Analysis failed"}
    result = await load_result(env, job)
    violations = []
    for v in result["violations"]:
        frames = [
            {
                "timestamp_sec": f["timestamp_sec"],
                "image_base64": base64.b64encode(await read_bytes(env, f["path"])).decode(),
            }
            for f in v["evidence_frames"]
        ]
        violations.append(
            {
                "type": v["type"],
                "timecode_sec": v["timecode_sec"],
                "description": v["description"],
                "evidence_frames": frames,
            }
        )
    return {"job_id": job_id, "status": "done", "verdict": result["verdict"], "violations": violations, "analysis": result.get("analysis", {})}


# History and completion activity: same responses as backend/app/history.py.
PAGE_SIZE = 20
ACTIVITY_SIZE = 50
CURSOR = re.compile(r"^(\d{1,16})\.([a-f0-9]{0,32})$")
COMPLETED_AT = "CAST(finished_at * 1000000 AS INTEGER)"


@app.get("/api/jobs")
async def ui_history(request: Request, q: str = "", status: str = "all", page: int = 1):
    if len(q) > 120 or status not in ("all", "queued", "processing", "done", "failed") or not 1 <= page <= 9999999:
        raise HTTPException(422, "Invalid query")
    env = env_of(request)
    conditions, params = [], []
    if status != "all":
        conditions.append("j.state = ?")
        params.append("running" if status == "processing" else status)
    if q.strip():
        pattern = "%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        conditions.append("(j.filename LIKE ? ESCAPE '\\' OR j.id LIKE ? ESCAPE '\\')")
        params.extend([pattern, pattern])
    where = " AND ".join(conditions) or "1=1"
    total = (await d1_first(env, f"SELECT COUNT(*) AS n FROM jobs j WHERE {where}", *params))["n"]
    page_count = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(page, page_count)
    rows = await d1_all(
        env,
        f"SELECT j.*, (SELECT COUNT(*) FROM events e WHERE e.job_id = j.id) AS event_count "
        f"FROM jobs j WHERE {where} ORDER BY j.created_at DESC, j.id DESC LIMIT ? OFFSET ?",
        *params, PAGE_SIZE, (page - 1) * PAGE_SIZE,
    )
    return {
        "items": [
            {
                "id": row["id"], "filename": row["filename"], "sizeBytes": row["size_bytes"],
                "status": "processing" if row["state"] == "running" else row["state"],
                "progress": row["progress"], "verdict": row["verdict"],
                "violationCount": row["event_count"] if row["state"] == "done" else None,
                "createdAt": round(row["created_at"] * 1000),
                "finishedAt": round(row["finished_at"] * 1000) if row["finished_at"] is not None else None,
            }
            for row in rows
        ],
        "total": total, "page": page, "pageSize": PAGE_SIZE, "pageCount": page_count,
    }


# Static path must precede /api/jobs/{job_id}.
@app.get("/api/jobs/activity")
async def ui_activity(request: Request, after: str | None = None):
    env = env_of(request)
    if after is None:
        latest = await d1_first(
            env,
            f"SELECT id, {COMPLETED_AT} AS completed_at FROM jobs WHERE state = 'done' AND finished_at IS NOT NULL"
            " ORDER BY completed_at DESC, id DESC LIMIT 1",
        )
        cursor = f"{latest['completed_at']}.{latest['id']}" if latest else "0."
        return {"items": [], "cursor": cursor, "hasMore": False}
    match = CURSOR.fullmatch(after)
    if not match or int(match[1]) > 2**53 - 1:
        raise HTTPException(400, "Invalid activity cursor")
    timestamp, job_id = int(match[1]), match[2]
    rows = await d1_all(
        env,
        f"SELECT id, filename, finished_at, {COMPLETED_AT} AS completed_at FROM jobs "
        f"WHERE state = 'done' AND ({COMPLETED_AT} > ? OR ({COMPLETED_AT} = ? AND id > ?)) "
        "ORDER BY completed_at, id LIMIT ?",
        timestamp, timestamp, job_id, ACTIVITY_SIZE + 1,
    )
    items = rows[:ACTIVITY_SIZE]
    cursor = f"{items[-1]['completed_at']}.{items[-1]['id']}" if items else after
    return {
        "items": [{"id": r["id"], "filename": r["filename"], "finishedAt": round(r["finished_at"] * 1000)} for r in items],
        "cursor": cursor,
        "hasMore": len(rows) > ACTIVITY_SIZE,
    }


@app.get("/api/jobs/{job_id}")
async def ui_job(job_id: str, request: Request):
    """Interface view: state, stage, queue position, structured rules, evidence URLs."""
    env = env_of(request)
    job = await load_job(env, job_id)
    queue_position = None
    if job["state"] == "queued":
        row = await d1_first(
            env, "SELECT COUNT(*) AS n FROM jobs WHERE state = 'queued' AND created_at < ?", job["created_at"]
        )
        queue_position = row["n"]
    view = {
        "job_id": job_id,
        "state": job["state"],
        "status": "processing" if job["state"] in ("queued", "running") else job["state"],
        "stage": job["stage"],
        "progress": job["progress"],
        "queue_position": queue_position,
        "filename": job["filename"],
        "size_bytes": job["size_bytes"],
        "duration_sec": job["duration_sec"],
        "created_at": job["created_at"],
        "finished_at": job["finished_at"],
        "error": job["error"],
    }
    if job["state"] != "done":
        return view
    result = await load_result(env, job)
    view["verdict"] = result["verdict"]
    view["analysis"] = result.get("analysis", {})
    view["violations"] = [
        {
            **{k: v[k] for k in ("event_id", "event_type", "type", "timecode_sec", "start_sec", "end_sec",
                                 "description", "rules")},
            "evidence_frames": [
                {
                    "timestamp_sec": f["timestamp_sec"],
                    "url": f"/api/jobs/{job_id}/evidence/{f['path'].rsplit('/', 1)[-1]}",
                }
                for f in v["evidence_frames"]
            ],
        }
        for v in result["violations"]
    ]
    return view


@app.get("/api/jobs/{job_id}/evidence/{name}")
async def ui_evidence(job_id: str, name: str, request: Request):
    env = env_of(request)
    job = await load_job(env, job_id)
    if not EVIDENCE_NAME.fullmatch(name):
        raise HTTPException(404, "Evidence frame not found")
    obj = await env.MEDIA.get(f"evidence/{job_id}/{name}")
    if obj is None and job.get("remote_job_id") and job["state"] == "done":
        result = await load_result(env, job)
        permitted = {str(f.get("frame_ref", "")) for f in result.get("analysis", {}).get("frames", [])}
        permitted.update(str(f.get("path", "")).rsplit("/", 1)[-1]
                         for event in result.get("violations", [])
                         for f in event.get("evidence_frames", []))
        if name not in permitted:
            raise HTTPException(404, "Evidence frame not found")
        try:
            content = await remote_client(env).evidence(job["remote_job_id"], name)
            await env.MEDIA.put(f"evidence/{job_id}/{name}", to_js(content))
        except Exception:
            raise HTTPException(502, "Inference evidence temporarily unavailable")
        return Response(content, media_type="image/jpeg")
    if obj is None:
        raise HTTPException(404, "Evidence frame not found")
    return Response((await obj.arrayBuffer()).to_bytes(), media_type="image/jpeg")


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        js_req = request.js_object
        path = urlparse(js_req.url).path.rstrip("/") or "/"
        if js_req.method == "POST" and path in UPLOAD_PATHS:
            return await handle_upload(js_req, self.env, UPLOAD_PATHS[path])
        if js_req.method in ("GET", "HEAD") and (m := VIDEO_PATH.match(path)):
            return await handle_video(js_req, self.env, m.group(1))
        return await asgi.fetch(app, request, self.env, self.ctx)
