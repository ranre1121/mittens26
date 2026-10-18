"""Offline Qwen3-VL inference using the verified revised-prompt pilot contract."""
from __future__ import annotations

from bisect import bisect_left
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import time

from app.cashrules.qwen_bridge import candidate_sequence
from app.pipeline.qwen_contract import (
    MODEL_ID, SYSTEM, messages_for, number, sha256, strict_json,
    text, validate_output, validate_window,
)
from app.pipeline.qwen_model_manifest import MANIFEST

REVISION = "0c351dd01ed87e9c1b53cbc748cba10e6187ff3b"
ADAPTER_SHA256 = "37ea6c54b73d0fba0b0001a281f3c442bab99b15b3bdce13b3efc35874ad669c"
ADAPTER_CONFIG_SHA256 = "a91ffee4ef0c5abf0cc1034013849b8859b00894ef140655941bc368e0c95e1d"
EQUIPMENT_CLASSES = {"laptop", "counting_machine_top", "counting_machine_bot"}
LIMITATIONS = [
    "Unreviewed model candidates; semantic accuracy and physical identity are unverified.",
    "Partial sampled frames cannot establish full-lot coverage or absence of an event.",
    "Empty or unknown output means insufficient evidence, never an automatic all-clear.",
    "Equipment detector context is disabled following the development comparison.",
]


def _integer(settings, key, default, low, high):
    value = getattr(settings, key, default)
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{key} must be an integer in [{low}, {high}]")
    return value


def validate_model_artifacts(model_dir, revision=REVISION):
    """Verify bytes before Transformers/PEFT can deserialize any model artifact."""
    if revision != REVISION:
        raise ValueError("Only the verified official Qwen3-VL-8B-Instruct revision is supported")
    if model_dir is None:
        raise ValueError("QWEN_MODEL_DIR must point to an existing verified local model")
    root = Path(model_dir).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("QWEN_MODEL_DIR must be a directory")
    if any(p.suffix.lower() in {".bin", ".pt", ".pth", ".pkl", ".pickle"} for p in root.iterdir()):
        raise ValueError("Pickle-based model artifacts are not permitted")
    expected = MANIFEST["weight_sha256"]
    if {p.name for p in root.glob("*.safetensors")} != set(expected):
        raise ValueError("Exactly four official safetensors shards are required")
    hashes = {}
    for row in MANIFEST["files"]:
        path = root / row["name"]
        if not path.is_file() or path.stat().st_size != row["bytes"]:
            raise ValueError("Missing or mismatched Qwen artifact: " + row["name"])
        actual = sha256(path)
        if actual != MANIFEST["file_sha256"][row["name"]]:
            raise ValueError("Official Qwen artifact hash mismatch: " + row["name"])
        hashes[row["name"]] = actual
    config = json.loads((root / "config.json").read_text())
    if config.get("model_type") != "qwen3_vl":
        raise ValueError("Qwen3-VL model configuration required")
    index = json.loads((root / "model.safetensors.index.json").read_text())
    if set(index.get("weight_map", {}).values()) != set(expected):
        raise ValueError("Unexpected safetensors weight index")
    return root, hashes


def validate_adapter_artifacts(adapter_dir, allow_experimental=False):
    if adapter_dir is None:
        return None
    if allow_experimental is not True:
        raise ValueError("LoRA requires explicit experimental adapter opt-in")
    root = Path(adapter_dir).resolve(strict=True)
    if not root.is_dir() or any(p.suffix.lower() in {".bin", ".pt", ".pth", ".pkl", ".pickle"} for p in root.iterdir()):
        raise ValueError("Only the verified safetensors adapter is supported")
    if {p.name for p in root.glob("*.safetensors")} != {"adapter_model.safetensors"}:
        raise ValueError("Exactly the verified experimental adapter is required")
    if (sha256(root / "adapter_model.safetensors") != ADAPTER_SHA256
            or sha256(root / "adapter_config.json") != ADAPTER_CONFIG_SHA256):
        raise ValueError("Experimental adapter hash mismatch")
    return root


