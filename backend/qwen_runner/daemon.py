"""Keeps Qwen loaded on the GPU and answers analysis requests over a local
Unix socket, so a job does not pay the ~1 minute model load.

    QWEN_MODEL_DIR=/path/to/snapshot python -m qwen_runner.daemon

Protocol: the client sends one backend request JSON (datathon.qwen.request.v1)
and closes its write side; the daemon answers with one JSON document and
closes. {"op": "info"} returns the model ID and version. Errors come back as
{"error": "..."}. One request at a time; the backend worker runs one job at a time.

Settings (environment):
  QWEN_MODEL_DIR          local Qwen3-VL-8B-Instruct snapshot (required)
  QWEN_LORA_DIR           optional experimental pilot adapter (NF4 + LoRA)
  QWEN_SOCKET             socket path (default /tmp/qwen-runner.sock)
  QWEN_TIME_BUDGET_S      generation budget per request (default 200)
  QWEN_BATCH_SIZE         windows per generate call (default 4)
  QWEN_MAX_WINDOWS        windows per video (default 12)
  QWEN_FRAMES_PER_WINDOW  frames per window (default 6)
  QWEN_WINDOW_S           target window length in seconds (default 8)
  QWEN_IMAGE_EDGE         longest image side given to the model (default 896)
  QWEN_CHECK_WINDOW_S     checklist window length in seconds (default 5)
  QWEN_CHECK_MAX_WINDOWS  checklist windows per video (default 30)
  QWEN_OBSERVATION_PASS   1 also runs the pilot's object-event pass (default 0)
  QWEN_LOG_DIR            optional: keep raw model output per request here
"""

from __future__ import annotations

import json
import logging
import os
import socket
import time
from pathlib import Path

from qwen_runner import checklist
from qwen_runner.model import MODEL_ID, QwenModel
from qwen_runner.windows import build_windows, parse_window_output, system_prompt

log = logging.getLogger("qwen-daemon")
MAX_REQUEST_BYTES = 16 * 1024 * 1024
MAX_EVENTS = 200


def env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


class Analyzer:
    def __init__(self):
        self.model = QwenModel(os.environ["QWEN_MODEL_DIR"], os.environ.get("QWEN_LORA_DIR") or None)
        self.prompt = system_prompt()
        self.budget = float(os.environ.get("QWEN_TIME_BUDGET_S", 200))
        self.batch = env_int("QWEN_BATCH_SIZE", 4)
        self.window_args = dict(max_windows=env_int("QWEN_MAX_WINDOWS", 12),
                                frames_per_window=env_int("QWEN_FRAMES_PER_WINDOW", 6),
                                target_window_s=float(os.environ.get("QWEN_WINDOW_S", 8)),
                                image_max_edge=env_int("QWEN_IMAGE_EDGE", 896))
        self.check_args = dict(max_windows=env_int("QWEN_CHECK_MAX_WINDOWS", 30),
                               frames_per_window=env_int("QWEN_FRAMES_PER_WINDOW", 6),
                               target_window_s=float(os.environ.get("QWEN_CHECK_WINDOW_S", 5)),
                               image_max_edge=env_int("QWEN_IMAGE_EDGE", 896))
        self.observation_pass = os.environ.get("QWEN_OBSERVATION_PASS", "0") == "1"
        self.log_dir = Path(os.environ["QWEN_LOG_DIR"]) if os.environ.get("QWEN_LOG_DIR") else None

    def info(self) -> dict:
        return {"model_id": MODEL_ID, "model_version": self.model.version, "device": self.model.device_name}

    def analyze(self, request: dict) -> dict:
        if request.get("schema_version") != "datathon.qwen.request.v1":
            raise ValueError("Unsupported request schema")
        started = time.monotonic()
        deadline = started + self.budget
        report, rows = [], []
        # Checklist pass first: it is what the violation rules need.
        check_windows = build_windows(request, **self.check_args)
        for first in range(0, len(check_windows), self.batch):
            chunk = check_windows[first:first + self.batch]
            texts = self.model.generate_conversations([checklist.messages_for(w) for w in chunk], deadline)
            for window, raw in zip(chunk, texts):
                row = {"pass": "checklist", "window_id": window["window_id"], "start_s": window["start_s"], "end_s": window["end_s"]}
                if raw is None:
                    row["status"] = "skipped_time_budget"
                else:
                    row["raw"] = raw
                    try:
                        rows.append(checklist.parse(raw, window))
                        row["status"] = "validated"
                    except (ValueError, KeyError, TypeError) as exc:
                        row.update(status="rejected", reason=str(exc))
                report.append(row)
        events = []
        windows = build_windows(request, **self.window_args) if self.observation_pass else []
        for first in range(0, len(windows), self.batch):
            chunk = windows[first:first + self.batch]
            texts = self.model.generate(chunk, self.prompt, deadline)
            for window, raw in zip(chunk, texts):
                row = {"pass": "observation", "window_id": window["window_id"], "start_s": window["start_s"], "end_s": window["end_s"]}
                if raw is None:
                    row["status"] = "skipped_time_budget"
                else:
                    row["raw"] = raw
                    try:
                        found, details = parse_window_output(raw, window)
                        events.extend(found)
                        row.update(status="validated", events=len(found), **details)
                    except (ValueError, KeyError, TypeError) as exc:
                        row.update(status="rejected", reason=str(exc))
                report.append(row)
        log.info("video %s: %d checklist + %d observation windows, %d events, %.1fs; %s",
                 request.get("video_sha256", "?")[:12], len(check_windows), len(windows),
                 len(events), time.monotonic() - started,
                 ", ".join(f"{r['window_id']}={r['status']}" for r in report))
        if self.log_dir:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            name = f"{int(time.time())}-{request.get('video_sha256', 'unknown')[:12]}.json"
            (self.log_dir / name).write_text(json.dumps({"info": self.info(), "windows": report}, ensure_ascii=False, indent=1))
        info = self.info()
        return {"schema_version": "datathon.qwen.v1", "model_id": info["model_id"],
                "model_version": info["model_version"], "events": events[:MAX_EVENTS], "checklist": rows}


def read_all(conn: socket.socket) -> bytes:
    chunks, size = [], 0
    while chunk := conn.recv(1 << 16):
        size += len(chunk)
        if size > MAX_REQUEST_BYTES:
            raise ValueError("Request too large")
        chunks.append(chunk)
    return b"".join(chunks)


def serve(analyzer: Analyzer, path: str) -> None:
    if os.path.exists(path):
        os.unlink(path)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    old = os.umask(0o177)  # socket readable by this user only
    try:
        server.bind(path)
    finally:
        os.umask(old)
    server.listen(4)
    log.info("ready on %s (%s)", path, analyzer.info())
    while True:
        conn, _ = server.accept()
        with conn:
            try:
                request = json.loads(read_all(conn))
                reply = analyzer.info() if request.get("op") == "info" else analyzer.analyze(request)
            except Exception as exc:  # report, keep serving
                log.exception("request failed")
                reply = {"error": f"{type(exc).__name__}: {exc}"[:500]}
            try:
                conn.sendall(json.dumps(reply, allow_nan=False).encode())
            except OSError:
                log.warning("client went away before the reply")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    analyzer = Analyzer()
    serve(analyzer, os.environ.get("QWEN_SOCKET", "/tmp/qwen-runner.sock"))


if __name__ == "__main__":
    main()
