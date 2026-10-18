from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import sys

from PIL import Image
import pytest

from app.pipeline import qwen_runtime as runtime
from app.pipeline.qwen_contract import SYSTEM, strict_json, validate_output


def settings(tmp_path, **overrides):
    return SimpleNamespace(data_dir=tmp_path, **overrides)


def request(tmp_path, times=(0.0, 1.0, 2.0)):
    rows = []
    for n, ts in enumerate(times):
        path = tmp_path / f"{n}.jpg"
        Image.new("RGB", (32, 24), color=(n * 10, 0, 0)).save(path)
        rows.append(dict(frame_ref=path.name, path=str(path), timestamp_s=ts,
            objects=[dict(display_id="cash-1", track_id="track-1", class_name="cash_stack",
                          box=[.1, .1, .5, .5], score=.8)]))
    return dict(schema_version="datathon.qwen.request.v1", duration_sec=times[-1] + 1,
                video_sha256="a" * 64, frames=rows)


def response(window, *, events=True):
    first, last = window["frames"][0], window["frames"][-1]
    event = dict(event_type="manual_cash_handling", start_s=first["timestamp_s"],
        end_s=last["timestamp_s"], object_ids=["cash-1"],
        evidence_frame_refs=[first["frame_ref"], last["frame_ref"]],
        observed_facts=["A pile moves between the cited images"],
        visibility_limits=["Sparse samples; physical identity remains unknown"], assessment="candidate",
        confidence=.5, identity_status="track_hypothesis", completion_status="unknown",
        completion_cue="", full_lot_observed=False)
    return dict(schema_version="datathon-qwen-events-v1", window_id=window["window_id"],
                coverage_status="partial", summary="Unreviewed sampled observation",
                events=[event] if events else [], limitations=["Partial visual coverage"])


class FakeBackend:
    actual_inference = True
    metadata = {"test_double": True, "model_id": runtime.MODEL_ID, "model_revision": runtime.REVISION}

    def __init__(self, events=True):
        self.events = events
        self.received = []

    def generate(self, window):
        self.received.append(window)
        return json.dumps(response(window, events=self.events))


def test_revised_prompt_is_exact_verified_pilot():
    assert hashlib.sha256(SYSTEM.encode()).hexdigest() == "19700bb4f1f521e499b83848cdc0d0b21d92636cecffcbd08dd055b87da64500"


def test_runtime_projects_candidates_without_promoting_legal_facts(tmp_path):
    result = runtime.infer(request(tmp_path), settings(tmp_path), backend=FakeBackend())
    assert result["is_mock"] is False
    assert result["events"][0]["event_type"] == "manual_cash_handling"
    assert result["events"][0]["entity_ids"] == ["cash-1"]
    assert result["events"][0]["frame_refs"] == ["0.jpg", "2.jpg"]
    assert result["windows"][0]["output"]["events"][0]["assessment"] == "candidate"
    assert result["provenance"]["semantic_accuracy_validated"] is False
    assert result["provenance"]["coverage"]["coverage_status"] == "partial"


def test_empty_events_remain_insufficient_evidence(tmp_path):
    result = runtime.infer(request(tmp_path), settings(tmp_path), backend=FakeBackend(events=False))
    assert result["events"] == []
    assert any("insufficient evidence" in item for item in result["limitations"])
    assert result["provenance"]["coverage"]["coverage_status"] == "partial"


def test_detections_get_frame_local_identity_equipment_excluded(tmp_path):
    source = request(tmp_path)
    for row in source["frames"]:
        row["detections"] = [dict(class_name="person", box=[.1, .1, .9, .9], score=.8),
                             dict(class_name="laptop", box=[.1, .1, .5, .5], score=.9)]
        del row["objects"]
    windows, _ = runtime.build_windows(source, settings(tmp_path))
    objects = [f["objects"][0] for f in windows[0]["frames"]]
    assert len({o["display_id"] for o in objects}) == 3
    assert all(o["identity_scope"] == "single_frame_detection_not_temporal_track" for o in objects)
    assert all(len(f["objects"]) == 1 for f in windows[0]["frames"])


def test_windows_are_bounded_and_omitted_coverage_reported(tmp_path):
    source = request(tmp_path, tuple(float(n) for n in range(20)))
    cfg = settings(tmp_path, qwen_max_windows=1, qwen_max_frames_per_window=3)
    windows, coverage = runtime.build_windows(source, cfg)
    assert len(windows) == 1
    assert len(windows[0]["frames"]) == 3
    assert coverage["omitted_frames"] == 17
    assert coverage["window_limit_reached"] is True
    result = runtime.infer(source, cfg, backend=FakeBackend(events=False))
    assert any("omitted 17" in row for row in result["limitations"])


@pytest.mark.parametrize("field,value", [
    ("qwen_max_frames_per_window", 9), ("qwen_max_frames_per_window", True),
    ("qwen_max_windows", 0), ("qwen_window_seconds", float("inf")),
])
def test_window_limits_fail_closed(tmp_path, field, value):
    with pytest.raises(ValueError):
        runtime.build_windows(request(tmp_path), settings(tmp_path, **{field: value}))


