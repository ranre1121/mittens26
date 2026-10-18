"""Strict typed contracts. A track, annotation or model assertion is not a fact.

All source time values are seconds on the ORIGINAL video's presentation timeline.
Confirmed evidence requires a reviewer, or an explicitly labelled synthetic test.
"""
from dataclasses import dataclass, asdict
from enum import Enum
import hashlib
import json
import math
from datetime import date
from typing import Optional


class ContractError(ValueError):
    pass


class Decision(str, Enum):
    CANDIDATE = "supported_violation_candidate"
    CLEAR = "no_violation_observed"
    UNKNOWN = "insufficient_evidence"


class Review(str, Enum):
    CANDIDATE = "candidate"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


EVENT_TYPES = {
    "lot_introduced", "split", "merge", "identity_gap", "occlusion",
    "machine_loading", "counting_start", "counting_activity", "completion_evidence",
    "machine_retrieval", "machine_interruption", "cash_bypass", "cash_receipt", "cash_issue",
    "workstation_exit", "service_active", "customer_absent", "customer_present",
    "cash_exposed", "backoffice_process", "controller_absent", "controller_present",
    "vault_entry", "escort_absent", "escort_present", "collection_bag_accepted",
    "bag_inspection_omission", "bag_inspected", "bag_damage", "bag_opening",
    "collector_absent", "collector_present", "personal_item_present", "unknown_event",
    # These are observations only; no rule promotes them to completion or a legal finding.
    "object_visible", "object_handover", "object_placement", "object_retrieval",
    "object_loading", "manual_cash_handling", "document_handling", "person_leaving",
    "person_entering", "person_presence", "person_absence_in_view", "laptop_interaction",
    "laptop_lid_state", "door_state", "annotation_observation",
    "workstation_presence", "cassette_ready", "cassette_transfer", "cassette_unlocked",
    "cassette_unsealed", "cassette_locked", "cassette_sealed", "transport_package_defect",
    "transport_acceptance", "defect_repaired", "transport_packing", "sender_absent",
    "sender_present", "loading_activity", "third_party_access", "third_party_view",
    "asset_present", "authorized_custodians_absent", "authorized_custodian_present",
    "room_exit",
    "identity_verification_record_absent", "identity_verification_confirmed",
}
SOURCE_TYPES = {"human_review", "model", "normalized_annotation", "telemetry", "synthetic"}
IDENTITIES = {"confirmed", "hypothesis", "unknown"}
VISIBILITIES = {"sufficient", "partial", "not_observed"}
DATA_TYPES = {
    "full_lot": bool, "lineage_complete": bool, "completion_cue": str,
    "all_opportunities_excluded": bool, "prior_transaction_id": str,
    "outside_locking_storage": bool, "process": str, "zone_id": str,
    "child_lot_ids": list, "parent_lot_ids": list, "track_ids": list,
    "source_event_type": str, "limitations": list, "method": str,
    "storage_confirmed": bool,
    "asset_kind": str, "protection_state": str, "screen_lock_state": str,
    "door_state": str, "lock_state": str,
    "customer_id": str,
}


def fail(message):
    raise ContractError(message)


def keys(row, required, optional=()):
    if not isinstance(row, dict) or set(row) - set(required) - set(optional) or set(required) - set(row):
        fail("Invalid fields: required=" + str(sorted(required)))


def string(value, name):
    if not isinstance(value, str) or not value.strip():
        fail(name + " requires a nonempty string")
    return value


def number(value, name):
    if type(value) not in {int, float} or not math.isfinite(value) or value < 0:
        fail(name + " requires a finite nonnegative number")
    return float(value)


def strings(value, name, nonempty=False):
    if not isinstance(value, list) or (nonempty and not value):
        fail(name + " requires a string list")
    if any(not isinstance(x, str) or not x.strip() for x in value) or len(set(value)) != len(value):
        fail(name + " requires unique nonempty strings")
    return tuple(value)


def digest(value, name):
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        fail(name + " requires lowercase SHA-256")
    return value


def interval(start, end):
    start, end = number(start, "start_s"), number(end, "end_s")
    if end < start:
        fail("Reversed interval")
    return start, end


@dataclass(frozen=True)
class FrameRef:
    frame_ref: str
    timestamp_s: float

    @classmethod
    def parse(cls, row):
        keys(row, {"frame_ref", "timestamp_s"})
        return cls(string(row["frame_ref"], "frame_ref"), number(row["timestamp_s"], "timestamp_s"))


