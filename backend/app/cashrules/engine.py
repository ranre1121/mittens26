"""Pure deterministic evaluation. No inference, I/O, legal text generation or GPU.

Closed-world reasoning is allowed only for explicitly reviewed complete coverage.
Absent detections/annotations are NEVER converted into absence events.
"""
from dataclasses import asdict
from .contracts import Sequence, Review, Decision, ContractError, canonical_hash


class EvidenceIndex:
    def __init__(self, seq):
        self.seq = seq
        self.evidence = {e.evidence_id: e for e in seq.evidence}
        self.txs = {t.transaction_id: t for t in seq.transactions}

    def confirmed(self, ids):
        return bool(ids) and all(self.evidence[i].review_status == Review.CONFIRMED for i in ids)

    def trusted(self, event):
        return self.confirmed(event.evidence_ids) and event.visibility == "sufficient" and event.identity == "confirmed"

    def fact(self, name, target, value=True):
        rows = [f for f in self.seq.facts if f.name == name and f.target == target and self.confirmed(f.evidence_ids)]
        return bool(rows) and all(f.value == value for f in rows)

    def covered(self, topic, start, end):
        if start is None or end is None:return False
        rows = [c for c in self.seq.coverage if c.topic == topic]
        # Explicit gap/partial evidence vetoes an overlapping complete-coverage claim.
        if any(c.status != "complete" and c.start_s < end and c.end_s > start for c in rows):return False
        spans = sorted((c.start_s, c.end_s) for c in rows if c.status == "complete" and self.confirmed(c.evidence_ids))
        cursor = start
        for left, right in spans:
            if left > cursor + 1e-6:break
            if right >= cursor:cursor = right
            if cursor >= end - 1e-6:return True
        return False

    def bounded(self, tx):
        if tx.start_s is None or tx.end_s is None or not self.confirmed(tx.evidence_ids):return False
        times = [r.timestamp_s for i in tx.evidence_ids for r in self.evidence[i].refs]
        return min(times) <= tx.start_s <= tx.end_s <= max(times)

    def evidence_payload(self, events=(), ids=()):
        all_ids = set(ids) | {i for e in events for i in e.evidence_ids}
        return [asdict(self.evidence[i]) for i in sorted(all_ids)]


def deduplicate(events):
    """Exact semantic duplicates only. Never merge two nearby independent counts.

    Duplicate observations retain all evidence references. Repeated independent
    sessions have different session IDs and remain separate. Input-order invariant.
    Near-duplicate overlapping windows must be assigned the SAME session by a
    reviewed linkage step; absent linkage is uncertainty, not extra coverage.
    """
    from dataclasses import replace
    groups = {}
    for e in sorted(events, key=lambda x: (x.start_s, x.end_s, x.event_id)):
        payload = asdict(e)
        for k in ("event_id", "evidence_ids"):payload.pop(k)
        key = canonical_hash(payload)
        if key not in groups:groups[key] = (e, [e.event_id])
        else:
            old, ids = groups[key]
            groups[key] = (replace(old, evidence_ids=tuple(sorted(set(old.evidence_ids) | set(e.evidence_ids)))), ids + [e.event_id])
    values = sorted(groups.values(), key=lambda x: (x[0].start_s, x[0].end_s, x[0].event_id))
    return tuple(x[0] for x in values), [dict(canonical_event_id=e.event_id, source_event_ids=ids) for e, ids in values if len(ids) > 1]


def closure(full, relations):
    """Full-credit equivalence for REVIEWED exhaustive, disjoint composition only."""
    full = set(full)
    changed = True
    while changed:
        before = len(full)
        for whole, parts in relations:
            if whole in full:full.update(parts)
            if set(parts) <= full:full.add(whole)
        changed = len(full) != before
    return full