def build_windows(request, settings):
    if not isinstance(request, dict) or request.get("schema_version") != "datathon.qwen.request.v1":
        raise ValueError("Qwen request schema mismatch")
    duration = number(request.get("duration_sec"), "duration_sec")
    source_start = number(request.get("source_start_s", 0), "source_start_s")
    digest = request.get("video_sha256")
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ValueError("Source video SHA256 required")
    frames = request.get("frames")
    if not isinstance(frames, list) or not 1 <= len(frames) <= 3600:
        raise ValueError("One to 3600 bounded source frames required")
    max_frames = _integer(settings, "qwen_max_frames_per_window", 6, 2, 8)
    max_windows = _integer(settings, "qwen_max_windows", 16, 1, 128)
    seconds = number(getattr(settings, "qwen_window_seconds", 6.0), "window seconds", .1, 30)
    stride = number(getattr(settings, "qwen_stride_seconds", 4.0), "stride seconds", .1, 30)
    edge = _integer(settings, "qwen_image_max_edge", 896, 224, 896)
    data_dir = getattr(settings, "data_dir", None)
    data_root = Path(data_dir).resolve() if data_dir else None
    rows, refs, previous = [], set(), -1.0
    for index, frame in enumerate(frames):
        if not isinstance(frame, dict):
            raise ValueError("Source frame object required")
        timestamp = number(frame.get("timestamp_s"), "source PTS", source_start, source_start + duration)
        reference = text(frame.get("frame_ref"), "frame_ref")
        if timestamp <= previous or reference in refs:
            raise ValueError("Unique increasing source timestamps and frame refs required")
        previous = timestamp
        refs.add(reference)
        path = Path(frame["path"]).resolve(strict=True)
        if data_root and not path.is_relative_to(data_root):
            raise ValueError("Source frame escapes private data directory")
        if not path.is_file() or path.stat().st_size > 10 * 1024 * 1024 or path.suffix.lower() not in {".jpg", ".jpeg", ".png"}:
            raise ValueError("Bounded local JPEG or PNG source frame required")
        supplied = frame.get("objects", frame.get("detections", []))
        if not isinstance(supplied, list) or len(supplied) > 128:
            raise ValueError("At most 128 visual hypotheses per frame required")
        objects = []
        for n, source in enumerate(supplied):
            if not isinstance(source, dict):
                raise ValueError("Visual hypothesis object required")
            if source.get("class_name") in EQUIPMENT_CLASSES:
                continue
            if "cash_lot_id" in source:
                raise ValueError("Visual hypotheses must not assign physical cash-lot IDs")
            obj = {k: deepcopy(source[k]) for k in (
                "display_id", "track_id", "class_name", "box", "score",
                "association_confidence", "identity_uncertainty", "identity_scope", "zones",
            ) if k in source}
            if "display_id" not in obj or "track_id" not in obj:
                identity = f"frame-{index:04d}-object-{n:03d}"
                obj.update(display_id=identity, track_id=identity,
                    identity_scope="single_frame_detection_not_temporal_track",
                    identity_uncertainty=["Frame-local detector hypothesis; temporal identity is not established"])
            objects.append(obj)
        rows.append(dict(frame_ref=reference, timestamp_s=timestamp, clean_path=str(path),
                         clean_sha256=sha256(path), objects=objects, invisible_tracks=[]))
    times = [row["timestamp_s"] for row in rows]
    candidates = []
    # Store only source index ranges until the bounded windows are selected.
    index = 0
    while (start := times[0] + index * stride) <= times[-1]:
        left, right = bisect_left(times, start), bisect_left(times, start + seconds)
        if left < right:
            candidates.append((left, right))
        index += 1
    total_windows = len(candidates)
    if total_windows > max_windows:
        selected_indices = ([total_windows // 2] if max_windows == 1 else
            [round(i * (total_windows - 1) / (max_windows - 1)) for i in range(max_windows)])
        candidates = [candidates[i] for i in selected_indices]
    windows = []
    for left, right in candidates:
        selected = rows[left:right]
        if len(selected) > max_frames:
            selected = [selected[round(i * (len(selected) - 1) / (max_frames - 1))] for i in range(max_frames)]
        window = dict(window_id=f"window-{len(windows):04d}", start_s=selected[0]["timestamp_s"],
            end_s=selected[-1]["timestamp_s"], frames=deepcopy(selected), image_max_edge=edge,
            max_events=16, source_video_sha256=digest, synthetic=False,
            identity_scope="unreviewed visual hypotheses, never permanent cash-lot identity")
        windows.append(validate_window(window))
    analyzed = {f["frame_ref"] for w in windows for f in w["frames"]}
    coverage = dict(source_frames=len(rows), analyzed_frames=len(analyzed),
        omitted_frames=len(rows) - len(analyzed), windows=len(windows),
        total_available_windows=total_windows, omitted_windows=total_windows - len(windows),
        window_limit_reached=total_windows > max_windows, coverage_status="partial",
        sampling_policy="uniformly_selected_windows_across_source_timeline",
        sampled_spans=[dict(start_s=w["start_s"], end_s=w["end_s"]) for w in windows])
    return windows, coverage


class TransformersBackend:
    """Loads only verified local artifacts; every generation requires CUDA."""

    actual_inference = False

    def __init__(self, settings):
        self.settings = settings
        self.max_new_tokens = _integer(settings, "qwen_max_new_tokens", 1024, 64, 1024)
        self.max_input_tokens = _integer(settings, "qwen_max_input_tokens", 8192, 256, 8192)
        self.max_time = number(getattr(settings, "qwen_generation_timeout_seconds", 90.0), "generation timeout", 1, 300)
        self.quantization = getattr(settings, "qwen_quantization", "bf16")
        if self.quantization not in {"bf16", "nf4"}:
            raise ValueError("Qwen quantization must be explicitly bf16 or nf4")
        if getattr(settings, "qwen_model_id", MODEL_ID) != MODEL_ID:
            raise ValueError("Only Qwen/Qwen3-VL-8B-Instruct is supported")
        self.model = None
        self.metadata = {}

    def _load(self):
        if self.model is not None:
            return
        root, hashes = validate_model_artifacts(getattr(self.settings, "qwen_model_dir", None),
            getattr(self.settings, "qwen_model_revision", REVISION))
        adapter = validate_adapter_artifacts(getattr(self.settings, "qwen_adapter_dir", None),
            getattr(self.settings, "qwen_allow_experimental_adapter", False))
        os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1", WANDB_MODE="disabled")
        import torch
        from transformers import AutoProcessor, Qwen3VLForConditionalGeneration
        if not torch.cuda.is_available():
            raise RuntimeError("Qwen3-VL runtime requires CUDA; no automatic fallback")
        if not torch.cuda.is_bf16_supported():
            raise RuntimeError("The verified Qwen runtime requires CUDA BF16 support")
        processor = AutoProcessor.from_pretrained(str(root), local_files_only=True, trust_remote_code=False)
        kwargs = dict(local_files_only=True, trust_remote_code=False, use_safetensors=True,
            torch_dtype=torch.bfloat16, device_map={"": 0}, low_cpu_mem_usage=True, attn_implementation="sdpa")
        if self.quantization == "nf4":
            from transformers import BitsAndBytesConfig
            kwargs["quantization_config"] = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                bnb_4bit_use_double_quant=True, bnb_4bit_compute_dtype=torch.bfloat16,
                llm_int8_skip_modules=["visual", "lm_head"])
        model = Qwen3VLForConditionalGeneration.from_pretrained(str(root), **kwargs).eval()
        if self.quantization == "nf4":
            import bitsandbytes as bnb
            converted = [name for name, module in model.named_modules() if isinstance(module, bnb.nn.Linear4bit)]
            if not converted or any("visual" in name or "language_model" not in name for name in converted):
                raise RuntimeError("NF4 escaped the verified language-only scope")
        if adapter:
            from peft import PeftModel
            model = PeftModel.from_pretrained(model, str(adapter), is_trainable=False,
                local_files_only=True, use_safetensors=True).eval()
        self.processor, self.model, self.torch = processor, model, torch
        self.metadata = dict(model_id=MODEL_ID, model_revision=REVISION,
            prompt_id="pilot-revised-v2", prompt_sha256=hashlib.sha256(SYSTEM.encode()).hexdigest(),
            model_artifact_sha256=hashes, quantization=self.quantization,
            adapter_sha256=ADAPTER_SHA256 if adapter else None, adapter_experimental=bool(adapter),
            adapter_semantic_improvement_established=False, local_files_only=True,
            trust_remote_code=False, use_safetensors=True, device=torch.cuda.get_device_name(0),
            dtype="bfloat16", torch_version=torch.__version__, max_new_tokens=self.max_new_tokens,
            max_input_tokens=self.max_input_tokens, equipment_context_enabled=False)

    def generate(self, window):
        validate_window(window)
        self.actual_inference = False
        self._load()
        messages = messages_for(window)
        try:
            inputs = self.processor.apply_chat_template(messages, add_generation_prompt=True,
                tokenize=True, return_dict=True, return_tensors="pt", padding=False, truncation=False)
        finally:
            for message in messages:
                for block in message["content"]:
                    if block.get("type") == "image":
                        block["image"].close()
        count = int(inputs["input_ids"].shape[-1])
        if count > self.max_input_tokens:
            raise ValueError("Qwen input context exceeds budget; never truncate evidence")
        inputs = inputs.to(self.model.device)
        started = time.monotonic()
        with self.torch.inference_mode():
            output = self.model.generate(**inputs, max_new_tokens=self.max_new_tokens,
                do_sample=False, use_cache=True, max_time=self.max_time)
        elapsed = time.monotonic() - started
        self.actual_inference = True
        self.metadata.update(input_tokens=count, generated_tokens=int(output.shape[-1] - count),
                             generation_seconds=elapsed)
        if elapsed > self.max_time:
            raise TimeoutError("Qwen generation exceeded its time budget")
        return self.processor.batch_decode(output[:, count:], skip_special_tokens=True)[0]