@dataclass(frozen=True)
class Evidence:
    evidence_id: str
    source_type: str
    review_status: str
    refs: tuple
    source_pointer: str
    reviewer: Optional[str] = None
    source_sha256: Optional[str] = None

    @classmethod
    def parse(cls, row, synthetic):
        keys(row, {"evidence_id", "source_type", "review_status", "refs", "source_pointer"},
             {"reviewer", "source_sha256"})
        string(row["evidence_id"], "evidence_id");string(row["source_pointer"], "source_pointer")
        if row["source_type"] not in SOURCE_TYPES or row["review_status"] not in set(Review):
            fail("Invalid evidence source/review status")
        if not isinstance(row["refs"], list) or not row["refs"]:
            fail("Evidence needs timestamped source references")
        refs = tuple(FrameRef.parse(x) for x in row["refs"])
        if len({x.frame_ref for x in refs}) != len(refs):
            fail("Duplicate frame references")
        if row["source_type"] == "synthetic" and not synthetic:
            fail("Synthetic evidence forbidden in a real sequence")
        if row["review_status"] == Review.CONFIRMED:
            if row["source_type"] in {"model", "normalized_annotation"}:
                fail("Model/normalized annotations cannot self-confirm")
            if row["source_type"] != "synthetic":
                string(row.get("reviewer"), "confirmed evidence reviewer")
        if row.get("source_sha256") is not None:
            digest(row["source_sha256"], "source_sha256")
        return cls(row["evidence_id"], row["source_type"], row["review_status"], refs,
                   row["source_pointer"], row.get("reviewer"), row.get("source_sha256"))


@dataclass(frozen=True)
class Event:
    event_id: str
    event_type: str
    start_s: float
    end_s: float
    entity_ids: tuple
    evidence_ids: tuple
    identity: str
    visibility: str
    data: dict
    transaction_id: Optional[str] = None
    session_id: Optional[str] = None
    machine_id: Optional[str] = None

    @classmethod
    def parse(cls, row):
        keys(row, {"event_id", "event_type", "start_s", "end_s", "entity_ids", "evidence_ids",
                   "identity", "visibility", "data"}, {"transaction_id", "session_id", "machine_id"})
        string(row["event_id"], "event_id")
        if row["event_type"] not in EVENT_TYPES or row["identity"] not in IDENTITIES or row["visibility"] not in VISIBILITIES:
            fail("Invalid event type/identity/visibility")
        start, end = interval(row["start_s"], row["end_s"])
        entities = strings(row["entity_ids"], "entity_ids")
        if row["event_type"] not in {"unknown_event", "annotation_observation"} and not entities:
            fail("Observable events require explicit subject/object references")
        refs = strings(row["evidence_ids"], "evidence_ids", True)
        if not isinstance(row["data"], dict) or set(row["data"]) - set(DATA_TYPES):
            fail("Unknown event data field")
        for key, value in row["data"].items():
            if type(value) is not DATA_TYPES[key]:
                fail("Invalid data type: " + key)
            if isinstance(value, list):
                strings(value, key)
        for key in ("transaction_id", "session_id", "machine_id"):
            if row.get(key) is not None:string(row[key], key)
        if row["event_type"] in {"machine_loading", "counting_start", "counting_activity", "completion_evidence", "machine_retrieval", "machine_interruption"}:
            # Manual counting may be represented without a machine session.
            if row["identity"] == "confirmed" and not (row["event_type"] == "counting_activity" and row["data"].get("method") == "manual"):
                string(row.get("session_id"), "machine session_id")
                string(row.get("machine_id"), "machine_id")
            if len(entities) != 1:fail("Counting event needs exactly one cash lot hypothesis")
        if row["event_type"] == "completion_evidence" and not row["data"].get("completion_cue", "").strip():
            fail("Completion needs an explicit cue")
        return cls(row["event_id"], row["event_type"], start, end, entities, refs,
                   row["identity"], row["visibility"], dict(row["data"]), row.get("transaction_id"),
                   row.get("session_id"), row.get("machine_id"))


@dataclass(frozen=True)
class Coverage:
    topic: str
    start_s: float
    end_s: float
    status: str
    evidence_ids: tuple

    @classmethod
    def parse(cls, row):
        keys(row, {"topic", "start_s", "end_s", "status", "evidence_ids"})
        if row["status"] not in {"complete", "partial", "gap"}:fail("Invalid coverage status")
        return cls(string(row["topic"], "coverage topic"), *interval(row["start_s"], row["end_s"]),
                   row["status"], strings(row["evidence_ids"], "coverage evidence", True))