def cash_ledger(tx, events, index):
    """Cash lot identities are supplied/reviewed, never created from track numbers.

    Partial-feed credits never add arithmetically. To prove two complementary
    portions, first establish an exhaustive split and count each child fully.
    """
    selected = [e for e in events if e.transaction_id == tx.transaction_id and
                (tx.start_s is None or e.start_s >= tx.start_s) and
                (tx.end_s is None or e.end_s <= tx.end_s)]
    known = set(tx.lot_ids)
    retired = set()
    relations = []
    footprints = {lot: {lot} for lot in known}
    warnings = []
    sessions = {}
    full, partial, gaps, bypass = set(), set(), set(), set()
    accepted = []
    # Apply lineage and sessions in source order. Completion credit is monotonic
    # only while physical continuity is supported; gap evidence vetoes a result.
    for e in selected:
        ids = set(e.entity_ids)
        if e.event_type in {"identity_gap", "occlusion"}:
            # Candidate loss can only reduce certainty, never create a finding.
            if any(index.evidence[i].review_status != Review.REJECTED for i in e.evidence_ids):
                gaps.update(ids or known)
            continue
        if e.event_type in {"split", "merge"}:
            parents = tuple(e.data.get("parent_lot_ids", e.entity_ids))
            children = tuple(e.data.get("child_lot_ids", ()))
            valid_shape = ((e.event_type == "split" and len(parents) == 1 and len(children) >= 2) or
                           (e.event_type == "merge" and len(parents) >= 2 and len(children) == 1))
            valid = (index.trusted(e) and e.data.get("lineage_complete") is True and valid_shape and
                     len(set(parents)) == len(parents) and len(set(children)) == len(children) and
                     set(parents) <= known - retired and not set(children) & known)
            if valid and e.event_type == "merge":
                sets = [footprints[x] for x in parents]
                valid = all(not a & b for n, a in enumerate(sets) for b in sets[n+1:])
            if not valid:
                gaps.update(set(parents) | set(children));warnings.append(e.event_id + ": unresolved lineage")
                continue
            retired.update(parents);known.update(children)
            if e.event_type == "split":
                relations.append((parents[0], children))
                # Fresh disjoint child footprints; update containing composites.
                old = footprints[parents[0]]
                replacement = set(children)
                for k in list(footprints):
                    if old <= footprints[k]:footprints[k] = (footprints[k] - old) | replacement
                for child in children:footprints[child] = {child}
            else:
                relations.append((children[0], parents))
                footprints[children[0]] = set().union(*(footprints[p] for p in parents))
            full = closure(full, relations);accepted.append(e)
        elif e.event_type == "cash_bypass":
            if (index.trusted(e) and len(ids) == 1 and ids <= known - retired and
                    e.data.get("full_lot") is True and e.data.get("all_opportunities_excluded") is True):
                bypass.update(ids);accepted.append(e)
        elif e.event_type in {"machine_loading", "counting_start", "counting_activity", "completion_evidence", "machine_retrieval", "machine_interruption"} and e.session_id:
            s = sessions.setdefault(e.session_id, {"events": [], "bindings": set(), "bad": False})
            s["events"].append(e)
            s["bindings"].add((tuple(e.entity_ids), e.machine_id))
            if not ids <= known - retired:s["bad"] = True
            if len(s["bindings"]) != 1:s["bad"] = True
    # Evaluate every session, including multiple sessions for the SAME lot.
    for sid, s in sorted(sessions.items()):
        rows = s["events"]
        trusted = [e for e in rows if index.trusted(e)]
        loads = [e for e in trusted if e.event_type == "machine_loading"]
        activity = [e for e in trusted if e.event_type in {"counting_start", "counting_activity"}]
        ends = [e for e in trusted if e.event_type == "completion_evidence"]
        interrupted = any(e.event_type == "machine_interruption" and
                          any(index.evidence[i].review_status != Review.REJECTED for i in e.evidence_ids) for e in rows)
        good = False
        if not s["bad"] and not interrupted:
            for end in ends:
                if (end.data.get("full_lot") is True and end.data.get("completion_cue", "").strip() and
                    any(l.end_s <= a.start_s <= a.end_s <= end.start_s for l in loads for a in activity)):
                    lot = end.entity_ids[0]
                    if end.machine_id in tx.machine_ids:
                        full.add(lot);accepted.extend(rows);good = True
        if not good and trusted:
            partial.update(x for e in trusted for x in e.entity_ids)
        s["state"] = "completion_supported" if good else ("interrupted" if interrupted else "unresolved")
    full = closure(full, relations)
    # Full-lot bypass propagates DOWN only across verified composition. A bypassed
    # child establishes SOME uncovered cash in its containing parent, never in a
    # disjoint sibling. Partial counting propagates upward but never creates full
    # credit or apportions an unlabelled partial feed.
    full_bypass = set(bypass)
    changed = True
    while changed:
        before = len(full_bypass)
        for whole, parts in relations:
            if whole in full_bypass:full_bypass.update(parts)
        changed = len(full_bypass) != before
    some_bypass = set(full_bypass)
    partial.update(full)
    changed = True
    while changed:
        before = len(some_bypass) + len(partial)
        for whole, parts in relations:
            if set(parts) & some_bypass:some_bypass.add(whole)
            if set(parts) & partial:partial.add(whole)
        changed = len(some_bypass) + len(partial) != before
    # Propagate uncertain physical lineage conservatively over the same composition.
    changed = True
    while changed:
        before = len(gaps)
        for whole, parts in relations:
            if whole in gaps or set(parts) & gaps:gaps.update((whole, *parts))
        changed = len(gaps) != before
    lots = {}
    for lot in sorted(known):
        if lot in gaps:state = "identity_or_visibility_uncertain"
        elif lot in full and lot in some_bypass:state = "contradictory_evidence"
        elif lot in full_bypass and lot in partial:state = "contradictory_evidence"
        elif lot in full:state = "full_count_coverage"
        elif lot in full_bypass and any(lot in e.entity_ids for s in sessions.values() for e in s["events"]):
            state = "contradictory_or_incomplete_session"
        elif lot in some_bypass:state = "supported_bypass"
        elif lot in partial:state = "partial_or_unresolved_count"
        else:state = "unknown"
        lots[lot] = {"coverage_state": state, "retired_by_lineage": lot in retired}
    return {"lots": lots, "sessions": [dict(session_id=sid, state=s["state"],
                lot_machine_bindings=[dict(lot_ids=list(ids), machine_id=m) for ids, m in sorted(s["bindings"], key=str)],
                event_ids=[e.event_id for e in s["events"]]) for sid, s in sorted(sessions.items())],
            "lineage": [dict(whole=w, exhaustive_disjoint_parts=list(p)) for w, p in relations],
            "warnings": warnings, "accepted_event_ids": sorted({e.event_id for e in accepted})}


