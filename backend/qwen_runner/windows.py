"""Qwen windows, prompts and output validation, ported from the qwen-pilot
(execution-v1/revision-v3/pilot/vendor/hybrid_mvp/qwen.py) to the backend's
command contract (app/pipeline/providers.py).

Pure Python: nothing here imports torch, so the tests run without a GPU.

Differences from the pilot, all deliberate:
- Windows come from the backend request (sampled frames + optional detector
  boxes), not from a pilot tracks.json run directory.
- Every frame also carries a whole-frame region object ("view-1"). Without the
  accepted YOLO weights there are no detections, and the model may only cite
  supplied object IDs; the region gives it something honest to cite.
- Output is checked per event: an invalid event is dropped (and logged by the
  daemon), the rest of the window is kept. Malformed JSON still rejects the
  whole window. Nothing is repaired.
- Counting events that cite no cash-pile detection become unknown_event, as in
  app/cashrules/qwen_bridge.py.
"""

from __future__ import annotations

import json
import math
from copy import deepcopy
from pathlib import Path

PROMPT_PATH = Path(__file__).with_name("prompt-v2.txt")

EVENT_TYPES = ["object_visible", "object_handover", "object_placement", "object_retrieval",
               "object_loading", "manual_cash_handling", "bag_opening", "document_handling",
               "counting_start", "counting_activity", "completion_evidence", "unknown_event"]
TEMPORAL = set(EVENT_TYPES) - {"object_visible", "unknown_event"}
COUNTING = {"counting_start", "counting_activity", "completion_evidence"}
PILE_CLASSES = {"cash_stack", "banknote_like_stack"}
BASE_FIELDS = {"event_type", "start_s", "end_s", "object_ids", "evidence_frame_refs",
               "observed_facts", "visibility_limits", "assessment"}
EXTRA_FIELDS = {"confidence", "identity_status", "completion_status", "completion_cue", "full_lot_observed"}
RESPONSE_FIELDS = {"schema_version", "window_id", "coverage_status", "events", "summary", "limitations"}

REGION = {"display_id": "view-1", "track_id": "frame-region", "class_name": "scene_region",
          "box": [0.0, 0.0, 1.0, 1.0], "score": 1.0,
          "identity_uncertainty": ["Whole-frame region supplied by the service, not a detected object"]}


def system_prompt() -> str:
    # The pilot strips the trailing newline before using the prompt (run_pilot.py).
    return PROMPT_PATH.read_text(encoding="utf-8").rstrip("\n")


def number(x, name, low=0.0, high=float("inf")):
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) or not low <= x <= high:
        raise ValueError(f"{name}: expected finite number in [{low}, {high}]")
    return float(x)


def text(x, name):
    if not isinstance(x, str) or not x.strip():
        raise ValueError(f"{name}: nonempty string required")
    return x


def keys(obj, required):
    if not isinstance(obj, dict) or set(obj) != set(required):
        raise ValueError(f"Invalid fields; required={sorted(required)}")


def strings(value, name):
    if not isinstance(value, list) or not value or any(not isinstance(v, str) for v in value) or len(value) != len(set(value)):
        raise ValueError(name + " needs nonempty unique strings")
    for item in value:
        text(item, name)


def output_schema(max_events: int) -> dict:
    string_list = {"type": "array", "items": {"type": "string"}, "minItems": 1, "uniqueItems": True}
    properties = dict(event_type={"enum": EVENT_TYPES}, start_s={"type": "number", "minimum": 0},
        end_s={"type": "number", "minimum": 0}, object_ids=string_list, evidence_frame_refs=string_list,
        observed_facts=string_list, visibility_limits=string_list, assessment={"enum": ["candidate", "unknown"]},
        confidence={"type": "number", "minimum": 0, "maximum": 1},
        identity_status={"enum": ["track_hypothesis", "ambiguous"]},
        completion_status={"enum": ["unknown", "not_observed", "candidate_explicit_evidence"]},
        completion_cue={"type": "string"}, full_lot_observed={"const": False})
    return {"type": "object", "additionalProperties": False,
        "required": ["schema_version", "window_id", "coverage_status", "events", "summary", "limitations"],
        "properties": {"schema_version": {"const": "datathon-qwen-events-v1"}, "window_id": {"type": "string"},
            "coverage_status": {"enum": ["partial", "not_observed"]}, "summary": {"type": "string"},
            "limitations": string_list, "events": {"type": "array", "maxItems": max_events,
                "items": {"type": "object", "additionalProperties": False,
                    "required": sorted(BASE_FIELDS | EXTRA_FIELDS), "properties": properties}}}}