def test_private_frame_path_cannot_escape_data_root(tmp_path):
    private = tmp_path / "private"
    private.mkdir()
    with pytest.raises(ValueError, match="escapes private"):
        runtime.build_windows(request(tmp_path), settings(private))


def test_unordered_or_duplicate_source_timestamps_rejected(tmp_path):
    with pytest.raises(ValueError, match="increasing"):
        runtime.build_windows(request(tmp_path, (0., 0.)), settings(tmp_path))


@pytest.mark.parametrize("mutation", [
    lambda r: r["events"][0].update(assessment="confirmed"),
    lambda r: r["events"][0].update(full_lot_observed=True),
    lambda r: r["events"][0].update(object_ids=["invented-object"]),
    lambda r: r["events"][0].update(evidence_frame_refs=["invented-frame"]),
    lambda r: r["events"][0].update(start_s=.1),
    lambda r: r["events"][0].update(completion_status="candidate_explicit_evidence", completion_cue="tray emptied"),
    lambda r: r["events"][0].pop("full_lot_observed"),
    lambda r: r.update(coverage_status="complete"),
])
def test_strict_pilot_validator_rejects_unproven_or_unsupported_claims(tmp_path, mutation):
    window = runtime.build_windows(request(tmp_path), settings(tmp_path))[0][0]
    value = response(window)
    mutation(value)
    with pytest.raises((ValueError, TypeError)):
        validate_output(value, window)


def test_temporal_claims_need_two_distinct_source_times(tmp_path):
    window = runtime.build_windows(request(tmp_path, (0.,)), settings(tmp_path))[0][0]
    value = response(window)
    value["events"][0]["evidence_frame_refs"] = ["0.jpg"]
    with pytest.raises(ValueError, match="multiple supplied times"):
        validate_output(value, window)


@pytest.mark.parametrize("raw", ['{"a": 1, "a": 2}', '{"a": NaN}', '```json\n{}\n```'])
def test_raw_json_is_not_repaired(raw):
    with pytest.raises(ValueError):
        strict_json(raw)


def test_fake_response_without_execution_provenance_is_rejected(tmp_path):
    backend = FakeBackend()
    backend.actual_inference = False
    with pytest.raises(RuntimeError, match="without actual inference"):
        runtime.infer(request(tmp_path), settings(tmp_path), backend=backend)


def test_wrong_revision_and_pickle_rejected_before_heavy_imports(tmp_path):
    with pytest.raises(ValueError, match="revision"):
        runtime.validate_model_artifacts(tmp_path, "different")
    (tmp_path / "pytorch_model.bin").write_bytes(b"not a trusted model")
    with pytest.raises(ValueError, match="Pickle"):
        runtime.validate_model_artifacts(tmp_path)


def tiny_cache(tmp_path, monkeypatch):
    payloads = {"config.json": json.dumps({"model_type": "qwen3_vl"}).encode(),
        "model-1.safetensors": b"safetensors-test-fixture",
        "model.safetensors.index.json": json.dumps({"weight_map": {"weight": "model-1.safetensors"}}).encode()}
    for name, payload in payloads.items():
        (tmp_path / name).write_bytes(payload)
    hashes = {name: hashlib.sha256(payload).hexdigest() for name, payload in payloads.items()}
    manifest = dict(files=[dict(name=name, bytes=len(payload)) for name, payload in payloads.items()],
        file_sha256=hashes, weight_sha256={"model-1.safetensors": hashes["model-1.safetensors"]})
    monkeypatch.setattr(runtime, "MANIFEST", manifest)
    return hashes


def test_all_cached_artifacts_are_hashed_before_loader(tmp_path, monkeypatch):
    expected = tiny_cache(tmp_path, monkeypatch)
    root, hashes = runtime.validate_model_artifacts(tmp_path)
    assert root == tmp_path
    assert hashes == expected
    (tmp_path / "model-1.safetensors").write_bytes(b"Safetensors-test-fixture")
    with pytest.raises(ValueError, match="hash mismatch"):
        runtime.validate_model_artifacts(tmp_path)


def test_adapter_never_activates_without_explicit_opt_in(tmp_path):
    assert runtime.validate_adapter_artifacts(None) is None
    with pytest.raises(ValueError, match="opt-in"):
        runtime.validate_adapter_artifacts(tmp_path)
    (tmp_path / "adapter_model.safetensors").write_bytes(b"wrong")
    (tmp_path / "adapter_config.json").write_text("{}")
    with pytest.raises(ValueError, match="hash mismatch"):
        runtime.validate_adapter_artifacts(tmp_path, True)


def test_gpu_loader_fails_without_cuda_and_does_not_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "validate_model_artifacts", lambda *args: (tmp_path, {}))
    fake_torch = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False))
    fake_transformers = SimpleNamespace(AutoProcessor=object, Qwen3VLForConditionalGeneration=object)
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setitem(sys.modules, "transformers", fake_transformers)
    backend = runtime.TransformersBackend(settings(tmp_path))
    with pytest.raises(RuntimeError, match="requires CUDA"):
        backend._load()
    assert backend.model is None


