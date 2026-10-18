"""Remote analysis worker: runs on the GPU machine and pulls jobs from the
Cloudflare Worker deployment (worker/src/main.py), so the GPU machine needs no
inbound port or tunnel. Only outbound HTTPS.

    ANALYZER_URL=https://datathon26.example.workers.dev ANALYZER_TOKEN=... \
    QWEN_PROVIDER=command QWEN_COMMAND_JSON=[...] python -m app.remote

Each claimed job is copied into the local data dir and run by the ordinary
local Worker (same isolation, timeout and result format), then the result and
the cited evidence frames go back to the Worker.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import socket
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from app import jobs
from app.config import Settings, load_settings
from app.db import transaction
from app.pipeline.providers import capabilities
from app.worker import Worker

log = logging.getLogger("remote")
USER_AGENT = "kassa-control-analyzer/1"
PROGRESS_SECONDS = 5


class Remote:
    """The analyzer endpoints of the Worker, authenticated with a bearer token."""

    def __init__(self, base_url: str, token: str, timeout: float = 60):
        self.base = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def request(self, method: str, path: str, body: bytes | None = None, content_type: str = "application/json"):
        req = urllib.request.Request(self.base + path, data=body, method=method, headers={
            "Authorization": f"Bearer {self.token}", "User-Agent": USER_AGENT,
            **({"Content-Type": content_type} if body is not None else {})})
        return urllib.request.urlopen(req, timeout=self.timeout)

    def json(self, method: str, path: str, payload: dict | None = None) -> dict | None:
        body = None if payload is None else json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
        with self.request(method, path, body) as res:
            data = res.read()
        return json.loads(data) if data else None

    def download(self, path: str, target: Path) -> int:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".part")
        size = 0
        with self.request("GET", path) as res, tmp.open("wb") as out:
            while chunk := res.read(1 << 20):
                out.write(chunk)
                size += len(chunk)
        os.replace(tmp, target)
        return size


class RemoteWorker:
    def __init__(self, settings: Settings, remote: Remote):
        self.settings = settings
        self.remote = remote
        self.local = Worker(settings)
        self.stopping = False
        self.info = {"host": socket.gethostname(), "models": capabilities(settings)}

    def poll_once(self) -> bool:
        job = self.remote.json("POST", "/api/analyzer/claim", self.info)
        if not job:
            return False
        self.run(job["id"], job["filename"], job["input_path"])
        return True

    def run(self, job_id: str, filename: str, input_path: str) -> None:
        local, storage = self.local, self.local.storage
        ext = Path(input_path).suffix.lower()
        try:
            started = time.monotonic()
            video = storage.input_path(job_id, ext)
            size = self.remote.download(f"/api/analyzer/jobs/{job_id}/video", video)
            log.info("job %s: downloaded %.1f MB in %.1fs", job_id, size / 2**20, time.monotonic() - started)
            with transaction(local.conn, immediate=True):
                # The local DB is scratch space: the Worker's D1 is the job of record.
                local.conn.execute("DELETE FROM jobs WHERE id = ? OR state IN ('queued', 'running')", (job_id,))
            jobs.create_job(local.conn, job_id, filename, size, storage.relative(video), 0.0)
        except Exception as exc:
            log.exception("job %s: could not fetch the video", job_id)
            self.report_failure(job_id, f"Analysis worker could not fetch the video: {type(exc).__name__}")
            return

        done = threading.Event()
        threading.Thread(target=self.mirror_progress, args=(job_id, done), daemon=True).start()
        try:
            local.run_once()
        finally:
            done.set()

        row = jobs.get_job(local.conn, job_id)
        if row["state"] == "done":
            try:
                self.upload(job_id, row)
                log.info("job %s: done in %.1fs", job_id, time.monotonic() - started)
            except Exception as exc:
                log.exception("job %s: upload failed", job_id)
                self.report_failure(job_id, f"Analysis finished but the upload failed: {type(exc).__name__}")
        else:
            self.report_failure(job_id, row["error"] or "Analysis failed")

    def mirror_progress(self, job_id: str, done: threading.Event) -> None:
        """Copy the local stage/progress to the Worker; doubles as the heartbeat."""
        while not done.wait(PROGRESS_SECONDS):
            row = jobs.get_job(self.local.conn, job_id)
            if row is None or row["state"] not in ("queued", "running"):
                continue
            try:
                self.remote.json("POST", f"/api/analyzer/jobs/{job_id}/progress",
                                 {"stage": row["stage"] or "running", "progress": int(row["progress"] or 0)})
            except (urllib.error.URLError, OSError) as exc:
                log.warning("job %s: progress update failed: %s", job_id, exc)

    def upload(self, job_id: str, row) -> None:
        storage = self.local.storage
        result = json.loads(storage.resolve(row["result_path"]).read_text(encoding="utf-8"))
        # Only frames someone can look at: violation evidence and frames cited by model events.
        names = {Path(f["path"]).name for v in result["violations"] for f in v["evidence_frames"]}
        names |= {r["frame_ref"] for e in result.get("analysis", {}).get("evidence", []) for r in e["refs"]}
        for name in sorted(names):
            data = (storage.evidence_dir(job_id) / name).read_bytes()
            with self.remote.request("PUT", f"/api/analyzer/jobs/{job_id}/evidence/{name}", data, "image/jpeg"):
                pass
        events = [
            {k: e[k] for k in ("event_id", "event_type", "start_sec", "end_sec", "representative_sec",
                               "confidence", "explanation", "rule_ids", "evidence_paths")}
            for e in self.local.conn.execute("SELECT * FROM events WHERE job_id = ?", (job_id,))
        ]
        self.remote.json("POST", f"/api/analyzer/jobs/{job_id}/complete",
                         {"result": result, "duration_sec": row["duration_sec"], "events": events})

    def report_failure(self, job_id: str, error: str) -> None:
        try:
            self.remote.json("POST", f"/api/analyzer/jobs/{job_id}/fail", {"error": error[:2000]})
        except Exception:
            # The Worker requeues a job whose heartbeat stops, so this is not fatal.
            log.exception("job %s: could not report the failure", job_id)

    def run_forever(self) -> None:
        log.info("remote worker ready: %s -> %s", self.info["host"], self.remote.base)
        while not self.stopping:
            try:
                busy = self.poll_once()
            except (urllib.error.URLError, OSError, ValueError) as exc:
                log.warning("poll failed: %s", exc)
                busy = False
            if not busy:
                time.sleep(self.settings.worker_poll_seconds)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    worker = RemoteWorker(load_settings(), Remote(os.environ["ANALYZER_URL"], os.environ["ANALYZER_TOKEN"]))

    def stop(signum, _frame):
        log.info("signal %s received, stopping after the current job", signum)
        worker.stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    worker.run_forever()


if __name__ == "__main__":
    main()