def frame_objects(frame: dict) -> list[dict]:
    """The region object plus this frame's detections. Detections are not
    tracked, so their IDs are local to the frame."""
    objects = [deepcopy(REGION)]
    counts: dict[str, int] = {}
    for det in frame.get("detections", []):
        name = det["class_name"]
        counts[name] = counts.get(name, 0) + 1
        objects.append({"display_id": f"{name}-{counts[name]}", "track_id": f"{frame['frame_ref']}#{len(objects)}",
                        "class_name": name, "box": [min(1.0, max(0.0, float(v))) for v in det["box"]],
                        "score": float(det["score"]),
                        "identity_uncertainty": ["Single-frame detection; not linked across frames"]})
    return [o for o in objects if o["box"][0] < o["box"][2] and o["box"][1] < o["box"][3]]


def build_windows(request: dict, max_windows: int = 12, frames_per_window: int = 6,
                  target_window_s: float = 8.0, image_max_edge: int = 896, max_events: int = 8) -> list[dict]:
    """Cover the whole video with at most `max_windows` equal spans, each with
    up to `frames_per_window` frames spread evenly over the span."""
    frames = sorted(request["frames"], key=lambda f: f["timestamp_s"])
    if not frames:
        raise ValueError("No frames in request")
    duration = max(float(request["duration_sec"]), frames[-1]["timestamp_s"] + 1e-3)
    count = max(1, min(max_windows, math.ceil(duration / target_window_s)))
    span = duration / count
    windows = []
    for index in range(count):
        lo, hi = index * span, (index + 1) * span
        selected = [f for f in frames if lo <= f["timestamp_s"] < hi or (index == count - 1 and f["timestamp_s"] >= lo)]
        if not selected:
            continue
        if len(selected) > frames_per_window:
            picks = [0] if frames_per_window == 1 else [round(i * (len(selected) - 1) / (frames_per_window - 1)) for i in range(frames_per_window)]
            selected = [selected[i] for i in picks]
        rows = [dict(frame_ref=f["frame_ref"], timestamp_s=float(f["timestamp_s"]), clean_path=f["path"],
                     objects=frame_objects(f), invisible_tracks=[]) for f in selected]
        windows.append(dict(window_id=f"window-{len(windows):04d}", start_s=rows[0]["timestamp_s"],
                            end_s=rows[-1]["timestamp_s"], frames=rows, image_max_edge=image_max_edge,
                            max_events=max_events))
    return windows


def messages_for(window: dict, prompt: str) -> list[dict]:
    """Same message layout as the pilot's messages_for, with the system text
    replaced by `prompt` (the pilot's collate() does the same for prompt-v2)."""
    from PIL import Image

    edge = int(window["image_max_edge"])
    content = []
    for frame in window["frames"]:
        image = Image.open(Path(frame["clean_path"])).convert("RGB")
        image.thumbnail((edge, edge))
        objects = []
        for original in frame["objects"]:
            obj = {k: deepcopy(original[k]) for k in ["display_id", "track_id", "class_name", "box", "score",
                   "association_confidence", "identity_uncertainty", "zones"] if k in original}
            obj["box"] = [round(v, 4) for v in obj["box"]]
            obj["score"] = round(obj["score"], 3)
            objects.append(obj)
        context = dict(frame_ref=frame["frame_ref"], timestamp_s=round(frame["timestamp_s"], 6),
                       objects=objects, invisible_tracks=[])
        content.extend([{"type": "text", "text": "SOURCE FRAME " + json.dumps(context)}, {"type": "image", "image": image}])
    content.insert(0, {"type": "text", "text": json.dumps(dict(
        context_policy="Uncalibrated scores and geometry are rounded for display. Older unmatched track hypotheses are summarized once; full raw tracks remain in the request. No audio or unseen motion is supplied.",
        older_unmatched_track_hypotheses=[]))})
    content.append({"type": "text", "text": json.dumps(dict(window_id=window["window_id"],
        time_semantics="sampled_evidence_span_not_action_onset", output_schema=output_schema(window["max_events"]),
        response_instruction="Use short factual strings and at most the schema event limit. Preserve uncertainty; do not fill the limit with weak events."))})
    return [{"role": "system", "content": [{"type": "text", "text": prompt}]}, {"role": "user", "content": content}]


def strict_json(raw: str):
    if not isinstance(raw, str) or len(raw.encode()) > 65536:
        raise ValueError("Bounded JSON text required")

    def pairs(rows):
        result = {}
        for key, value in rows:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result

    def invalid(value):
        raise ValueError("Non-finite JSON number: " + value)

    return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)