def assess_machine_coverage(tx, events, index):
    ledger = cash_ledger(tx, events, index)
    problems = []
    if not index.bounded(tx):problems.append("transaction_boundaries_unconfirmed")
    if not tx.inventory_complete or not tx.lot_ids or not index.confirmed(tx.evidence_ids):problems.append("lot_inventory_incomplete")
    if not tx.machine_ids or not index.fact("machine_scope_complete", tx.transaction_id):problems.append("machine_scope_unconfirmed")
    topics = ["transaction:" + tx.transaction_id] + ["cash_path:" + lot for lot in tx.lot_ids] + ["machine:" + m for m in tx.machine_ids]
    for topic in topics:
        if not index.covered(topic, tx.start_s, tx.end_s):problems.append("incomplete_coverage:" + topic)
    required = {lot: ledger["lots"].get(lot, {}).get("coverage_state", "unknown") for lot in tx.lot_ids}
    if not problems and any(v == "supported_bypass" for v in required.values()):
        decision = Decision.CANDIDATE
    elif not problems and required and all(v == "full_count_coverage" for v in required.values()):
        decision = Decision.CLEAR
    else:
        decision = Decision.UNKNOWN
        problems.extend(lot + ":" + state for lot, state in required.items() if state != "full_count_coverage")
    return decision, problems, ledger


def _contained(outer, inner):
    return outer.start_s <= inner.start_s <= inner.end_s <= outer.end_s