@dataclass(frozen=True)
class Transaction:
    transaction_id: str
    kind: str
    start_s: Optional[float]
    end_s: Optional[float]
    lot_ids: tuple
    machine_ids: tuple
    inventory_complete: bool
    evidence_ids: tuple

    @classmethod
    def parse(cls, row):
        keys(row, {"transaction_id", "kind", "start_s", "end_s", "lot_ids", "machine_ids",
                   "inventory_complete", "evidence_ids"})
        string(row["transaction_id"], "transaction_id")
        if row["kind"] not in {"receipt", "issue", "back_office", "unknown"}:fail("Invalid transaction kind")
        if type(row["inventory_complete"]) is not bool:fail("inventory_complete must be Boolean")
        start = None if row["start_s"] is None else number(row["start_s"], "start_s")
        end = None if row["end_s"] is None else number(row["end_s"], "end_s")
        if start is not None and end is not None and end < start:fail("Reversed transaction")
        return cls(row["transaction_id"], row["kind"], start, end,
                   strings(row["lot_ids"], "lot_ids"), strings(row["machine_ids"], "machine_ids"),
                   row["inventory_complete"], strings(row["evidence_ids"], "boundary evidence", True))


@dataclass(frozen=True)
class Fact:
    name: str
    target: str
    value: bool
    evidence_ids: tuple

    @classmethod
    def parse(cls, row):
        keys(row, {"name", "target", "value", "evidence_ids"})
        if type(row["value"]) is not bool:fail("Fact value must be Boolean; omit unknown facts")
        return cls(string(row["name"], "fact name"), string(row["target"], "fact target"), row["value"],
                   strings(row["evidence_ids"], "fact evidence", True))


@dataclass(frozen=True)
class Sequence:
    schema_version: str
    synthetic: bool
    video_sha256: str
    events: tuple
    evidence: tuple
    coverage: tuple
    transactions: tuple
    facts: tuple
    operation_date: Optional[str] = None

    @classmethod
    def parse(cls, row):
        keys(row, {"schema_version", "synthetic", "video_sha256", "events", "evidence", "coverage", "transactions", "facts"}, {"operation_date"})
        if row["schema_version"] != "cashrules.sequence.v1" or type(row["synthetic"]) is not bool:
            fail("Invalid sequence version/synthetic flag")
        digest(row["video_sha256"], "video_sha256")
        if row.get("operation_date") is not None:
            try:date.fromisoformat(row["operation_date"])
            except (TypeError, ValueError):fail("operation_date must be ISO calendar date")
        for key in ("events", "evidence", "coverage", "transactions", "facts"):
            if not isinstance(row[key], list):fail(key + " must be a list")
        seq = cls(row["schema_version"], row["synthetic"], row["video_sha256"],
                  tuple(Event.parse(e) for e in row["events"]),
                  tuple(Evidence.parse(e, row["synthetic"]) for e in row["evidence"]),
                  tuple(Coverage.parse(c) for c in row["coverage"]),
                  tuple(Transaction.parse(t) for t in row["transactions"]),
                  tuple(Fact.parse(f) for f in row["facts"]), row.get("operation_date"))
        for items, attr in ((seq.events, "event_id"), (seq.evidence, "evidence_id"), (seq.transactions, "transaction_id")):
            if len({getattr(x, attr) for x in items}) != len(items):fail("Duplicate " + attr)
        evid = {e.evidence_id: e for e in seq.evidence}
        txs = {t.transaction_id for t in seq.transactions}
        for record in (*seq.events, *seq.coverage, *seq.transactions, *seq.facts):
            if not set(record.evidence_ids) <= set(evid):fail("Dangling evidence reference")
        for e in seq.events:
            if e.transaction_id is not None and e.transaction_id not in txs:fail("Unknown transaction reference")
            times = [r.timestamp_s for i in e.evidence_ids for r in evid[i].refs]
            if not min(times) <= e.start_s <= e.end_s <= max(times):
                fail("Event interval is not bracketed by cited source times")
        for c in seq.coverage:
            times = [r.timestamp_s for i in c.evidence_ids for r in evid[i].refs]
            if not min(times) <= c.start_s <= c.end_s <= max(times):
                fail("Coverage interval is not bracketed by cited source times")
        ref_times = {}
        for evidence in seq.evidence:
            for ref in evidence.refs:
                if ref.frame_ref in ref_times and ref_times[ref.frame_ref] != ref.timestamp_s:
                    fail("Same source frame reference has conflicting timestamps")
                ref_times[ref.frame_ref] = ref.timestamp_s
        return seq

    def to_dict(self):
        return json.loads(json.dumps(asdict(self)))


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def strict_load(path):
    def pairs(rows):
        result = {}
        for key, value in rows:
            if key in result:fail("Duplicate JSON key: " + key)
            result[key] = value
        return result
    with open(path, encoding="utf-8") as source:
        return json.load(source, object_pairs_hook=pairs, parse_constant=lambda x: fail("Non-finite JSON: " + x))
