"""Explicitly synthetic behavioural fixtures, never evidence of actual conduct."""
from copy import deepcopy
import json
from pathlib import Path
from app.config import REPO_ROOT


def catalog(*ids):
    data = json.loads((REPO_ROOT / "rules" / "cashrules-catalog.json").read_text())
    data["rules"] = [r for r in data["rules"] if not ids or r["rule_id"] in ids]
    return data


def sequence(lots=("A", "B", "C"), kind="receipt"):
    d = dict(schema_version="cashrules.sequence.v1", synthetic=True, operation_date="2026-10-18", video_sha256="a"*64,
             events=[], evidence=[], coverage=[], transactions=[dict(transaction_id="T", kind=kind, start_s=0, end_s=100,
             lot_ids=list(lots), machine_ids=["M"], inventory_complete=True, evidence_ids=["scope"])], facts=[])
    evidence(d, "scope", 0, 100)
    fact(d, "machine_scope_complete")
    for topic in ["transaction:T", "machine:M", *("cash_path:" + x for x in lots)]:coverage(d, topic)
    return d


def evidence(d, eid, start=0, end=100, status="confirmed", source="synthetic"):
    refs = [dict(frame_ref=eid + ":" + str(start), timestamp_s=start)]
    if end != start:refs.append(dict(frame_ref=eid + ":" + str(end), timestamp_s=end))
    d["evidence"].append(dict(evidence_id=eid, source_type=source, review_status=status, refs=refs,
                              source_pointer="synthetic-test:" + eid))
    return eid


def fact(d, name, target="T", value=True):
    d["facts"].append(dict(name=name, target=target, value=value, evidence_ids=["scope"]))


def contexts(d, rule_id):
    for name in catalog(rule_id)["rules"][0]["required_context_facts"]:fact(d, name)


def coverage(d, topic, start=0, end=100, status="complete"):
    d["coverage"].append(dict(topic=topic, start_s=start, end_s=end, status=status, evidence_ids=["scope"]))


def event(d, kind, entities=("A",), start=10, end=11, data=None, sid=None, machine=None,
          identity="confirmed", visibility="sufficient", status="confirmed", tx="T", eid=None):
    eid = eid or "e" + str(len(d["events"]))
    ev = evidence(d, "proof:" + eid, start, end, status=status)
    row = dict(event_id=eid, event_type=kind, start_s=start, end_s=end, entity_ids=list(entities), evidence_ids=[ev],
               identity=identity, visibility=visibility, data=data or {}, transaction_id=tx)
    if sid is not None:row["session_id"] = sid
    if machine is not None:row["machine_id"] = machine
    d["events"].append(row)
    return row


def count(d, lot, sid, start=10, full=True, complete=True):
    event(d,"machine_loading",(lot,),start,start+1,sid=sid,machine="M")
    event(d,"counting_activity",(lot,),start+2,start+3,sid=sid,machine="M")
    if complete:event(d,"completion_evidence",(lot,),start+4,start+5,sid=sid,machine="M",data=dict(full_lot=full, completion_cue="SYNTHETIC reviewed end indicator"))
    event(d,"machine_retrieval",(lot,),start+6,start+7,sid=sid,machine="M")


def bypass(d, lot="C", start=80):
    return event(d,"cash_bypass",(lot,),start,start+5,data=dict(full_lot=True,all_opportunities_excluded=True))
