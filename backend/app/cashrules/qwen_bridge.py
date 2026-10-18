"""Import existing Qwen candidate output without inference or evidence promotion.

This bridge supplements, not replaces, task4's upstream request/output validation.
It verifies cited refs, times and observed track membership, keeps partial sampling
and unproven identity explicit, and deliberately creates NO counting sessions,
physical lots, transactions, confirmed evidence, legal facts or count credits.
"""
from .contracts import Sequence, ContractError, EVENT_TYPES, canonical_hash


def candidate_sequence(response, window, video_sha256):
    if response.get("schema_version") != "datathon-qwen-events-v1" or response.get("window_id") != window.get("window_id"):
        raise ContractError("Qwen schema/window mismatch")
    if response.get("coverage_status") not in {"partial", "not_observed"}:
        raise ContractError("Qwen cannot claim complete coverage")
    frames = {f["frame_ref"]:f for f in window["frames"]}
    if len(frames) != len(window["frames"]):raise ContractError("Duplicate input frame refs")
    evidence, events = [], []
    for i, raw in enumerate(response["events"]):
        if raw.get("assessment") not in {"candidate", "unknown"} or raw.get("identity_status") not in {"track_hypothesis", "ambiguous"}:
            raise ContractError("Model cannot confirm identity/event")
        if raw.get("full_lot_observed") is not False:raise ContractError("Model cannot establish full-lot coverage")
        refs = raw["evidence_frame_refs"]
        if not refs or len(set(refs)) != len(refs) or not set(refs) <= set(frames):raise ContractError("Unknown/duplicate cited frame")
        times = [frames[r]["timestamp_s"] for r in refs]
        if abs(raw["start_s"] - min(times)) > 1e-6 or abs(raw["end_s"] - max(times)) > 1e-6:
            raise ContractError("Qwen span must match supplied cited source PTS")
        objects = {o["display_id"]:o for r in refs for o in frames[r]["objects"]}
        if not raw["object_ids"] or not set(raw["object_ids"]) <= set(objects):raise ContractError("Qwen cited unseen track")
        event_type = raw["event_type"]
        if event_type not in EVENT_TYPES:raise ContractError("Unknown Qwen event type")
        entities = list(raw["object_ids"])
        limitations = list(raw["visibility_limits"]) + ["Unlinked model tracks; partial samples; no physical lot/session or full-count credit"]
        data = dict(source_event_type=event_type, limitations=list(dict.fromkeys(limitations)))
        if event_type in {"counting_start", "counting_activity", "completion_evidence"}:
            entities = [p for p in entities if objects[p]["class_name"] in {"cash_stack", "banknote_like_stack"}]
            if len(entities) != 1:
                event_type = "unknown_event";entities = list(raw["object_ids"])
            elif event_type == "completion_evidence":
                if raw.get("completion_status") != "candidate_explicit_evidence" or not raw.get("completion_cue", "").strip():
                    raise ContractError("Completion candidate needs a visible cue")
                data.update(full_lot=False, completion_cue=raw["completion_cue"])
        eid = window["window_id"] + ":" + str(i)
        evidence.append(dict(evidence_id="model:" + eid, source_type="model", review_status="candidate",
                             refs=[dict(frame_ref=r, timestamp_s=frames[r]["timestamp_s"]) for r in refs],
                             source_pointer="qwen-window:" + window["window_id"] + "#/events/" + str(i),
                             source_sha256=canonical_hash(response)))
        events.append(dict(event_id=eid, event_type=event_type, start_s=min(times), end_s=max(times),
                           entity_ids=entities, evidence_ids=["model:" + eid], identity="hypothesis", visibility="partial", data=data))
    result = dict(schema_version="cashrules.sequence.v1", synthetic=False, video_sha256=video_sha256,
                  operation_date=None, events=events, evidence=evidence, coverage=[], transactions=[], facts=[])
    Sequence.parse(result)
    return result
