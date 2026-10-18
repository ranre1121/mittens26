"""qwen_runner without a GPU: window building, output validation and the
daemon/client round trip through the real QwenProvider, with a stand-in model."""

import json
import os
import socket
import tempfile
import sys
import threading

import pytest

from app.pipeline.providers import QwenProvider
from qwen_runner import daemon, windows
from tests.test_api import upload

MODEL_VERSION = "test-rev+prompt-v2"


def request_for(tmp_path, seconds=20, fps=2):
    from PIL import Image
    frames = []
    for i in range(int(seconds * fps)):
        ts = i / fps
        path = tmp_path / f"e0-{ts:08.3f}.jpg"
        Image.new("RGB", (64, 48), (i * 5 % 255, 80, 120)).save(path)
        frames.append({"frame_ref": path.name, "timestamp_s": ts, "path": str(path), "detections": []})
    return {"schema_version": "datathon.qwen.request.v1", "video_path": "x", "video_sha256": "0" * 64,
            "duration_sec": float(seconds), "frames": frames, "equipment_context_enabled": False}


def event(window, event_type="manual_cash_handling", refs=None, **overrides):
    refs = refs or [window["frames"][0]["frame_ref"], window["frames"][-1]["frame_ref"]]
    times = [f["timestamp_s"] for f in window["frames"] if f["frame_ref"] in refs]
    row = dict(event_type=event_type, start_s=min(times), end_s=max(times), object_ids=["view-1"],
               evidence_frame_refs=refs, observed_facts=["hands move notes"], visibility_limits=["sparse samples"],
               assessment="candidate", confidence=0.6, identity_status="track_hypothesis",
               completion_status="unknown", completion_cue="", full_lot_observed=False)
    row.update(overrides)
    return row


def answer(window, events):
    return json.dumps({"schema_version": "datathon-qwen-events-v1", "window_id": window["window_id"],
                       "coverage_status": "partial", "events": events, "summary": "s", "limitations": ["l"]})


def test_windows_cover_the_video_within_bounds(tmp_path):
    rows = windows.build_windows(request_for(tmp_path, seconds=100), max_windows=12, frames_per_window=6)
    assert len(rows) == 12
    assert rows[0]["start_s"] == 0 and rows[-1]["end_s"] == 99.5
    for w in rows:
        assert 1 <= len(w["frames"]) <= 6
        assert all(f["objects"][0]["display_id"] == "view-1" for f in w["frames"])
    short = windows.build_windows(request_for(tmp_path, seconds=10))
    assert len(short) == 2


def test_valid_events_kept_invalid_dropped_counting_downgraded(tmp_path):
    window = windows.build_windows(request_for(tmp_path))[0]
    one = [window["frames"][0]["frame_ref"]]
    raw = answer(window, [
        event(window),
        event(window, "counting_activity"),                    # no cash pile detected → unknown_event
        event(window, refs=one),                               # temporal claim from one frame
        event(window, object_ids=["cash_stack-9"]),            # unseen object ID
        event(window, start_s=0.123),                          # span not on cited frames
    ])
    found, report = windows.parse_window_output(raw, window)
    assert [e["event_type"] for e in found] == ["manual_cash_handling", "unknown_event"]
    assert found[1]["data"]["source_event_type"] == "counting_activity"
    assert len(report["dropped"]) == 3
    with pytest.raises(ValueError):
        windows.parse_window_output("```json\n{}\n```", window)


def test_prompt_is_the_revised_v2():
    assert windows.system_prompt().startswith("You review bounded visual evidence")
    assert "completion_cue must be a string" in windows.system_prompt()


def checklist_answer(window, **true_items):
    from qwen_runner.checklist import ITEMS
    ref = window["frames"][0]["frame_ref"]
    return json.dumps({"window_id": window["window_id"], "items": {
        k: {"value": k in true_items, "frame_ref": ref if k in true_items else None, "note": true_items.get(k, "")}
        for k in ITEMS}})


