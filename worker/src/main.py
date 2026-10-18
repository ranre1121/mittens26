"""FastAPI on a Cloudflare Python Worker: the interface API and the jury API,
with jobs in D1 and media in R2. No analysis runs here: a GPU machine running
`python -m app.remote` (backend/app/remote.py) pulls queued jobs through the
/api/analyzer/* routes, authenticated with the ANALYZER_TOKEN secret, and
posts results back. Until one is connected, jobs stay queued ("processing").

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
ANALYZER_MEDIA = re.compile(r"^/api/analyzer/jobs/([0-9a-f]{12})/(?:video|evidence/(e\d+-[\d.]+\.jpg))$")
# A running job whose analyzer has not reported for this long goes back to the queue.
STALE_SECONDS = 120
MAX_ATTEMPTS = 3
MAX_EVIDENCE_BYTES = 5 * 1024 * 1024


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


def analyzer_ok(env, authorization: str | None) -> bool:
    token = str(getattr(env, "ANALYZER_TOKEN", "") or "")
    return bool(token) and bool(authorization) and secrets.compare_digest(authorization, f"Bearer {token}")


def max_upload_bytes(env) -> int:
    return int(float(getattr(env, "MAX_UPLOAD_MB", "95") or 95) * 1024 * 1024)


def as_bytes(buffer) -> bytes:
    """R2 bodies through the SDK arrive as memoryview; raw JS buffers as proxies."""
    return bytes(buffer) if isinstance(buffer, memoryview) else buffer.to_bytes()


def py(value):
    """The SDK already converts D1 results to Python; plain JS proxies still need to_py()."""
    return value.to_py() if hasattr(value, "to_py") else value


async def d1_first(env, sql: str, *args):
    return py(await env.DB.prepare(sql).bind(*args).first())


async def d1_all(env, sql: str, *args) -> list[dict]:
    res = py(await env.DB.prepare(sql).bind(*args).all())
    return list(res["results"] if isinstance(res, dict) else py(res.results))


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

    limit = max_upload_bytes(env)
    declared = int(js_req.headers.get("content-length") or 0)
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
    return js_json({"job_id": job_id})


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


async def handle_analyzer_media(js_req, env, job_id: str, evidence_name: str | None):
    """Video download and evidence upload for the analyzer, on the JavaScript
    side for the same memory reason as uploads."""
    if not analyzer_ok(env, js_req.headers.get("authorization")):
        return await reject(js_req, {"detail": "Analyzer token required"}, 401)
    job = await d1_first(env, "SELECT state, input_path FROM jobs WHERE id = ?", job_id)
    if job is None:
        return await reject(js_req, {"detail": "Job not found"}, 404)
    if job["state"] != "running":
        return await reject(js_req, {"detail": "Job is not running"}, 409)
    if evidence_name is None:
        if js_req.method != "GET":
            return await reject(js_req, {"detail": "Method not allowed"}, 405)
        obj = await env.MEDIA.get(job["input_path"])
        if obj is None:
            return js_json({"detail": "Video not found"}, 404)
        headers = [["content-type", "application/octet-stream"], ["content-length", str(int(obj.size))]]
        return JsResponse.new(obj.body, status=200, headers=JsHeaders.new(to_js(headers)))
    if js_req.method != "PUT":
        return await reject(js_req, {"detail": "Method not allowed"}, 405)
    if int(js_req.headers.get("content-length") or 0) > MAX_EVIDENCE_BYTES:
        return await reject(js_req, {"detail": "Evidence frame too large"}, 413)
    data = await js_req.arrayBuffer()
    if data.byteLength > MAX_EVIDENCE_BYTES:
        return js_json({"detail": "Evidence frame too large"}, 413)
    await env.MEDIA.put(f"evidence/{job_id}/{evidence_name}", data,
                        js_obj({"httpMetadata": js_obj({"contentType": "image/jpeg"})}))
    return js_json({"ok": True})


# ---------- FastAPI: everything else ----------

app = FastAPI(title="Контроль кассы", version="0.1.0")


def env_of(request: Request):
    return request.scope["env"]


async def load_job(env, job_id: str) -> dict:
    job = await d1_first(env, "SELECT * FROM jobs WHERE id = ?", job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    return job


async def load_result(env, job: dict) -> dict:
    obj = await env.MEDIA.get(job["result_path"])
    if obj is None:
        raise HTTPException(500, "Result is missing for a finished job")
    return json.loads(await obj.text())


async def read_bytes(env, key: str) -> bytes:
    obj = await env.MEDIA.get(key)
    if obj is None:
        raise HTTPException(500, f"Missing object {key}")
    return as_bytes(await obj.arrayBuffer())


@app.get("/api/health")
async def health(request: Request):
    seen = await d1_first(env_of(request), "SELECT seen_at FROM analyzer_status WHERE id = 1")
    connected = seen is not None and time.time() - seen["seen_at"] < 90
    return {"ok": True, "analysis": "connected" if connected else "not connected"}


# ---------- analyzer: the GPU machine pulls jobs from here ----------


def require_analyzer(request: Request):
    if not analyzer_ok(env_of(request), request.headers.get("authorization")):
        raise HTTPException(401, "Analyzer token required")
    return env_of(request)


@app.post("/api/analyzer/claim")
async def analyzer_claim(request: Request):
    """Hand the oldest queued job to the analyzer, after requeueing jobs whose
    analyzer went silent (bounded by MAX_ATTEMPTS, like the local worker)."""
    env = require_analyzer(request)
    now, stale = time.time(), time.time() - STALE_SECONDS
    await env.DB.prepare(
        "UPDATE jobs SET state = 'failed', stage = 'failed', finished_at = ?,"
        " error = 'Analysis was interrupted ' || attempts || ' times; giving up.'"
        " WHERE state = 'running' AND COALESCE(heartbeat_at, started_at) < ? AND attempts >= ?"
    ).bind(now, stale, MAX_ATTEMPTS).run()
    await env.DB.prepare(
        "UPDATE jobs SET state = 'queued', stage = 'requeued', progress = 0"
        " WHERE state = 'running' AND COALESCE(heartbeat_at, started_at) < ?"
    ).bind(stale).run()
    info = json.dumps(await request.json(), ensure_ascii=False)[:2000] if await request.body() else "{}"
    # Last-seen for /api/health; written at most every 30 s.
    await env.DB.prepare(
        "INSERT INTO analyzer_status (id, seen_at, info) VALUES (1, ?, ?)"
        " ON CONFLICT (id) DO UPDATE SET seen_at = excluded.seen_at, info = excluded.info"
        " WHERE analyzer_status.seen_at < ?"
    ).bind(now, info, now - 30).run()
    job = await d1_first(
        env,
        "UPDATE jobs SET state = 'running', started_at = ?, heartbeat_at = ?, attempts = attempts + 1,"
        " stage = 'claimed', progress = 0, error = NULL"
        " WHERE id = (SELECT id FROM jobs WHERE state = 'queued' ORDER BY created_at LIMIT 1) AND state = 'queued'"
        " RETURNING id, filename, input_path, attempts",
        now, now,
    )
    return job if job else Response(status_code=204)


@app.post("/api/analyzer/jobs/{job_id}/progress")
async def analyzer_progress(job_id: str, request: Request):
    env = require_analyzer(request)
    body = await request.json()
    stage, progress = str(body.get("stage", "running"))[:40], int(body.get("progress", 0))
    row = await d1_first(
        env,
        "UPDATE jobs SET stage = ?, progress = ?, heartbeat_at = ? WHERE id = ? AND state = 'running' RETURNING id",
        stage, max(0, min(99, progress)), time.time(), job_id,
    )
    if row is None:
        raise HTTPException(409, "Job is not running")
    return {"ok": True}


@app.post("/api/analyzer/jobs/{job_id}/complete")
async def analyzer_complete(job_id: str, request: Request):
    """Store the result (same format as backend/app/jobs.py) in R2, then mark
    the job done. Evidence frames were uploaded before this call."""
    env = require_analyzer(request)
    job = await load_job(env, job_id)
    if job["state"] != "running":
        raise HTTPException(409, "Job is not running")
    body = await request.json()
    result, events = body.get("result"), body.get("events", [])
    if not isinstance(result, dict) or not isinstance(result.get("violations"), list) or not isinstance(events, list):
        raise HTTPException(422, "Invalid result")
    prefix = f"evidence/{job_id}/"
    for v in result["violations"]:
        for f in v.get("evidence_frames", []):
            name = str(f.get("path", ""))[len(prefix):]
            if not str(f.get("path", "")).startswith(prefix) or not EVIDENCE_NAME.match(name):
                raise HTTPException(422, "Invalid evidence path")
    result["job_id"] = job_id
    result_key = f"results/{job_id}/result.json"
    await env.MEDIA.put(result_key, json.dumps(result, ensure_ascii=False),
                        js_obj({"httpMetadata": js_obj({"contentType": "application/json"})}))
    now = time.time()
    statements = [env.DB.prepare("DELETE FROM events WHERE job_id = ?").bind(job_id)]
    for e in events[:500]:
        statements.append(env.DB.prepare(
            "INSERT INTO events (job_id, event_id, event_type, start_sec, end_sec, representative_sec,"
            " confidence, explanation, rule_ids, evidence_paths) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        ).bind(job_id, str(e["event_id"]), str(e["event_type"]), float(e["start_sec"]), float(e["end_sec"]),
               float(e["representative_sec"]), float(e["confidence"]) if e.get("confidence") is not None else None,
               str(e["explanation"]), str(e["rule_ids"]), str(e["evidence_paths"])))
    duration = body.get("duration_sec")
    statements.append(env.DB.prepare(
        "UPDATE jobs SET state = 'done', verdict = NULL, result_path = ?, finished_at = ?, stage = 'done',"
        " progress = 100, duration_sec = ? WHERE id = ? AND state = 'running'"
    ).bind(result_key, now, float(duration) if duration is not None else None, job_id))
    await env.DB.batch(statements)
    return {"ok": True}


@app.post("/api/analyzer/jobs/{job_id}/fail")
async def analyzer_fail(job_id: str, request: Request):
    env = require_analyzer(request)
    body = await request.json()
    row = await d1_first(
        env,
        "UPDATE jobs SET state = 'failed', error = ?, finished_at = ?, stage = 'failed'"
        " WHERE id = ? AND state = 'running' RETURNING id",
        str(body.get("error") or "Analysis failed")[:2000], time.time(), job_id,
    )
    if row is None:
        raise HTTPException(409, "Job is not running")
    return {"ok": True}


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
    return {"job_id": job_id, "status": "done", "verdict": result["verdict"], "violations": violations}


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
    await load_job(env, job_id)
    if not EVIDENCE_NAME.match(name):
        raise HTTPException(404, "Evidence frame not found")
    obj = await env.MEDIA.get(f"evidence/{job_id}/{name}")
    if obj is None:
        raise HTTPException(404, "Evidence frame not found")
    return Response(as_bytes(await obj.arrayBuffer()), media_type="image/jpeg")


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        js_req = request.js_object
        path = urlparse(js_req.url).path.rstrip("/") or "/"
        if js_req.method == "POST" and path in UPLOAD_PATHS:
            return await handle_upload(js_req, self.env, UPLOAD_PATHS[path])
        if js_req.method in ("GET", "HEAD") and (m := VIDEO_PATH.match(path)):
            return await handle_video(js_req, self.env, m.group(1))
        if m := ANALYZER_MEDIA.match(path):
            return await handle_analyzer_media(js_req, self.env, m.group(1), m.group(2))
        return await asgi.fetch(app, request, self.env, self.ctx)