def infer(request, settings, *, backend=None):
    windows, coverage = build_windows(request, settings)
    runtime = backend if backend is not None else TransformersBackend(settings)
    events, records, limitations, seen = [], [], list(LIMITATIONS), set()
    for window in windows:
        raw = runtime.generate(window)
        if getattr(runtime, "actual_inference", False) is not True:
            raise RuntimeError("Qwen provider returned without actual inference provenance")
        output = validate_output(strict_json(raw), window)
        projected = candidate_sequence(output, window, request["video_sha256"])
        by_id = {row["evidence_id"]: row for row in projected["evidence"]}
        for event in projected["events"]:
            refs = [r["frame_ref"] for r in by_id[event["evidence_ids"][0]]["refs"]]
            row = dict(event_type=event["event_type"], start_s=event["start_s"], end_s=event["end_s"],
                       frame_refs=refs, entity_ids=event["entity_ids"], data=event["data"])
            signature = json.dumps(row, sort_keys=True, allow_nan=False)
            if signature not in seen:
                seen.add(signature)
                events.append(row)
        records.append(dict(window_id=window["window_id"], output=output,
                            actual_inference=True, model_metadata=deepcopy(runtime.metadata)))
        limitations.extend(output["limitations"])
    if len(events) > 200:
        raise ValueError("Qwen produced more than 200 candidates; reduce the window budget")
    if coverage["omitted_frames"]:
        limitations.append(f"Window and frame budgets omitted {coverage['omitted_frames']} source frames; no absence conclusion is supported.")
    if getattr(settings, "qwen_adapter_dir", None):
        limitations.append("Experimental LoRA improved pilot formatting but has no established overall semantic advantage.")
    provenance = dict(runtime.metadata)
    provenance.update(actual_inference=True, semantic_accuracy_validated=False, coverage=coverage,
                      inference_contract="datathon.qwen.v1", model_output_contract="datathon-qwen-events-v1")
    return dict(events=events, is_mock=False, limitations=list(dict.fromkeys(limitations)),
                provenance=provenance, windows=records)
