"""Analysis worker: a separate process that claims queued jobs and runs the
pipeline, one job at a time. Models load in an isolated job process.

    python -m app.worker
"""

from __future__ import annotations

import logging
import fcntl
import multiprocessing
import os
from contextlib import contextmanager
import signal
import sqlite3
import time

from app import jobs
from app.config import Settings, load_settings
from app.db import connect, init_db
from app.pipeline import Pipeline, Stage
from app.pipeline.detectors import build_detector
from app.rules import Rulebook
from app.storage import Storage
from app.video import InvalidVideo

log = logging.getLogger("worker")


class Worker:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.storage = Storage(settings.data_dir)
        self.storage.ensure()
        init_db(settings.db_path)
        self.conn: sqlite3.Connection = connect(settings.db_path)
        rulebook = Rulebook(settings.rules_dir, settings.allow_unverified_rules)
        self.detector = build_detector(settings)
        self.pipeline = Pipeline(self.detector, rulebook, settings.sample_fps)
        from app.pipeline.providers import capabilities
        self.meta = {
            "models": capabilities(settings),
            "detector": self.detector.name,
            "sample_fps": settings.sample_fps,
            "vocabulary_version": rulebook.vocabulary_version,
            "registry_version": rulebook.registry_version,
        }
        self.stopping = False

    def recover(self) -> None:
        requeued, failed = jobs.recover_interrupted(self.conn, self.settings.max_attempts)
        if requeued or failed:
            log.info("restart recovery: %d requeued, %d failed", requeued, failed)

    @contextmanager
    def exclusive(self):
        with (self.settings.data_dir / "worker.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError("Another inference worker owns this data directory")
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def heartbeat(self):
        self.storage.write_json_atomic(self.settings.data_dir / "worker-status.json",
            {"timestamp": time.time(), "pid": os.getpid(), "models": self.meta["models"]})

    def run_once(self) -> bool:
        with self.exclusive():
            self.heartbeat()
            job = jobs.claim_next(self.conn)
            if job is None:
                return False
            process = multiprocessing.get_context("spawn").Process(target=execute_job, args=(self.settings, job["id"], os.getpid()))
            process.start()
            deadline = time.monotonic() + self.settings.job_timeout_seconds
            while process.is_alive() and time.monotonic() < deadline:
                process.join(timeout=min(1, max(.01, deadline - time.monotonic())))
                self.heartbeat()
            if process.is_alive():
                # Child has its own session; kill its configured provider descendants too.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.kill()
                process.join()
                self.storage.clear_attempt(job["id"])
                jobs.fail(self.conn, job["id"], "Analysis timed out")
            elif process.exitcode:
                jobs.fail(self.conn, job["id"], "Inference process exited unexpectedly")
            return True

    def process_job(self, job) -> bool:
        """Process one queued job. Returns False when the queue was empty."""
        job_id = job["id"]
        log.info("job %s: attempt %d, %s", job_id, job["attempts"], job["filename"])
        started = time.monotonic()
        self.storage.clear_attempt(job_id)
        try:
            info, findings = self.pipeline.analyze(
                self.storage.resolve(job["input_path"]),
                self.storage.evidence_dir(job_id),
                on_stage=lambda stage, progress: jobs.set_stage(self.conn, job_id, stage.value, progress),
            )
            jobs.set_stage(self.conn, job_id, Stage.SAVING.value, 95)
            meta = {**self.meta, "analysis": self.detector.last_analysis}
            jobs.complete(self.conn, self.storage, job_id, info, findings, meta)
            log.info(
                "job %s: done, %d finding(s), %.1fs", job_id, len(findings), time.monotonic() - started
            )
        except InvalidVideo as exc:
            jobs.fail(self.conn, job_id, f"Invalid video: {exc}")
            log.warning("job %s: invalid video: %s", job_id, exc)
        except Exception as exc:  # a processing failure is a failed job, never no_violation
            jobs.fail(self.conn, job_id, f"Analysis failed: {type(exc).__name__}")
            log.exception("job %s: failed", job_id)
        return True

    def run_forever(self) -> None:
        with self.exclusive():
            self.recover()
        log.info("worker ready (%s)", self.detector.name)
        while not self.stopping:
            if not self.run_once():
                time.sleep(self.settings.worker_poll_seconds)


def execute_job(settings, job_id, parent_pid):
    os.setsid()
    # A hard-killed coordinator must not leave an inference process racing recovery.
    import threading
    def watch_parent():
        while True:
            time.sleep(.5)
            if os.getppid() != parent_pid:
                os.killpg(os.getpid(), signal.SIGKILL)
    threading.Thread(target=watch_parent, daemon=True).start()
    worker = Worker(settings)
    try:
        worker.process_job(jobs.get_job(worker.conn, job_id))
    finally:
        worker.conn.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    worker = Worker(load_settings())

    def stop(signum, _frame):
        # Finish the current job, then exit. If the process is killed instead,
        # the job is requeued on the next start (bounded by MAX_ATTEMPTS).
        log.info("signal %s received, stopping after the current job", signum)
        worker.stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    worker.run_forever()


if __name__ == "__main__":
    main()

