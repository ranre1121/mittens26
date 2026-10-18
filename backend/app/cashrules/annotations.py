"""Annotation audit/import only. Never use supplied verdicts or rule prose at runtime.

No clip is viewed, event adjudicated, boundary invented or absence inferred here.
The importer deliberately does not assign machine sessions or physical cash IDs.
"""
from collections import Counter, defaultdict
import hashlib
from pathlib import Path
from .contracts import Sequence, ContractError


REVIEW_TYPES = {
    "WF-MACHINE-ALL": {"machine_loading", "machine_operation", "machine_retrieval", "cash_handover"},
    "KZ231-22": {"person_leaving", "person_presence", "cash_handover"},
    "KZ231-20-PRESENCE": {"cash_counting_activity", "machine_operation", "cash_handover", "person_absence_in_view"},
    "KZ231-31-4": {"cash_counting_activity", "machine_operation", "cash_handover", "person_absence_in_view"},
    "KZ231-20-STORAGE": {"object_presence", "object_placement", "person_entering", "door_state"},
    "KZ231-41": {"cash_manual_handling", "machine_loading", "machine_operation"},
    "KZ231-42": {"cash_manual_handling", "cash_counting_activity", "person_presence", "person_absence_in_view"},
    "KZ231-75": {"person_entering", "door_opening", "door_state"},
    "KZ231-24": {"object_inspection", "object_handover", "object_opening"},
    "KZ231-25": {"object_opening", "object_inspection", "cash_counting_activity"},
    "KZ231-12": {"clothing_removal", "object_placement", "object_presence"},
    "KZ231-15": {"cash_handover", "writing", "document_handover"},
    "KZ231-80": {"key_presence", "key_interaction", "object_presence"},
    "KZ231-78-79": {"door_closing", "laptop_interaction", "writing"},
    "KZ231-55": {"object_closing", "key_interaction"},
    "KZ14-PREMISES": {"door_state", "door_opening", "person_entering"},
    "INTERNAL-IT-LOCK": {"screen_state", "laptop_lid_state", "person_leaving"},
}

# Primary-source verified catalog IDs; labels provide review pointers only.
REVIEW_TYPES.update({
    "KZ-CASH-01": {"cash_manual_handling", "machine_loading", "machine_operation", "machine_retrieval"},
    "KZ-CASH-02": {"cash_manual_handling", "cash_counting_activity", "person_presence", "person_absence_in_view"},
    "KZ-CASH-03": {"object_closing", "object_handover", "key_interaction"},
    "KZ-CASH-04": {"person_entering", "door_opening", "door_state"},
    "KZ-CASH-05": {"cash_handover", "writing", "document_handover"},
    "KZ-CASH-06": {"key_presence", "key_interaction", "object_presence"},
    "KZ-CASH-07": {"clothing_removal", "object_placement", "object_presence"},
    "KZ-CASH-08": {"object_inspection", "object_handover", "object_opening"},
    "KZ-CASH-09": {"object_opening", "object_closing", "person_presence"},
    "KZ-CASH-10": {"door_closing", "laptop_interaction", "writing"},
    "KZ-CASH-11": {"person_entering", "object_handover"},
    "KZ-CASH-12": {"door_state", "door_opening"},
    "KZ-CASH-13": {"writing", "object_presence"},
    "KZ-CASH-14": {"document_handover", "document_inspection", "cash_handover", "machine_loading"},
    "WF-EXIT-DOOR-ASSETS": {"person_leaving", "door_state", "object_presence"},
    "KZ-CASH-15": {"person_leaving", "person_presence", "cash_handover"},
    "KZ-CASH-16": {"document_handover", "document_inspection", "object_handover"},
})