@pytest.mark.parametrize("key,value", [("qwen_max_new_tokens", 1025),
    ("qwen_max_input_tokens", 8193), ("qwen_quantization", "auto"), ("qwen_model_id", "Qwen2.5-VL")])
def test_unverified_runtime_configuration_rejected(tmp_path, key, value):
    with pytest.raises(ValueError):
        runtime.TransformersBackend(settings(tmp_path, **{key: value}))


def test_window_cap_samples_both_ends_of_video_and_reports_omissions(tmp_path):
    source = request(tmp_path, tuple(float(n) for n in range(20)))
    windows, coverage = runtime.build_windows(source, settings(tmp_path, qwen_max_windows=2))
    assert windows[0]["start_s"] == 0
    assert windows[-1]["end_s"] == 19
    assert coverage["total_available_windows"] == 5
    assert coverage["omitted_windows"] == 3
    assert coverage["sampling_policy"] == "uniformly_selected_windows_across_source_timeline"


class FakeTensor:
    def __init__(self, tokens):
        self.shape = (1, tokens)

    def __getitem__(self, index):
        return self


class FakeInputs(dict):
    def to(self, device):
        self.device = device
        return self


class FakeModel:
    device = "cuda:0"

    def eval(self):
        return self

    def generate(self, **kwargs):
        self.generation_kwargs = kwargs
        return FakeTensor(15)


class FakeProcessor:
    def apply_chat_template(self, messages, **kwargs):
        self.messages = messages
        self.template_kwargs = kwargs
        return FakeInputs(input_ids=FakeTensor(10))

    def batch_decode(self, output, **kwargs):
        return ['{"output":"synthetic test double, no GPU execution"}']


def install_fake_loader(tmp_path, monkeypatch):
    from contextlib import nullcontext
    processor, model = FakeProcessor(), FakeModel()
    calls = {}
    def load_processor(path, **kwargs):
        calls["processor"] = kwargs
        return processor
    def load_model(path, **kwargs):
        calls["model"] = kwargs
        return model
    fake_torch = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: True,
        is_bf16_supported=lambda: True, get_device_name=lambda _: "FAKE TEST CUDA"),
        bfloat16="FAKE BF16", __version__="test", inference_mode=nullcontext)
    fake_transformers = SimpleNamespace(
        AutoProcessor=SimpleNamespace(from_pretrained=load_processor),
        Qwen3VLForConditionalGeneration=SimpleNamespace(from_pretrained=load_model))
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setitem(sys.modules, "transformers", fake_transformers)
    monkeypatch.setattr(runtime, "validate_model_artifacts", lambda *args: (tmp_path, {"config.json": "verified-test-hash"}))
    return processor, model, calls


def test_runner_uses_safetensors_offline_flags_determinism_and_token_caps(tmp_path, monkeypatch):
    processor, model, calls = install_fake_loader(tmp_path, monkeypatch)
    backend = runtime.TransformersBackend(settings(tmp_path))
    window = runtime.build_windows(request(tmp_path), settings(tmp_path))[0][0]
    raw = backend.generate(window)
    assert "synthetic test double" in raw
    assert calls["model"]["use_safetensors"] is True
    assert calls["model"]["local_files_only"] is True
    assert calls["model"]["trust_remote_code"] is False
    assert calls["processor"] == dict(local_files_only=True, trust_remote_code=False)
    assert processor.template_kwargs["truncation"] is False
    assert model.generation_kwargs["max_new_tokens"] == 1024
    assert model.generation_kwargs["do_sample"] is False
    assert model.generation_kwargs["max_time"] == 90.0
    assert backend.metadata["adapter_experimental"] is False
    assert backend.metadata["prompt_id"] == "pilot-revised-v2"
    assert backend.metadata["device"] == "FAKE TEST CUDA"


def test_runner_rejects_overlength_context_before_generation(tmp_path, monkeypatch):
    processor, model, calls = install_fake_loader(tmp_path, monkeypatch)
    processor.apply_chat_template = lambda *a, **kw: FakeInputs(input_ids=FakeTensor(8193))
    backend = runtime.TransformersBackend(settings(tmp_path))
    window = runtime.build_windows(request(tmp_path), settings(tmp_path))[0][0]
    with pytest.raises(ValueError, match="never truncate"):
        backend.generate(window)
    assert not hasattr(model, "generation_kwargs")


def test_runner_rejects_late_generations(tmp_path, monkeypatch):
    install_fake_loader(tmp_path, monkeypatch)
    ticks = iter([0.0, 91.0])
    monkeypatch.setattr(runtime.time, "monotonic", lambda: next(ticks))
    backend = runtime.TransformersBackend(settings(tmp_path))
    window = runtime.build_windows(request(tmp_path), settings(tmp_path))[0][0]
    with pytest.raises(TimeoutError, match="time budget"):
        backend.generate(window)