def pattern_candidates(rule, tx, events, index):
    """Bounded positive predicates. Absence must be EXPLICIT and confirmed.

    No inference of role, authorization, screen locking, digital records, cash
    amount, or legal applicability from appearance/annotation prose occurs here.
    """
    trusted = [e for e in events if e.transaction_id == tx.transaction_id and index.trusted(e)]
    kind = rule["engine"]
    out = []
    if not index.bounded(tx):return [], ["transaction_boundaries_unconfirmed"]
    missing = [name for name in rule.get("required_context_facts", []) if not index.fact(name, tx.transaction_id)]
    if missing:return [], ["missing_context:" + x for x in missing]
    if rule.get("transaction_kinds") and tx.kind not in rule["transaction_kinds"]:
        return [], ["workflow_applicability_not_established"]
    def rows(t):return [e for e in trusted if e.event_type == t and tx.start_s <= e.start_s <= e.end_s <= tx.end_s]
    def pair(action, absence, topic):
        for a in rows(action):
            for b in rows(absence):
                if _contained(b, a) and index.covered(topic + tx.transaction_id, b.start_s, b.end_s):out.append((a, b))
    if kind == "leave_during_service":
        for leave in rows("workstation_exit"):
            if not all(index.fact("cashier_role_verified",p) for p in leave.entity_ids):continue
            for active in rows("service_active"):
                if (_contained(active, leave) and leave.end_s < tx.end_s and
                    index.covered("workstation:" + tx.transaction_id, active.start_s, active.end_s)):
                    out.append((active, leave))
    elif kind == "customer_absent_during_count":
        for count in rows("counting_activity"):
            for absent in rows("customer_absent"):
                if not _contained(absent, count) or not index.covered("customer:" + tx.transaction_id, absent.start_s, absent.end_s):continue
                handovers = rows("cash_receipt" if tx.kind == "receipt" else "cash_issue")
                for h in handovers:
                    correct_order = h.end_s <= count.start_s if tx.kind == "receipt" else count.end_s <= h.start_s
                    if correct_order and h.entity_ids == count.entity_ids and index.covered("cash_path:" + count.entity_ids[0], min(h.start_s, count.start_s), max(h.end_s, count.end_s)):
                        out.append((h, count, absent))
    elif kind == "prior_cash_exposed":
        for exposed in rows("cash_exposed"):
            prior = index.txs.get(exposed.data.get("prior_transaction_id"))
            if (prior and index.bounded(prior) and prior.kind == "receipt" and prior.end_s <= tx.start_s and
                set(exposed.entity_ids) <= set(prior.lot_ids) and not set(exposed.entity_ids) & set(tx.lot_ids) and
                exposed.data.get("outside_locking_storage") is True and
                all(index.covered("cash_path:" + lot, prior.end_s, exposed.end_s) for lot in exposed.entity_ids)):
                out.append((exposed,))
    elif kind == "controller_absent":
        pair("backoffice_process", "controller_absent", "room:")
        out = [g for g in out if g[0].data.get("process") in {"recount", "sort", "packaging"}]
    elif kind == "unescorted_vault_entry":
        for entry in rows("vault_entry"):
            if not all(index.fact("entrant_requires_escort", entity) for entity in entry.entity_ids):continue
            for absent in rows("escort_absent"):
                if _contained(absent, entry) and index.covered("access:" + tx.transaction_id, absent.start_s, absent.end_s):out.append((entry, absent))
    elif kind == "bag_inspection_omitted":
        for accepted in rows("collection_bag_accepted"):
            for omitted in rows("bag_inspection_omission"):
                if (omitted.entity_ids == accepted.entity_ids and omitted.end_s <= accepted.start_s and
                    index.covered("bag:" + accepted.entity_ids[0], omitted.start_s, accepted.end_s)):
                    out.append((omitted, accepted))
    elif kind == "damaged_bag_without_collector":
        for damaged in rows("bag_damage"):
            for opened in rows("bag_opening"):
                if damaged.entity_ids != opened.entity_ids or damaged.end_s > opened.start_s:continue
                for absent in rows("collector_absent"):
                    if _contained(absent, opened) and index.covered("room:" + tx.transaction_id, absent.start_s, absent.end_s):out.append((damaged, opened, absent))
    elif kind == "personal_item_in_cash_node":
        for item in rows("personal_item_present"):
            if (item.data.get("storage_confirmed") is True and item.end_s > item.start_s and
                index.covered("cash_node:" + tx.transaction_id, item.start_s, item.end_s) and
                all(index.fact("personal_ownership_and_no_exception", entity) for entity in item.entity_ids)):
                out.append((item,))
    elif kind == "cassette_unprotected":
        for ready in rows("cassette_ready"):
            for transfer in rows("cassette_transfer"):
                if ready.entity_ids != transfer.entity_ids or ready.end_s > transfer.start_s:continue
                for defect in rows("cassette_unlocked") + rows("cassette_unsealed"):
                    repair_type = "cassette_locked" if defect.event_type == "cassette_unlocked" else "cassette_sealed"
                    repaired = any(p.entity_ids == defect.entity_ids and defect.start_s <= p.end_s <= transfer.end_s for p in rows(repair_type))
                    if defect.entity_ids == transfer.entity_ids and _contained(defect, transfer) and not repaired and index.covered("cassette:" + transfer.entity_ids[0], ready.start_s, transfer.end_s):
                        out.append((ready, defect, transfer))
    elif kind == "defective_transport_acceptance":
        for defect in rows("transport_package_defect"):
            for accepted in rows("transport_acceptance"):
                repaired = any(p.entity_ids == defect.entity_ids and defect.end_s <= p.end_s <= accepted.start_s for p in rows("defect_repaired"))
                if (defect.entity_ids == accepted.entity_ids and defect.end_s <= accepted.start_s and not repaired and
                    index.covered("package:" + accepted.entity_ids[0], defect.start_s, accepted.end_s)):
                    out.append((defect, accepted))
    elif kind == "sender_absent_during_packing":
        pair("transport_packing", "sender_absent", "room:")
    elif kind == "third_party_loading_exposure":
        for loading in rows("loading_activity"):
            for third in rows("third_party_access") + rows("third_party_view"):
                if not all(index.fact("verified_third_party", p) for p in third.entity_ids):continue
                if third.event_type == "third_party_view" and not index.fact("public_sightline_verified", tx.transaction_id):continue
                if _contained(loading, third) and index.covered("loading_area:" + tx.transaction_id, loading.start_s, loading.end_s):out.append((loading, third))
    elif kind == "deposit_identity_verification":
        for receipt in rows("cash_receipt"):
            customer=receipt.data.get("customer_id")
            if not customer:continue
            for absent in rows("identity_verification_record_absent"):
                if (customer in absent.entity_ids and absent.start_s<=tx.start_s and absent.end_s>=tx.end_s and
                    index.fact("verification_record_absence_confirmed",tx.transaction_id) and
                    index.covered("identification:"+tx.transaction_id,tx.start_s,tx.end_s)):
                    prior=any(customer in e.entity_ids for e in rows("identity_verification_confirmed"))
                    if not prior:out.append((absent,receipt))
    # Explicit contrary occupancy/inspection evidence suppresses a conflicting claim.
    opposites = {"customer_absent": "customer_present", "controller_absent": "controller_present",
                 "escort_absent": "escort_present", "collector_absent": "collector_present",
                 "bag_inspection_omission": "bag_inspected", "sender_absent": "sender_present"}
    filtered = []
    for group in out:
        conflict = any(e.event_type in opposites and any(
            p.event_type == opposites[e.event_type] and p.start_s <= e.end_s and p.end_s >= e.start_s
            for p in trusted) for e in group)
        if not conflict:filtered.append(group)
    return filtered, ([] if filtered else ["required_observable_pattern_not_established_or_contradictory"])