class FakeModel:
    """Answers each observation window with one manual_cash_handling event and
    each checklist window with an open safe."""
    version = MODEL_VERSION
    device_name = "fake"

    def __init__(self):
        self.batches = []

    def generate(self, chunk, prompt, deadline):
        self.batches.append(len(chunk))
        return [answer(w, [event(w)]) for w in chunk]

    def generate_conversations(self, conversations, deadline):
        out = []
        for conv in conversations:
            window = json.loads(conv[1]["content"][-1]["text"])
            frames = [json.loads(b["text"][6:]) for b in conv[1]["content"] if b["type"] == "text" and b["text"].startswith("FRAME ")]
            out.append(checklist_answer({"window_id": window["window_id"], "frames": frames}, safe_open="Дверца сейфа открыта"))
        return out


@pytest.fixture
def fake_daemon(tmp_path, monkeypatch):
    analyzer = daemon.Analyzer.__new__(daemon.Analyzer)
    analyzer.model, analyzer.prompt, analyzer.budget, analyzer.batch = FakeModel(), "p", 60, 4
    analyzer.window_args = dict(max_windows=12, frames_per_window=6, target_window_s=8, image_max_edge=448)
    analyzer.check_args = dict(max_windows=30, frames_per_window=6, target_window_s=5, image_max_edge=448)
    analyzer.observation_pass = True
    analyzer.log_dir = tmp_path / "qwen-log"
    monkeypatch.setattr(daemon, "MODEL_ID", "Qwen/Qwen3-VL-8B-Instruct")
    # macOS limits socket paths to ~104 bytes; pytest's tmp_path is longer.
    path = os.path.join(tempfile.mkdtemp(prefix="q", dir="/tmp"), "q.sock")
    threading.Thread(target=daemon.serve, args=(analyzer, path), daemon=True).start()
    for _ in range(100):
        try:
            with socket.socket(socket.AF_UNIX) as probe:
                probe.connect(path)
            break
        except OSError:
            threading.Event().wait(0.02)
    monkeypatch.setenv("QWEN_SOCKET", path)
    return analyzer


def qwen_settings(make_settings, **kwargs):
    return make_settings(qwen_provider="command", qwen_command=(sys.executable, "-m", "qwen_runner.client"),
                         qwen_model_id="Qwen/Qwen3-VL-8B-Instruct", qwen_model_version=MODEL_VERSION, **kwargs)


def test_client_satisfies_the_provider_contract(fake_daemon, make_settings, tmp_path):
    response = QwenProvider(qwen_settings(make_settings)).infer(request_for(tmp_path, seconds=30))
    assert not response["is_mock"] and len(response["events"]) == 4
    assert fake_daemon.model.batches == [4]
    assert len(response["checklist"]) == 6 and response["checklist"][0]["items"]["safe_open"]["value"] is True
    assert (tmp_path / "qwen-log").exists()


def test_worker_job_with_runner(fake_daemon, env, video):
    client, worker, _ = env(qwen_provider="command", qwen_command=(sys.executable, "-m", "qwen_runner.client"),
                            qwen_model_id="Qwen/Qwen3-VL-8B-Instruct", qwen_model_version=MODEL_VERSION)
    job_id = upload(client, video).json()["job_id"]
    assert worker.run_once()
    data = client.get(f"/api/v1/jobs/{job_id}").json()
    assert data["status"] == "done", data
    assert data["analysis"]["events"][0]["event_type"] == "manual_cash_handling"
    # The open safe persists across windows, so the rules report it with evidence.
    assert data["verdict"] == "violation"
    found = data["violations"][0]
    assert found["type"] == "Сейф оставлен открытым" and "п. 20" in found["description"]
    assert 1 <= len(found["evidence_frames"]) <= 3 and found["evidence_frames"][0]["image_base64"]


def test_daemon_down_fails_the_job(env, video, monkeypatch, tmp_path):
    monkeypatch.setenv("QWEN_SOCKET", str(tmp_path / "missing.sock"))
    client, worker, _ = env(qwen_provider="command", qwen_command=(sys.executable, "-m", "qwen_runner.client"),
                            qwen_model_id="Qwen/Qwen3-VL-8B-Instruct", qwen_model_version=MODEL_VERSION)
    job_id = upload(client, video).json()["job_id"]
    worker.run_once()
    assert client.get(f"/api/v1/jobs/{job_id}").json()["status"] == "failed"
