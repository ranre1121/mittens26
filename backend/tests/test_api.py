from __future__ import annotations

import base64
import io
import sqlite3

from PIL import Image

from app import jobs
from app.db import connect
from tests.conftest import API_KEY

KEY = {"X-API-Key": API_KEY}


def upload(client, video, path="/api/v1/analyze", headers=KEY, name="clip.mov"):
    with open(video, "rb") as fh:
        return client.post(path, headers=headers, files={"file": (name, fh, "video/quicktime")})


def test_rejects_missing_and_wrong_key(env, video):
    client, _, _ = env()
    assert upload(client, video, headers={}).status_code == 401
    assert upload(client, video, headers={"X-API-Key": "nope"}).status_code == 401
    assert client.get("/api/v1/jobs/abc").status_code == 401
    assert client.get("/api/v1/jobs/abc", headers={"X-API-Key": "nope"}).status_code == 401


def test_rejects_non_video_and_wrong_extension(env, tmp_path, video):
    client, _, settings = env()
    junk = tmp_path / "junk.mov"
    junk.write_bytes(b"not a video at all" * 100)
    res = upload(client, junk)
    assert res.status_code == 400 and "readable video" in res.json()["detail"]
    assert upload(client, video, name="clip.jpg").status_code == 400
    # Rejected uploads leave nothing behind and create no job.
    assert not any((settings.data_dir / "tmp").iterdir())
    assert not any((settings.data_dir / "uploads").iterdir())


def test_no_violation_flow(env, video):
    client, worker, _ = env()
    res = upload(client, video)
    assert res.status_code == 200
    job_id = res.json()["job_id"]
    assert client.get(f"/api/v1/jobs/{job_id}", headers=KEY).json() == {
        "job_id": job_id,
        "status": "processing",
    }

    assert worker.run_once() is True
    assert worker.run_once() is False  # queue is empty

    body = client.get(f"/api/v1/jobs/{job_id}", headers=KEY).json()
    assert body == {"job_id": job_id, "status": "done", "verdict": "no_violation", "violations": []}


def test_violation_flow_matches_jury_schema(env, video):
    client, worker, _ = env(stub_mode="demo", allow_unverified_rules=True)
    job_id = upload(client, video).json()["job_id"]
    worker.run_once()

    body = client.get(f"/api/v1/jobs/{job_id}", headers=KEY).json()
    assert set(body) == {"job_id", "status", "verdict", "violations"}
    assert body["status"] == "done" and body["verdict"] == "violation"
    [v] = body["violations"]
    assert set(v) == {"type", "timecode_sec", "description", "evidence_frames"}
    assert 0 <= v["timecode_sec"] <= 4.0
    assert "Норматив:" in v["description"] and "adilet.zan.kz" in v["description"]
    assert 1 <= len(v["evidence_frames"]) <= 3
    for frame in v["evidence_frames"]:
        assert set(frame) == {"timestamp_sec", "image_base64"}
        img = Image.open(io.BytesIO(base64.b64decode(frame["image_base64"])))
        assert img.format == "JPEG" and img.size == (160, 120)


def test_unverified_rules_are_not_cited_by_default(env, video):
    client, worker, _ = env(stub_mode="demo")
    job_id = upload(client, video).json()["job_id"]
    worker.run_once()
    [v] = client.get(f"/api/v1/jobs/{job_id}", headers=KEY).json()["violations"]
    assert "adilet" not in v["description"] and "ожидает проверки" in v["description"]


def test_interface_endpoints(env, video):
    client, worker, _ = env(stub_mode="demo", allow_unverified_rules=True)
    job_id = upload(client, video, path="/api/jobs", headers={}).json()["job_id"]

    queued = client.get(f"/api/jobs/{job_id}").json()
    assert queued["state"] == "queued" and queued["status"] == "processing"
    assert queued["queue_position"] == 0

    worker.run_once()
    done = client.get(f"/api/jobs/{job_id}").json()
    assert done["state"] == "done" and done["verdict"] == "violation"
    [v] = done["violations"]
    assert v["rules"] and v["rules"][0]["url"].startswith("https://")
    frame_url = v["evidence_frames"][0]["url"]
    img = client.get(frame_url)
    assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg"

    full = client.get(f"/api/jobs/{job_id}/video")
    assert full.status_code == 200
    part = client.get(f"/api/jobs/{job_id}/video", headers={"Range": "bytes=0-99"})
    assert part.status_code == 206 and len(part.content) == 100

    assert client.get(f"/api/jobs/{job_id}/evidence/../../app.sqlite3").status_code == 404
    assert client.get("/api/jobs/doesnotexist").status_code == 404


def test_processing_failure_is_failed_not_no_violation(env, video):
    client, worker, settings = env()
    job_id = upload(client, video).json()["job_id"]
    # Corrupt the stored input after upload so analysis fails.
    conn = connect(settings.db_path)
    path = settings.data_dir / jobs.get_job(conn, job_id)["input_path"]
    path.write_bytes(b"garbage")
    worker.run_once()
    body = client.get(f"/api/v1/jobs/{job_id}", headers=KEY).json()
    assert body["status"] == "failed" and "verdict" not in body


def test_restart_recovery_requeues_then_gives_up(env, video):
    client, worker, settings = env()
    job_id = upload(client, video).json()["job_id"]
    conn: sqlite3.Connection = connect(settings.db_path)

    # Simulate a crash mid-analysis: claimed but never finished.
    jobs.claim_next(conn)
    assert jobs.recover_interrupted(conn, settings.max_attempts) == (1, 0)
    assert jobs.get_job(conn, job_id)["state"] == "queued"

    conn.execute("UPDATE jobs SET state = 'running', attempts = ? WHERE id = ?", (settings.max_attempts, job_id))
    assert jobs.recover_interrupted(conn, settings.max_attempts) == (0, 1)
    job = jobs.get_job(conn, job_id)
    assert job["state"] == "failed" and "interrupted" in job["error"]


def test_retry_replaces_previous_attempt_artifacts(env, video):
    client, worker, settings = env(stub_mode="demo")
    job_id = upload(client, video).json()["job_id"]
    stale = settings.data_dir / "evidence" / job_id / "e9-0001.000.jpg"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"old attempt")
    worker.run_once()
    assert not stale.exists()
    conn = connect(settings.db_path)
    assert conn.execute("SELECT COUNT(*) FROM events WHERE job_id = ?", (job_id,)).fetchone()[0] == 1