def compliant_pattern(rule, tx, events, index):
    """Positive compliance evidence for a bounded interval; no empty-list acquittal."""
    if not index.bounded(tx):return ()
    if any(not index.fact(n, tx.transaction_id) for n in rule.get("required_context_facts", [])):return ()
    if rule.get("transaction_kinds") and tx.kind not in rule["transaction_kinds"]:return ()
    trusted = [e for e in events if e.transaction_id == tx.transaction_id and index.trusted(e)
               and tx.start_s <= e.start_s <= e.end_s <= tx.end_s]
    def rows(t):return [e for e in trusted if e.event_type == t]
    kind = rule["engine"]
    if kind == "leave_during_service":
        for present in rows("workstation_presence"):
            if present.start_s <= tx.start_s and present.end_s >= tx.end_s and index.covered("workstation:" + tx.transaction_id, tx.start_s, tx.end_s):
                if not rows("workstation_exit"):return (present,)
    pairs = {"controller_absent": ("backoffice_process", "controller_present", "controller_absent", "room:"),
             "sender_absent_during_packing": ("transport_packing", "sender_present", "sender_absent", "room:")}
    if kind in pairs:
        action, presence, absence, topic = pairs[kind]
        actions = rows(action)
        if kind == "controller_absent":actions = [a for a in actions if a.data.get("process") in {"recount", "sort", "packaging"}]
        if not actions or rows(absence) or not index.covered(topic + tx.transaction_id, tx.start_s, tx.end_s):return ()
        used = []
        for a in actions:
            found = next((p for p in rows(presence) if _contained(p, a)), None)
            if found is None:return ()
            used.extend((a, found))
        return tuple(used)
    if kind == "unescorted_vault_entry":
        if not index.covered("access:" + tx.transaction_id, tx.start_s, tx.end_s) or rows("escort_absent"):return ()
        entries = rows("vault_entry")
        if not entries:return ()
        used = []
        for entry in entries:
            if all(index.fact("entrant_requires_escort", p, False) for p in entry.entity_ids):used.append(entry);continue
            escort = next((p for p in rows("escort_present") if _contained(p, entry)), None)
            if escort is None or not all(index.fact("entrant_requires_escort", p) and index.fact("entry_authorized", p) for p in entry.entity_ids):return ()
            used.extend((entry, escort))
        return tuple(used)
    if kind == "deposit_identity_verification":
        receipts=rows("cash_receipt")
        if not receipts or rows("identity_verification_record_absent") or not index.covered("identification:"+tx.transaction_id,tx.start_s,tx.end_s):return ()
        used=[]
        for receipt in receipts:
            customer=receipt.data.get("customer_id")
            check=next((e for e in rows("identity_verification_confirmed") if customer in e.entity_ids and
                        e.data.get("method") in {"physical_document","digital_document"}),None)
            if check is None:return ()
            used.extend((check,receipt))
        return tuple(used)
    return ()


def unattended_assets(rule, tx, events, index):
    """Interval intersection, explicit ALL-custodians absence, and per-asset policy.

    Ordinary/unknown bags are not relabelled cash bags. A laptop alert concerns
    physical custody only; open lid or unknown lock state never proves unlocked.
    Presence of ANY verified authorized custodian suppresses that subinterval.
    """
    required = rule["required_context_facts"]
    missing = [n for n in required if not index.fact(n, tx.transaction_id)]
    if not index.bounded(tx):missing.append("transaction_boundaries_unconfirmed")
    if missing:return [], ["missing_context:" + n for n in missing]
    rows = [e for e in events if e.transaction_id == tx.transaction_id and index.trusted(e)
            and tx.start_s <= e.start_s <= e.end_s <= tx.end_s]
    absences = [e for e in rows if e.event_type == "authorized_custodians_absent"]
    presences = [e for e in rows if e.event_type == "authorized_custodian_present"]
    alerts = []
    for asset in (e for e in rows if e.event_type == "asset_present"):
        if len(asset.entity_ids) != 1:continue
        identity = asset.entity_ids[0]
        kind = asset.data.get("asset_kind", "unknown")
        zone = asset.data.get("zone_id")
        if kind not in {"cash", "cash_bag", "stamp", "key", "laptop", "personal_bag"} or not zone:continue
        if not index.fact("asset_type_verified", identity) or not index.fact("custody_required", identity):continue
        if asset.data.get("protection_state") != "physically_accessible":continue
        for absence in absences:
            if absence.data.get("zone_id") != zone:continue
            left, right = max(asset.start_s, absence.start_s), min(asset.end_s, absence.end_s)
            if right <= left:continue
            spans = [(left, right)]
            for present in presences:
                if present.data.get("zone_id") != zone:continue
                remaining = []
                for a,b in spans:
                    if present.end_s <= a or present.start_s >= b:remaining.append((a,b))
                    else:
                        if a < present.start_s:remaining.append((a,present.start_s))
                        if present.end_s < b:remaining.append((present.end_s,b))
                spans = remaining
            for a,b in spans:
                if b-a <= rule.get("duration_threshold_s",0):continue
                if not index.covered("custody:" + zone,a,b) or not index.covered("asset:" + identity,a,b):continue
                policy_verified = index.fact("internal_custody_policy_verified",identity)
                alerts.append(dict(asset_id=identity,asset_kind=kind,start_s=a,end_s=b,duration_s=b-a,
                                   decision=Decision.CANDIDATE.value if policy_verified else Decision.UNKNOWN.value,
                                   alert_type="policy_unattended_asset_candidate" if policy_verified else "unattended_asset_observational_review",
                                   severity="severe" if kind in {"cash","cash_bag","stamp","key"} else "minor",
                                   severity_basis="project_risk_priority_not_statutory",
                                   source_limit="No statutory unattended-duration threshold asserted. Bank policy is required for a policy-violation candidate.",
                                   screen_lock_state=asset.data.get("screen_lock_state","unknown") if kind=="laptop" else "not_applicable",
                                   event_ids=[asset.event_id,absence.event_id],
                                   evidence=index.evidence_payload((asset,absence),tx.evidence_ids)))
    # Identical intervals from duplicate windows are one observational alert.
    unique = {}
    for a in alerts:
        key = (a["asset_id"],a["start_s"],a["end_s"],a["alert_type"])
        unique.setdefault(key,a)
    return list(unique.values()), ([] if alerts else ["unattended_interval_or_item_custody_requirement_not_established"])