def validate_response(data, window: dict) -> None:
    """Window-level checks; a failure discards the whole window."""
    keys(data, RESPONSE_FIELDS)
    if data["schema_version"] != "datathon-qwen-events-v1" or data["window_id"] != window["window_id"]:
        raise ValueError("Response schema/window mismatch")
    if data["coverage_status"] not in {"partial", "not_observed"}:
        raise ValueError("No confirmed coverage from a model")
    if not isinstance(data["events"], list) or len(data["events"]) > window["max_events"]:
        raise ValueError("Bounded events list required")


def validate_event(event, window: dict) -> None:
    """The pilot's per-event rules (validate_output + validate_object_events)."""
    keys(event, BASE_FIELDS | EXTRA_FIELDS)
    if event["event_type"] not in EVENT_TYPES:
        raise ValueError("Unsupported event type")
    if event["assessment"] not in {"candidate", "unknown"}:
        raise ValueError("Model output cannot confirm an event")
    for field in ["object_ids", "evidence_frame_refs", "observed_facts", "visibility_limits"]:
        strings(event[field], field)
    frames = {f["frame_ref"]: f for f in window["frames"]}
    if not set(event["evidence_frame_refs"]) <= set(frames):
        raise ValueError("Evidence was not supplied to model")
    cited = [frames[r] for r in event["evidence_frame_refs"]]
    times = [f["timestamp_s"] for f in cited]
    for field, expected in (("start_s", min(times)), ("end_s", max(times))):
        if not math.isclose(number(event[field], field), expected, abs_tol=1e-6, rel_tol=0):
            raise ValueError("Event span must match cited evidence endpoints")
    number(event["confidence"], "confidence", 0, 1)
    if event["identity_status"] not in {"track_hypothesis", "ambiguous"}:
        raise ValueError("Physical identity is unproven")
    if not isinstance(event["completion_cue"], str):
        raise ValueError("completion_cue must be a string")
    if event["full_lot_observed"] is not False:
        raise ValueError("Sparse model evidence cannot establish full-lot coverage")
    if event["completion_status"] not in {"unknown", "not_observed", "candidate_explicit_evidence"}:
        raise ValueError("Invalid completion state")
    if event["event_type"] in TEMPORAL and (len(event["evidence_frame_refs"]) < 2 or max(times) <= min(times)):
        raise ValueError("Temporal claims require multiple supplied times")
    if event["event_type"] == "completion_evidence":
        if event["completion_status"] != "candidate_explicit_evidence" or not event["completion_cue"].strip():
            raise ValueError("Completion requires explicit visible cue, still only candidate")
    elif event["completion_status"] == "candidate_explicit_evidence" or event["completion_cue"]:
        raise ValueError("Activity/loading/retrieval never imply complete coverage")
    seen = {o["display_id"] for f in cited for o in f["objects"]}
    if not set(event["object_ids"]) <= seen:
        raise ValueError("Object IDs absent from cited evidence")


def to_backend_event(event: dict, window: dict) -> dict:
    """One validated pilot event → the providers.py event shape, with the same
    mapping as app/cashrules/qwen_bridge.py."""
    frames = {f["frame_ref"]: f for f in window["frames"]}
    refs = list(dict.fromkeys(event["evidence_frame_refs"]))
    times = [frames[r]["timestamp_s"] for r in refs]
    classes = {o["display_id"]: o["class_name"] for r in refs for o in frames[r]["objects"]}
    event_type = event["event_type"]
    entities = list(event["object_ids"])
    limitations = list(event["visibility_limits"]) + ["Unlinked model tracks; partial samples; no physical lot/session or full-count credit"]
    data = dict(source_event_type=event_type, limitations=list(dict.fromkeys(limitations)))
    if event_type in COUNTING:
        piles = [i for i in entities if classes.get(i) in PILE_CLASSES]
        if len(piles) != 1:
            event_type = "unknown_event"
        else:
            entities = piles
            if event_type == "completion_evidence":
                data.update(full_lot=False, completion_cue=event["completion_cue"])
    return dict(event_type=event_type, start_s=min(times), end_s=max(times), frame_refs=refs,
                entity_ids=entities, data=data)


def parse_window_output(raw: str, window: dict) -> tuple[list[dict], dict]:
    """Returns (backend events, report). Raises ValueError for a rejected window."""
    data = strict_json(raw)
    validate_response(data, window)
    events, dropped = [], []
    for index, event in enumerate(data["events"]):
        try:
            validate_event(event, window)
        except (ValueError, KeyError, TypeError) as exc:
            dropped.append({"index": index, "reason": str(exc)})
            continue
        events.append(to_backend_event(event, window))
    return events, {"summary": data.get("summary"), "observed": [e.get("observed_facts") for e in data["events"] if isinstance(e, dict)], "dropped": dropped}