def annotation_audit(data, source_path, source_sha256):
    if data.get("format") != "fortebank-normalized-events":raise ContractError("Unsupported annotation format")
    events = data["events"]
    counts = Counter(e["event_type"] for e in events)
    examples = {}
    for rule, types in REVIEW_TYPES.items():
        selected = [e for e in events if e["event_type"] in types and e.get("timeline_role") == "structural_starting_point"]
        # Short review pointer list, spread across clips; not predicted violations.
        by_video = {}
        for e in selected:
            if e["video_sha256"] not in by_video:by_video[e["video_sha256"]] = e
        examples[rule] = [dict(event_id=e["event_id"], video_sha256=e["video_sha256"], source_clip_ids=e["source_clip_ids"],
                              event_type=e["event_type"], anchor_s=e["anchor_s"], start_s=e["start_s"], end_s=e["end_s"],
                              timing_status=e["timing_status"], source_annotation_refs=e["source_annotation_refs"],
                              adjudication_status=e["adjudication_status"],
                              missing="Rule applicability, linked identities, transaction scope, absence/completion evidence and independent media review")
                          for e in list(by_video.values())[:5]]
    return dict(schema_version="cashrules.annotation-audit.v1", source_path=str(source_path), source_sha256=source_sha256,
                source_status=data.get("status"), original_video_groups=len(data["videos"]), annotation_rows=len(events),
                event_type_counts=dict(sorted(counts.items())),
                adjudication_counts=dict(Counter(e["adjudication_status"] for e in events)),
                timing_status_counts=dict(Counter(e["timing_status"] for e in events)),
                source_rule_candidates=len(data.get("rule_candidates", [])),
                runtime_import_time_unresolved=[dict(event_id=e["event_id"],video_sha256=e["video_sha256"],source_annotation_refs=e["source_annotation_refs"],reason="No observation timestamp or explicit interval; excluded from temporal import, preserved in source/audit")
                                               for e in events if e.get("anchor_s") is None and (e.get("start_s") is None or e.get("end_s") is None)],
                supplied_verdicts_used=False, source_rule_strings_used_as_law=False, media_review_performed=False,
                review_pointers=examples,
                limitations=["Review pointers are observed-label candidates, not violation findings or ground truth.",
                             "Alternative reviewers may be dependent; do not count them as independent evidence.",
                             "Missing labels never establish a missing step; coarse shared windows do not establish order."])


def import_annotations(data, source_path, source_sha256):
    """Return candidate-only sequences, one per original video, with no transactions."""
    if data.get("format") != "fortebank-normalized-events":raise ContractError("Unsupported annotation format")
    groups = defaultdict(list)
    for i, row in enumerate(data["events"]):groups[row["video_sha256"]].append((i, row))
    output = []
    for video, rows in sorted(groups.items()):
        events, evidence = [], []
        for i, row in rows:
            # Use reported observation bounds only, never reference-violation times
            # or the next annotation as an inferred endpoint.
            if row.get("start_s") is not None and row.get("end_s") is not None:
                start, end = row["start_s"], row["end_s"]
            else:start = end = row["anchor_s"]
            if start is None or end is None:continue
            eid = row["event_id"]
            refs = [dict(frame_ref="annotation:" + eid + ":start", timestamp_s=start)]
            if end != start:refs.append(dict(frame_ref="annotation:" + eid + ":end", timestamp_s=end))
            evidence.append(dict(evidence_id="ev:" + eid, source_type="normalized_annotation", review_status="candidate",
                                 refs=refs, source_pointer=str(source_path) + "#/events/" + str(i), source_sha256=source_sha256))
            events.append(dict(event_id=eid, event_type="annotation_observation", start_s=start, end_s=end,
                               entity_ids=[], evidence_ids=["ev:" + eid], identity="unknown", visibility="partial",
                               data=dict(source_event_type=row["event_type"], limitations=["unadjudicated source label; no physical identity or continuous coverage"])))
        sequence = dict(schema_version="cashrules.sequence.v1", synthetic=False, video_sha256=video,
                        events=events, evidence=evidence, coverage=[], transactions=[], facts=[])
        Sequence.parse(sequence)
        output.append(sequence)
    return output


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