def exit_door_assets(rule, tx, events, index):
    """User-confirmed project rule: actual room exit + protected items + open/
    independently verified unlocked door. Closed never implies locked/unlocked.
    The current detector has no door class; this accepts reviewed state evidence.
    """
    missing = [n for n in rule["required_context_facts"] if not index.fact(n,tx.transaction_id)]
    if not index.bounded(tx):missing.append("transaction_boundaries_unconfirmed")
    if missing:return [],False,["missing_context:"+n for n in missing]
    rows = [e for e in events if e.transaction_id==tx.transaction_id and index.trusted(e)
            and tx.start_s <= e.start_s <= e.end_s <= tx.end_s]
    exits = [e for e in rows if e.event_type=="room_exit" and all(index.fact("cashier_role_verified",p) for p in e.entity_ids)]
    assets = [e for e in rows if e.event_type=="asset_present" and len(e.entity_ids)==1
              and all(index.fact(n,e.entity_ids[0]) for n in ("asset_type_verified","protected_asset_verified","custody_required"))]
    absent = [e for e in rows if e.event_type=="authorized_custodians_absent"]
    present = [e for e in rows if e.event_type=="authorized_custodian_present"]
    doors = [e for e in rows if e.event_type=="door_state"]
    alerts=[];safe_checks=[]
    for leave in exits:
        zone=leave.data.get("zone_id")
        if not zone:continue
        for asset in assets:
            if asset.data.get("zone_id")!=zone or asset.end_s<=leave.end_s:continue
            target=asset.entity_ids[0]
            # To clear this bounded exit, the asset and reviewed protection/custody
            # evidence must span the complete remaining transaction interval.
            full_span=asset.start_s<=leave.end_s and asset.end_s>=tx.end_s
            full_coverage=(index.covered("room:"+zone,leave.start_s,tx.end_s) and
                           index.covered("asset:"+target,leave.end_s,tx.end_s) and
                           index.covered("door:"+zone,leave.end_s,tx.end_s) and
                           index.covered("custody:"+zone,leave.end_s,tx.end_s))
            guarded=any(p.data.get("zone_id")==zone and p.start_s<=leave.end_s and p.end_s>=tx.end_s for p in present)
            locked=any(d.data.get("zone_id")==zone and d.data.get("door_state")=="closed" and d.data.get("lock_state")=="verified_locked"
                       and d.start_s<=leave.end_s and d.end_s>=tx.end_s for d in doors)
            conflicting_open=any(d.data.get("zone_id")==zone and d.start_s<tx.end_s and d.end_s>leave.end_s and
                                 (d.data.get("door_state")=="open" or d.data.get("lock_state")=="verified_unlocked") for d in doors)
            safe_checks.append(full_span and full_coverage and (guarded or (locked and not conflicting_open)))
            if asset.data.get("protection_state")!="physically_accessible":continue
            for no_custodian in absent:
                if no_custodian.data.get("zone_id")!=zone:continue
                for door in doors:
                    if door.data.get("zone_id")!=zone:continue
                    state,lock=door.data.get("door_state","unknown"),door.data.get("lock_state","unknown")
                    if not (state=="open" or lock=="verified_unlocked") or (state=="open" and lock=="verified_locked"):continue
                    a=max(leave.end_s,asset.start_s,no_custodian.start_s,door.start_s)
                    b=min(asset.end_s,no_custodian.end_s,door.end_s,tx.end_s)
                    if b<=a:continue
                    spans=[(a,b)]
                    for custodian in present:
                        if custodian.data.get("zone_id")!=zone:continue
                        next_spans=[]
                        for left,right in spans:
                            if custodian.end_s<=left or custodian.start_s>=right:next_spans.append((left,right))
                            else:
                                if left<custodian.start_s:next_spans.append((left,custodian.start_s))
                                if custodian.end_s<right:next_spans.append((custodian.end_s,right))
                        spans=next_spans
                    for left,right in spans:
                        if right-left<=rule.get("duration_threshold_s",0):continue
                        if not (index.covered("room:"+zone,leave.start_s,right) and
                                index.covered("asset:"+target,left,right) and index.covered("door:"+zone,left,right) and
                                index.covered("custody:"+zone,left,right)):continue
                        # Contradictory reviewed locked-door state blocks a claim.
                        if any(d.data.get("zone_id")==zone and d.data.get("door_state")=="closed" and
                               d.data.get("lock_state")=="verified_locked" and d.start_s<right and d.end_s>left for d in doors):continue
                        chain=(leave,asset,no_custodian,door)
                        alerts.append(dict(asset_id=target,asset_kind=asset.data.get("asset_kind","unknown"),
                                           start_s=left,end_s=right,duration_s=right-left,door_state=state,lock_state=lock,
                                           decision=Decision.CANDIDATE.value,basis="user_workflow_policy",severity=rule["severity"],
                                           reason="Confirmed room exit; protected asset remains accessible; all authorized custodians absent; door open or independently verified unlocked.",
                                           event_ids=[e.event_id for e in chain],evidence=index.evidence_payload(chain,tx.evidence_ids)))
    unique={(a["asset_id"],a["start_s"],a["end_s"],a["door_state"],a["lock_state"]):a for a in alerts}
    return list(unique.values()),bool(safe_checks) and all(safe_checks),([] if alerts else ["exit_door_asset_chain_not_established; closed_does_not_prove_locked"])


