from app.db import connect
from tests.test_api import upload


def seed(settings, count=25):
    conn = connect(settings.db_path)
    conn.executemany(
        "INSERT INTO jobs (id, state, filename, size_bytes, input_path, created_at, stage) "
        "VALUES (?, 'queued', ?, 1000, 'unused.mov', 1792280000, 'queued')",
        [(f"{i:012x}", f"video-{i}.mov") for i in range(count)],
    )
    return conn


def test_history_stable_pagination_literal_search_and_status(env):
    client, _, settings = env()
    conn = seed(settings)
    first = client.get("/api/jobs").json()
    second = client.get("/api/jobs?page=2").json()
    assert (first["total"], first["pageSize"], first["pageCount"]) == (25, 20, 2)
    ids = [row["id"] for row in first["items"] + second["items"]]
    assert ids == [f"{i:012x}" for i in reversed(range(25))]
    assert first["items"][0]["createdAt"] == 1792280000000
    assert first["items"][0]["violationCount"] is None
    assert client.get("/api/jobs?page=999").json()["page"] == 2

    conn.execute("UPDATE jobs SET filename = ?, state = 'running', stage = 'detecting', progress = 15 WHERE id = ?", ("100%_cash.mov", ids[0]))
    for q in ("%", "_", "100%_cash", ids[0]):
        found = client.get("/api/jobs", params={"q": q}).json()
        assert found["total"] == 1
        assert found["items"][0]["id"] == ids[0]
    assert client.get("/api/jobs", params={"q": "' OR 1=1 --"}).json()["total"] == 0
    running = client.get("/api/jobs?status=processing").json()
    assert running["total"] == 1 and running["items"][0]["status"] == "processing"
    assert running["items"][0]["progress"] == 15
    assert client.get("/api/jobs?status=queued").json()["total"] == 24
    empty = client.get("/api/jobs?status=failed&page=99").json()
    assert empty["items"] == [] and empty["page"] == empty["pageCount"] == 1
    conn.close()


def test_history_invalid_queries(env):
    client, _, _ = env()
    for params in ({"page": 0}, {"page": -1}, {"page": "abc"}, {"status": "running"}, {"q": "a" * 121}):
        assert client.get("/api/jobs", params=params).status_code == 422
    for after in ("", "no", "0.' OR 1=1", "9999999999999999."):
        assert client.get("/api/jobs/activity", params={"after": after}).status_code == 400


def test_completion_cursor_skips_history_and_pages_equal_timestamps(env):
    client, _, settings = env()
    conn = seed(settings, count=58)
    assert client.get("/api/jobs/activity").json() == {"items": [], "cursor": "0.", "hasMore": False}
    conn.execute("UPDATE jobs SET state = 'done', finished_at = 1792280001 WHERE id = '000000000039'")
    initial = client.get("/api/jobs/activity").json()
    assert initial["items"] == []
    # Old queued uploads can finish after a newer upload; use completion order.
    conn.execute("UPDATE jobs SET state = 'done', finished_at = 1792280002.123456 WHERE id < '000000000037'")
    conn.execute("UPDATE jobs SET state = 'failed', finished_at = 1792280003 WHERE id = '000000000037'")
    first = client.get("/api/jobs/activity", params={"after": initial["cursor"]}).json()
    second = client.get("/api/jobs/activity", params={"after": first["cursor"]}).json()
    assert len(first["items"]) == 50 and first["hasMore"]
    assert len(second["items"]) == 5 and not second["hasMore"]
    assert [row["id"] for row in first["items"] + second["items"]] == [f"{i:012x}" for i in range(55)]
    unchanged = client.get("/api/jobs/activity", params={"after": second["cursor"]}).json()
    assert unchanged == {"items": [], "cursor": second["cursor"], "hasMore": False}
    # A distinct sub-millisecond completion must not be skipped or repeated.
    conn.execute("UPDATE jobs SET state = 'done', finished_at = 1792280002.123499 WHERE id = '000000000038'")
    later = client.get("/api/jobs/activity", params={"after": second["cursor"]}).json()
    assert [row["id"] for row in later["items"]] == ["000000000038"]
    conn.close()


def test_upload_worker_result_history_and_notification(env, video):
    client, worker, _ = env()
    cursor = client.get("/api/jobs/activity").json()["cursor"]
    job_id = upload(client, video, path="/api/jobs", headers={"X-API-Key": "test-key"}).json()["job_id"]
    queued = client.get(f"/api/jobs/{job_id}").json()
    assert queued["state"] == "queued" and queued["created_at"] > 1700000000
    assert worker.run_once()
    result = client.get(f"/api/jobs/{job_id}").json()
    assert result["analysis"]["is_mock"] and result["analysis"]["limitations"]
    assert result["violations"] == [] and result["verdict"] == "insufficient_evidence"
    assert result["analysis"]["rule_evaluation"]["decision"] == "insufficient_evidence"
    item = client.get("/api/jobs?status=done").json()["items"][0]
    assert item["id"] == job_id and item["violationCount"] == len(result["violations"])
    assert item["createdAt"] == round(result["created_at"] * 1000)
    assert item["finishedAt"] == round(result["finished_at"] * 1000)
    activity = client.get("/api/jobs/activity", params={"after": cursor}).json()
    assert activity["items"] == [{"id": job_id, "filename": "clip.mov", "finishedAt": item["finishedAt"]}]