def validate_catalog(catalog):
    if catalog.get("schema_version") != "cashrules.catalog.v1" or not isinstance(catalog.get("rules"), list):
        raise ContractError("Invalid rule catalog")
    ids = set()
    engines = {"machine_coverage", "leave_during_service", "customer_absent_during_count", "prior_cash_exposed",
               "controller_absent", "unescorted_vault_entry", "bag_inspection_omitted", "damaged_bag_without_collector",
               "personal_item_in_cash_node", "cassette_unprotected", "defective_transport_acceptance",
               "sender_absent_during_packing", "third_party_loading_exposure", "deposit_identity_verification", "regulated_equipment", "unattended_assets", "exit_door_assets", "deferred"}
    for rule in catalog["rules"]:
        required = {"rule_id", "title", "basis", "enabled", "verification_status", "severity", "severity_basis",
                    "engine", "sources", "applicability_assumptions", "required_observable_events", "required_context_facts"}
        if not required <= set(rule) or rule["rule_id"] in ids:raise ContractError("Invalid/duplicate rule")
        ids.add(rule["rule_id"])
        if rule["engine"] not in engines or rule["severity"] not in {"severe", "minor"} or type(rule["enabled"]) is not bool:
            raise ContractError("Invalid rule engine/severity/enabled")
        if rule["basis"] not in {"user_workflow_policy", "regulatory", "internal_policy"}:raise ContractError("Invalid rule basis")
        if rule["enabled"] and rule["engine"] == "deferred":raise ContractError("Deferred engine cannot be enabled")
        if "duration_threshold_s" in rule:
            from .contracts import number
            number(rule["duration_threshold_s"],"project/bank-policy duration threshold")
        if rule["enabled"] and rule["basis"] == "regulatory":
            if rule["verification_status"] != "verified_primary_source" or not rule["sources"]:
                raise ContractError("Unverified legal rule cannot be enabled")
            for s in rule["sources"]:
                if not all(s.get(k) for k in ("act", "paragraph", "url", "verified_at_utc", "verification_evidence")):
                    raise ContractError("Incomplete legal verification record")
                if not s["url"].startswith(("https://adilet.zan.kz/", "https://www.adilet.zan.kz/", "https://nationalbank.kz/", "https://www.nationalbank.kz/")):
                    raise ContractError("Official legal primary URL required")
    return catalog


def evaluate(sequence, catalog):
    seq = sequence if isinstance(sequence, Sequence) else Sequence.parse(sequence)
    validate_catalog(catalog)
    events, duplicates = deduplicate(seq.events)
    index = EvidenceIndex(seq)
    results, ledgers = [], {}
    for rule in catalog["rules"]:
        scopes = seq.transactions or (None,)
        for tx in scopes:
            tid = tx.transaction_id if tx else None
            result = dict(rule_id=rule["rule_id"], title=rule["title"], transaction_id=tid,
                          decision=Decision.UNKNOWN.value, severity=rule["severity"], severity_basis=rule["severity_basis"],
                          basis=rule["basis"], verification_status=rule["verification_status"], enabled=rule["enabled"],
                          sources=rule["sources"], applicability_assumptions=rule["applicability_assumptions"],
                          reasons=[], event_ids=[], evidence=[], scope="supplied transaction only; never a legal determination")
            if not rule["enabled"]:
                result["reasons"] = ["rule_disabled_pending_verification_or_external_data"]
            elif tx is None:
                result["reasons"] = ["no_transaction_scope"]
            elif rule["basis"] == "regulatory" and (seq.operation_date is None or
                    not catalog.get("verified_operation_date_start", "9999-12-31") <= seq.operation_date <= catalog.get("legal_as_of", "0001-01-01")):
                result["reasons"] = ["operation_date_missing_or_outside_verified_legal_version_interval"]
            elif rule["engine"] == "exit_door_assets":
                alerts,safe,reasons=exit_door_assets(rule,tx,events,index)
                result["security_candidates"]=alerts
                result["event_ids"]=sorted({i for a in alerts for i in a["event_ids"]})
                if alerts:result["decision"]=Decision.CANDIDATE.value
                elif safe:result["decision"]=Decision.CLEAR.value
                else:result["reasons"]=reasons
                result["evidence"]=index.evidence_payload([e for e in events if e.event_id in result["event_ids"]],tx.evidence_ids)
                result["perception_gap"]="No configured door detector; requires reviewed door/lock state evidence and camera/room calibration."
                if safe and not alerts:
                    chosen=[e for e in events if e.transaction_id==tid and e.event_type in {"room_exit","asset_present","door_state","authorized_custodian_present"} and index.trusted(e)]
                    result["event_ids"]=sorted(e.event_id for e in chosen)
                    result["evidence"]=index.evidence_payload(chosen,tx.evidence_ids)
            elif rule["engine"] == "unattended_assets":
                alerts,reasons = unattended_assets(rule,tx,events,index)
                result["observational_alerts"] = alerts
                result["event_ids"] = sorted({i for a in alerts for i in a["event_ids"]})
                if any(a["decision"] == Decision.CANDIDATE for a in alerts):result["decision"] = Decision.CANDIDATE.value
                result["reasons"] = reasons or (["bank_policy_not_verified; physical observation only"] if alerts else [])
                result["evidence"] = index.evidence_payload([e for e in events if e.event_id in result["event_ids"]],tx.evidence_ids)
            elif rule["engine"] in {"machine_coverage", "regulated_equipment"}:
                decision, reasons, ledger = assess_machine_coverage(tx, events, index)
                if rule["engine"] == "regulated_equipment":
                    missing = [n for n in rule["required_context_facts"] if not index.fact(n, tid)]
                    if rule.get("transaction_kinds") and tx.kind not in rule["transaction_kinds"]:missing.append("regulated_workflow_kind")
                    if missing:
                        decision = Decision.UNKNOWN;reasons.extend("missing_context:" + n for n in missing)
                    elif decision == Decision.CLEAR and not index.fact("qualifying_equipment_confirmed", tid):
                        decision = Decision.UNKNOWN;reasons.append("two_security_feature_equipment_capability_unconfirmed")
                result.update(decision=decision.value, reasons=reasons, cash_coverage=ledger["lots"])
                ledgers[tid] = ledger
                ids = set(ledger["accepted_event_ids"])
                chosen = [e for e in events if e.event_id in ids]
                result["event_ids"] = sorted(ids)
                extra_ids = set(tx.evidence_ids)
                extra_ids.update(i for c in seq.coverage for i in c.evidence_ids)
                extra_ids.update(i for f in seq.facts for i in f.evidence_ids)
                result["evidence"] = index.evidence_payload(chosen, extra_ids)
            else:
                groups, reasons = pattern_candidates(rule, tx, events, index)
                if groups:
                    chosen = {e.event_id:e for g in groups for e in g}
                    result.update(decision=Decision.CANDIDATE.value, event_ids=sorted(chosen),
                                  evidence=index.evidence_payload(chosen.values(), tx.evidence_ids))
                else:
                    compliant = compliant_pattern(rule, tx, events, index)
                    if compliant:
                        result.update(decision=Decision.CLEAR.value, event_ids=sorted({e.event_id for e in compliant}),
                                      evidence=index.evidence_payload(compliant, tx.evidence_ids))
                    else:result["reasons"] = reasons
                # Include reviewed applicability and coverage provenance used by gates.
                if result["decision"] != Decision.UNKNOWN:
                    extra = set(tx.evidence_ids)
                    extra.update(i for f in seq.facts if f.target == tid or f.target in {p for e in events for p in e.entity_ids} for i in f.evidence_ids)
                    extra.update(i for c in seq.coverage for i in c.evidence_ids)
                    cited = {p["evidence_id"] for p in result["evidence"]}
                    result["evidence"].extend(index.evidence_payload(ids=extra - cited))
                # Legal pattern rules currently abstain on missing patterns: lack
                # of a detected violation is not proof the required check happened.
            if tx is not None and result["decision"]!=Decision.UNKNOWN:
                context_ids={i for f in seq.facts for i in f.evidence_ids}|{i for c in seq.coverage for i in c.evidence_ids}|set(tx.evidence_ids)
                cited={p["evidence_id"] for p in result["evidence"]}
                result["evidence"].extend(index.evidence_payload(ids=context_ids-cited))
            result["observed_events"]=[asdict(e) for e in events if e.event_id in set(result["event_ids"])]
            results.append(result)
    decisions = [r["decision"] for r in results if r["enabled"]]
    overall = (Decision.CANDIDATE if Decision.CANDIDATE in decisions else
               Decision.CLEAR if decisions and all(d == Decision.CLEAR for d in decisions) else Decision.UNKNOWN)
    return dict(schema_version="cashrules.result.v1", synthetic=seq.synthetic, video_sha256=seq.video_sha256,
                sequence_sha256=canonical_hash(seq.to_dict()), catalog_sha256=canonical_hash(catalog),
                decision=overall.value, decision_scope="enabled rules only; disabled legal rules remain unevaluated",
                results=results, cash_ledgers=ledgers, deduplication=duplicates,
                limitations=["Human review required; project severity is not statutory grading.",
                             "No detection/annotation absence is treated as a missing action.",
                             "Static machine/laptop regions do not establish activity or completion."])
