from copy import deepcopy
from pathlib import Path
import json
import tempfile
import unittest

from app.cashrules import Sequence, ContractError, evaluate
from app.cashrules.engine import validate_catalog
from app.cashrules.annotations import import_annotations, annotation_audit
from app.cashrules.contracts import strict_load
from app.cashrules.qwen_bridge import candidate_sequence
from tests.rules.helpers import sequence, catalog, event, count, bypass, coverage, fact, contexts

VIOLATION = "supported_violation_candidate"
CLEAR = "no_violation_observed"
UNKNOWN = "insufficient_evidence"


class MachineRules(unittest.TestCase):
    def run_case(self, d):return evaluate(d, catalog("WF-MACHINE-ALL"))
    def test_recount_A_never_covers_C(self):
        d = sequence();count(d,"A","A1");count(d,"A","A2",25);count(d,"B","B1",40);bypass(d)
        r = self.run_case(d)
        self.assertEqual(r["decision"],VIOLATION)
        self.assertEqual(len(r["cash_ledgers"]["T"]["sessions"]),3)
        self.assertEqual(r["cash_ledgers"]["T"]["lots"]["C"]["coverage_state"],"supported_bypass")
    def test_all_lots_completed_positive_compliance(self):
        d=sequence();count(d,"A","A");count(d,"B","B",25);count(d,"C","C",40)
        self.assertEqual(self.run_case(d)["decision"],CLEAR)
    def test_unobserved_C_not_a_violation(self):
        d=sequence();count(d,"A","A");count(d,"A","AA",25);count(d,"B","B",40)
        self.assertEqual(self.run_case(d)["decision"],UNKNOWN)
    def test_loading_retrieval_without_completion(self):
        d=sequence(("A",));count(d,"A","A",complete=False)
        self.assertEqual(self.run_case(d)["decision"],UNKNOWN)
    def test_partial_feed_never_full_credit(self):
        d=sequence(("A",));count(d,"A","A",full=False);count(d,"A","AA",30,full=False)
        r=self.run_case(d);self.assertEqual(r["decision"],UNKNOWN)
        self.assertEqual(r["cash_ledgers"]["T"]["lots"]["A"]["coverage_state"],"partial_or_unresolved_count")
    def test_completion_without_loading_is_unknown(self):
        d=sequence(("A",));event(d,"completion_evidence",start=20,end=21,sid="S",machine="M",data=dict(full_lot=True,completion_cue="end"))
        self.assertEqual(self.run_case(d)["decision"],UNKNOWN)
    def test_ambiguous_identity_cannot_count(self):
        d=sequence(("A",));count(d,"A","A")
        for e in d["events"]:e["identity"]="hypothesis"
        self.assertEqual(self.run_case(d)["decision"],UNKNOWN)
    def test_occlusion_vetoes_bypass(self):
        d=sequence(("C",));bypass(d);event(d,"occlusion",("C",),20,30)
        self.assertEqual(self.run_case(d)["decision"],UNKNOWN)
    def test_missing_camera_coverage_vetoes_bypass(self):
        d=sequence(("C",));bypass(d);coverage(d,"cash_path:C",30,40,"gap")
        self.assertEqual(self.run_case(d)["decision"],UNKNOWN)
    def test_missing_transaction_end(self):
        d=sequence(("C",));bypass(d);d["transactions"][0]["end_s"]=None
        self.assertEqual(self.run_case(d)["decision"],UNKNOWN)
    def test_unknown_inventory(self):
        d=sequence(("A",));count(d,"A","A");d["transactions"][0]["inventory_complete"]=False
        self.assertEqual(self.run_case(d)["decision"],UNKNOWN)
    def test_unreviewed_model_cannot_confirm_completion(self):
        d=sequence(("A",));count(d,"A","A")
        for p in d["evidence"]:
            if p["evidence_id"].startswith("proof:"):p.update(source_type="model",review_status="candidate")
        self.assertEqual(self.run_case(d)["decision"],UNKNOWN)
    def test_session_bound_to_two_lots_is_unknown(self):
        d=sequence(("A","B"));count(d,"A","same");count(d,"B","same",30)
        r=self.run_case(d);self.assertEqual(r["decision"],UNKNOWN)
        self.assertEqual(r["cash_ledgers"]["T"]["sessions"][0]["state"],"unresolved")
    def test_session_bound_to_unknown_machine_is_unknown(self):
        d=sequence(("A",));count(d,"A","A")
        for e in d["events"]:e["machine_id"]="unmapped-machine"
        self.assertEqual(self.run_case(d)["decision"],UNKNOWN)
    def test_interrupted_count(self):
        d=sequence(("A",));count(d,"A","S");event(d,"machine_interruption",start=14,end=15,sid="S",machine="M")
        self.assertEqual(self.run_case(d)["decision"],UNKNOWN)
    def test_wrong_temporal_order(self):
        d=sequence(("A",));count(d,"A","S")
        for e in d["events"]:
            if e["event_type"]=="machine_loading":
                e["start_s"],e["end_s"]=50,51
                p=next(p for p in d["evidence"] if p["evidence_id"]==e["evidence_ids"][0]);p["refs"]=[dict(frame_ref="late-loading",timestamp_s=50),dict(frame_ref="late-loaded",timestamp_s=51)]
        self.assertEqual(self.run_case(d)["decision"],UNKNOWN)
    def test_duplicate_events_do_not_create_sessions(self):
        d=sequence(("A",));count(d,"A","A")
        e=deepcopy(d["events"][1]);e["event_id"]="duplicate-window";d["events"].append(e)
        r=self.run_case(d);self.assertEqual(r["decision"],CLEAR)
        self.assertEqual(len(r["cash_ledgers"]["T"]["sessions"]),1)
        self.assertEqual(len(r["deduplication"]),1)
    def test_input_order_invariant(self):
        d=sequence(("A",));count(d,"A","A");r=self.run_case(d)
        d["events"].reverse();self.assertEqual(r["cash_ledgers"],self.run_case(d)["cash_ledgers"])
    def test_split_both_children_counted_covers_parent(self):
        d=sequence(("A",));event(d,"split",("A",),1,2,data=dict(child_lot_ids=["A1","A2"],lineage_complete=True))
        count(d,"A1","S1",10);count(d,"A2","S2",30)
        self.assertEqual(self.run_case(d)["decision"],CLEAR)
    def test_only_one_split_child_is_partial(self):
        d=sequence(("A",));event(d,"split",("A",),1,2,data=dict(child_lot_ids=["A1","A2"],lineage_complete=True));count(d,"A1","S1",10)
        self.assertEqual(self.run_case(d)["decision"],UNKNOWN)
    def test_uncertain_split_no_inherited_credit(self):
        d=sequence(("A",));event(d,"split",("A",),1,2,data=dict(child_lot_ids=["A1","A2"],lineage_complete=False));count(d,"A1","S1",10);count(d,"A2","S2",30)
        self.assertEqual(self.run_case(d)["decision"],UNKNOWN)
    def test_full_count_before_verified_split_covers_children(self):
        d=sequence(("A",));count(d,"A","S",5);event(d,"split",("A",),30,31,data=dict(child_lot_ids=["A1","A2"],lineage_complete=True))
        r=self.run_case(d);self.assertEqual(r["decision"],CLEAR)
        self.assertEqual(r["cash_ledgers"]["T"]["lots"]["A1"]["coverage_state"],"full_count_coverage")
    def test_count_verified_merged_lot_covers_both_parents(self):
        d=sequence(("A","B"));event(d,"merge",("A","B"),1,2,data=dict(child_lot_ids=["D"],lineage_complete=True));count(d,"D","S",10)
        self.assertEqual(self.run_case(d)["decision"],CLEAR)
    def test_merge_one_counted_parent_not_full(self):
        d=sequence(("A","B"));count(d,"A","S",5);event(d,"merge",("A","B"),30,31,data=dict(child_lot_ids=["D"],lineage_complete=True))
        self.assertEqual(self.run_case(d)["decision"],UNKNOWN)
    def test_split_merge_subset_does_not_cover_other_child(self):
        d=sequence(("A","B"));event(d,"split",("A",),1,2,data=dict(child_lot_ids=["A1","A2"],lineage_complete=True))
        event(d,"merge",("A1","B"),3,4,data=dict(child_lot_ids=["D"],lineage_complete=True));count(d,"D","S",10)
        r=self.run_case(d);self.assertEqual(r["decision"],UNKNOWN)
        self.assertNotEqual(r["cash_ledgers"]["T"]["lots"]["A2"]["coverage_state"],"full_count_coverage")
    def test_bypassed_child_supports_partial_lot_omission(self):
        d=sequence(("A",));event(d,"split",("A",),1,2,data=dict(child_lot_ids=["A1","A2"],lineage_complete=True));count(d,"A1","S",10);bypass(d,"A2")
        self.assertEqual(self.run_case(d)["decision"],VIOLATION)
    def test_parent_bypass_conflicts_with_counted_child(self):
        d=sequence(("A",));bypass(d,"A",start=1);event(d,"split",("A",),10,11,data=dict(child_lot_ids=["A1","A2"],lineage_complete=True));count(d,"A1","S",20)
        self.assertEqual(self.run_case(d)["decision"],UNKNOWN)
    def test_lineage_cycle_rejected_as_uncertainty(self):
        d=sequence(("A",));event(d,"split",("A",),1,2,data=dict(child_lot_ids=["A","B"],lineage_complete=True));count(d,"A","S",10)
        self.assertEqual(self.run_case(d)["decision"],UNKNOWN)
    def test_completion_and_bypass_contradiction(self):
        d=sequence(("A",));count(d,"A","S");bypass(d,"A")
        self.assertEqual(self.run_case(d)["decision"],UNKNOWN)
    def test_hopper_and_output_track_hypotheses_do_not_create_lots(self):
        d=sequence(("A",));count(d,"A","S");event(d,"object_visible",("hopper-track","output-track"),20,21,identity="hypothesis")
        r=self.run_case(d);self.assertEqual(r["decision"],CLEAR);self.assertEqual(set(r["cash_ledgers"]["T"]["lots"]),{"A"})
    def test_no_transaction_is_insufficient(self):
        d=sequence();d["transactions"]=[];d["facts"]=[]
        self.assertEqual(self.run_case(d)["decision"],UNKNOWN)
    def test_static_region_presence_is_not_completion(self):
        d=sequence(("A",));event(d,"object_visible",data=dict(zone_id="machine_output"))
        self.assertEqual(self.run_case(d)["decision"],UNKNOWN)


class LegalRules(unittest.TestCase):
    def controller(self, absence=True):
        d=sequence(("A",),kind="back_office");contexts(d,"KZ-CASH-02");coverage(d,"room:T")
        event(d,"backoffice_process",start=20,end=40,data=dict(process="recount"))
        event(d,"controller_absent" if absence else "controller_present",("role:controller",),10,50)
        return d
    def test_controller_absence_positive(self):
        self.assertEqual(evaluate(self.controller(),catalog("KZ-CASH-02"))["decision"],VIOLATION)
    def test_controller_present_negative_control(self):
        self.assertEqual(evaluate(self.controller(False),catalog("KZ-CASH-02"))["decision"],CLEAR)
    def test_customer_transaction_exception(self):
        d=self.controller();d["transactions"][0]["kind"]="receipt"
        self.assertEqual(evaluate(d,catalog("KZ-CASH-02"))["decision"],UNKNOWN)
    def test_lone_visible_cashier_not_controller_absence(self):
        d=self.controller();d["events"]=[e for e in d["events"] if e["event_type"]!="controller_absent"]
        self.assertEqual(evaluate(d,catalog("KZ-CASH-02"))["decision"],UNKNOWN)
    def test_unknown_role_abstains(self):
        d=self.controller();d["facts"]=[f for f in d["facts"] if f["name"]!="staff_roles_verified"]
        self.assertEqual(evaluate(d,catalog("KZ-CASH-02"))["decision"],UNKNOWN)
    def test_conflicting_presence_abstains(self):
        d=self.controller();event(d,"controller_present",("other-track",),25,30)
        self.assertEqual(evaluate(d,catalog("KZ-CASH-02"))["decision"],UNKNOWN)
    def test_missing_legal_operation_date(self):
        d=self.controller();d.pop("operation_date")
        self.assertEqual(evaluate(d,catalog("KZ-CASH-02"))["decision"],UNKNOWN)
    def test_old_video_not_mapped_to_current_law(self):
        d=self.controller();d["operation_date"]="2018-01-01"
        self.assertEqual(evaluate(d,catalog("KZ-CASH-02"))["decision"],UNKNOWN)
    def test_future_video_requires_legal_refresh(self):
        d=self.controller();d["operation_date"]="2027-01-02"
        self.assertEqual(evaluate(d,catalog("KZ-CASH-02"))["decision"],UNKNOWN)
    def test_vault_role_and_escort_gates(self):
        d=sequence();contexts(d,"KZ-CASH-04");coverage(d,"access:T");fact(d,"entrant_requires_escort","P")
        event(d,"vault_entry",("P",),20,21);event(d,"escort_absent",("role:escort",),10,30)
        self.assertEqual(evaluate(d,catalog("KZ-CASH-04"))["decision"],VIOLATION)
        d["facts"]=[f for f in d["facts"] if f["name"]!="entrant_requires_escort"]
        self.assertEqual(evaluate(d,catalog("KZ-CASH-04"))["decision"],UNKNOWN)
    def test_responsible_person_can_enter_alone(self):
        d=sequence();contexts(d,"KZ-CASH-04");coverage(d,"access:T");fact(d,"entrant_requires_escort","P",False)
        event(d,"vault_entry",("P",),20,21)
        self.assertEqual(evaluate(d,catalog("KZ-CASH-04"))["decision"],CLEAR)
    def test_bag_defect_repaired_before_acceptance_suppresses(self):
        d=sequence();contexts(d,"KZ-CASH-08");coverage(d,"package:bag1")
        event(d,"transport_package_defect",("bag1",),10,11);event(d,"transport_acceptance",("bag1",),30,31)
        self.assertEqual(evaluate(d,catalog("KZ-CASH-08"))["decision"],VIOLATION)
        event(d,"defect_repaired",("bag1",),20,21)
        self.assertEqual(evaluate(d,catalog("KZ-CASH-08"))["decision"],UNKNOWN)
    def test_cassette_in_progress_is_not_ready(self):
        d=sequence();contexts(d,"KZ-CASH-03");coverage(d,"cassette:C1")
        event(d,"cassette_unsealed",("C1",),10,40);event(d,"cassette_transfer",("C1",),30,31)
        self.assertEqual(evaluate(d,catalog("KZ-CASH-03"))["decision"],UNKNOWN)
        event(d,"cassette_ready",("C1",),1,2)
        self.assertEqual(evaluate(d,catalog("KZ-CASH-03"))["decision"],VIOLATION)
    def test_personal_item_ownership_required(self):
        d=sequence();contexts(d,"KZ-CASH-07");coverage(d,"cash_node:T");event(d,"personal_item_present",("bag1",),20,30,data=dict(storage_confirmed=True))
        self.assertEqual(evaluate(d,catalog("KZ-CASH-07"))["decision"],UNKNOWN)
        fact(d,"personal_ownership_and_no_exception","bag1")
        self.assertEqual(evaluate(d,catalog("KZ-CASH-07"))["decision"],VIOLATION)
    def test_personal_item_presence_not_storage(self):
        d=sequence();contexts(d,"KZ-CASH-07");coverage(d,"cash_node:T");fact(d,"personal_ownership_and_no_exception","bag1")
        event(d,"personal_item_present",("bag1",),20,30)
        self.assertEqual(evaluate(d,catalog("KZ-CASH-07"))["decision"],UNKNOWN)
    def test_sender_absence_positive_and_presence_negative(self):
        d=sequence();contexts(d,"KZ-CASH-09");coverage(d,"room:T")
        event(d,"transport_packing",("bag1",),20,30);event(d,"sender_absent",("sender",),10,40)
        self.assertEqual(evaluate(d,catalog("KZ-CASH-09"))["decision"],VIOLATION)
        d["events"][-1]["event_type"]="sender_present"
        self.assertEqual(evaluate(d,catalog("KZ-CASH-09"))["decision"],CLEAR)
    def test_loading_public_sightline_needs_verified_geometry(self):
        d=sequence();contexts(d,"KZ-CASH-11");coverage(d,"loading_area:T");fact(d,"verified_third_party","P")
        event(d,"loading_activity",("package",),10,40);event(d,"third_party_view",("P",),20,30)
        self.assertEqual(evaluate(d,catalog("KZ-CASH-11"))["decision"],UNKNOWN)
        fact(d,"public_sightline_verified")
        self.assertEqual(evaluate(d,catalog("KZ-CASH-11"))["decision"],VIOLATION)
    def test_regulatory_machine_rule_not_blanket_manual_handling(self):
        d=sequence(("A",),kind="back_office");bypass(d,"A")
        self.assertEqual(evaluate(d,catalog("KZ-CASH-01"))["decision"],UNKNOWN)
        contexts(d,"KZ-CASH-01")
        self.assertEqual(evaluate(d,catalog("KZ-CASH-01"))["decision"],VIOLATION)
    def test_machine_capabilities_not_inferred(self):
        d=sequence(("A",),kind="back_office");contexts(d,"KZ-CASH-01");count(d,"A","A")
        self.assertEqual(evaluate(d,catalog("KZ-CASH-01"))["decision"],UNKNOWN)
        fact(d,"qualifying_equipment_confirmed")
        self.assertEqual(evaluate(d,catalog("KZ-CASH-01"))["decision"],CLEAR)
    def test_unverified_rule_cannot_be_enabled(self):
        c=catalog("DRAFT-231-20");c["rules"][0]["enabled"]=True
        with self.assertRaises(ContractError):validate_catalog(c)
    def test_records_based_rule_remains_disabled(self):
        d=sequence();event(d,"object_handover",("A",),10,11)
        r=evaluate(d,catalog("KZ-CASH-05"));self.assertEqual(r["decision"],UNKNOWN)
        self.assertFalse(r["results"][0]["enabled"])
    def test_catalog_all_severity_is_project_priority(self):
        for r in catalog()["rules"]:self.assertEqual(r["severity_basis"],"project_risk_priority_not_statutory")
    def test_every_candidate_carries_legal_and_timestamp_evidence(self):
        r=evaluate(self.controller(),catalog("KZ-CASH-02"))["results"][0]
        self.assertTrue(r["sources"][0]["paragraph"]);self.assertTrue(r["sources"][0]["url"].startswith("https://adilet"))
        self.assertTrue(r["evidence"]);self.assertTrue(all(p["refs"] for p in r["evidence"]))


class Contracts(unittest.TestCase):
    def test_model_cannot_mark_itself_confirmed(self):
        d=sequence();d["evidence"][0]["source_type"]="model"
        with self.assertRaises(ContractError):Sequence.parse(d)
    def test_synthetic_evidence_not_real_video(self):
        d=sequence();d["synthetic"]=False
        with self.assertRaises(ContractError):Sequence.parse(d)
    def test_dangling_evidence(self):
        d=sequence();d["coverage"][0]["evidence_ids"]=["missing"]
        with self.assertRaises(ContractError):Sequence.parse(d)
    def test_false_coverage_span_rejected(self):
        d=sequence();d["coverage"][0]["end_s"]=101
        with self.assertRaises(ContractError):Sequence.parse(d)
    def test_duplicate_event_id_rejected(self):
        d=sequence();e=event(d,"object_visible");d["events"].append(deepcopy(e))
        with self.assertRaises(ContractError):Sequence.parse(d)
    def test_missing_completion_cue_rejected(self):
        d=sequence(("A",));count(d,"A","A");d["events"][2]["data"]["completion_cue"]=""
        with self.assertRaises(ContractError):Sequence.parse(d)
    def test_nan_and_boolean_timestamps_rejected(self):
        for bad in (float("nan"),float("inf"),True):
            d=sequence();d["transactions"][0]["end_s"]=bad
            with self.assertRaises(ContractError):Sequence.parse(d)
    def test_duplicate_json_key_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/"bad.json";p.write_text('{"x":1,"x":2}')
            with self.assertRaises(ContractError):strict_load(p)
    def test_conflicting_frame_times_rejected(self):
        d=sequence();e=event(d,"object_visible")
        d["evidence"][-1]["refs"][0]["frame_ref"]="scope:0"
        with self.assertRaises(ContractError):Sequence.parse(d)


class AnnotationImport(unittest.TestCase):
    def fixture(self):
        return dict(format="fortebank-normalized-events",status="not_ground_truth",videos=[{}],rule_candidates=[],events=[
            dict(event_id="obs1",video_sha256="b"*64,event_type="person_leaving",anchor_s=5,start_s=None,end_s=None,
                 timeline_role="structural_starting_point",source_clip_ids=["test-only"],timing_status="point_only",
                 source_annotation_refs=["/clips/0/event/0"],adjudication_status="not_adjudicated",
                 evaluation_metadata={"verdict":"DO_NOT_COPY"},original_observation="DO_NOT_COPY")])
    def test_import_does_not_copy_verdict_or_prose(self):
        rows=import_annotations(self.fixture(),"source.json","c"*64)
        self.assertNotIn("DO_NOT_COPY",json.dumps(rows))
        self.assertEqual(rows[0]["transactions"],[])
        self.assertEqual(rows[0]["evidence"][0]["review_status"],"candidate")
    def test_annotation_point_does_not_invent_interval(self):
        rows=import_annotations(self.fixture(),"source.json","c"*64)
        e=rows[0]["events"][0];self.assertEqual(e["start_s"],e["end_s"])
    def test_annotation_import_cannot_issue_violation(self):
        row=import_annotations(self.fixture(),"source.json","c"*64)[0]
        self.assertEqual(evaluate(row,catalog())["decision"],UNKNOWN)
    def test_audit_pointers_are_not_findings(self):
        audit=annotation_audit(self.fixture(),"source.json","c"*64)
        self.assertFalse(audit["supplied_verdicts_used"]);self.assertFalse(audit["media_review_performed"])


class AssetRules(unittest.TestCase):
    def fixture(self, kind="cash", policy=False):
        d=sequence(("A",));contexts(d,"INTERNAL-UNATTENDED-ASSETS")
        for name in ("asset_type_verified","custody_required"):fact(d,name,"item")
        if policy:fact(d,"internal_custody_policy_verified","item")
        coverage(d,"custody:room");coverage(d,"asset:item")
        event(d,"asset_present",("item",),0,100,data=dict(asset_kind=kind,zone_id="room",protection_state="physically_accessible",screen_lock_state="unknown"))
        event(d,"authorized_custodians_absent",("custody:room",),20,80,data=dict(zone_id="room"))
        return d
    def result(self,d):return evaluate(d,catalog("INTERNAL-UNATTENDED-ASSETS"))
    def test_no_bank_policy_only_observational(self):
        r=self.result(self.fixture());self.assertEqual(r["decision"],UNKNOWN)
        self.assertEqual(r["results"][0]["observational_alerts"][0]["alert_type"],"unattended_asset_observational_review")
    def test_reviewed_bank_policy_candidate(self):self.assertEqual(self.result(self.fixture(policy=True))["decision"],VIOLATION)
    def test_empty_chair_but_other_authorized_custodian(self):
        d=self.fixture(policy=True);event(d,"authorized_custodian_present",("other_authorized",),0,100,data=dict(zone_id="room"))
        self.assertEqual(self.result(d)["decision"],UNKNOWN);self.assertFalse(self.result(d)["results"][0]["observational_alerts"])
    def test_offcamera_custody_gap_abstains(self):
        d=self.fixture(policy=True);coverage(d,"custody:room",30,40,"gap")
        self.assertFalse(self.result(d)["results"][0]["observational_alerts"])
    def test_personal_bag_not_assumed_cash_bag(self):
        d=self.fixture(kind="personal_bag");d["facts"]=[f for f in d["facts"] if f["name"]!="custody_required"]
        self.assertFalse(self.result(d)["results"][0]["observational_alerts"])
        d=self.fixture(kind="cash_bag");self.assertEqual(self.result(d)["results"][0]["observational_alerts"][0]["severity"],"severe")
    def test_unknown_bag_contents_abstain(self):
        self.assertFalse(self.result(self.fixture(kind="unknown_bag"))["results"][0]["observational_alerts"])
    def test_laptop_lock_unknown_not_unlocked_claim(self):
        r=self.result(self.fixture(kind="laptop"));alert=r["results"][0]["observational_alerts"][0]
        self.assertEqual(r["decision"],UNKNOWN);self.assertEqual(alert["screen_lock_state"],"unknown")
    def test_custodian_return_closes_interval(self):
        d=self.fixture();event(d,"authorized_custodian_present",("returned",),50,100,data=dict(zone_id="room"))
        alert=self.result(d)["results"][0]["observational_alerts"][0];self.assertEqual((alert["start_s"],alert["end_s"]),(20,50))


class DoorRules(unittest.TestCase):
    def fixture(self,state="open",lock="unknown"):
        d=sequence(("A",));contexts(d,"WF-EXIT-DOOR-ASSETS");fact(d,"cashier_role_verified","cashier")
        for name in ("asset_type_verified","protected_asset_verified","custody_required"):fact(d,name,"item")
        for topic in ("room:R","door:R","custody:R","asset:item"):coverage(d,topic)
        event(d,"room_exit",("cashier",),9,10,data=dict(zone_id="R"))
        event(d,"asset_present",("item",),0,100,data=dict(zone_id="R",asset_kind="cash_bag",protection_state="physically_accessible"))
        event(d,"authorized_custodians_absent",("all_custodians",),10,100,data=dict(zone_id="R"))
        event(d,"door_state",("door",),10,100,data=dict(zone_id="R",door_state=state,lock_state=lock))
        return d
    def result(self,d):return evaluate(d,catalog("WF-EXIT-DOOR-ASSETS"))
    def test_exit_open_door_protected_item_positive(self):
        r=self.result(self.fixture());self.assertEqual(r["decision"],VIOLATION)
        chain=r["results"][0]["security_candidates"][0];self.assertEqual(len(chain["event_ids"]),4)
        self.assertEqual(chain["basis"],"user_workflow_policy")
    def test_closed_independently_unlocked_positive(self):self.assertEqual(self.result(self.fixture("closed","verified_unlocked"))["decision"],VIOLATION)
    def test_closed_unknown_lock_is_unknown(self):self.assertEqual(self.result(self.fixture("closed","unknown"))["decision"],UNKNOWN)
    def test_closed_locked_negative_control(self):self.assertEqual(self.result(self.fixture("closed","verified_locked"))["decision"],CLEAR)
    def test_leaving_frame_not_confirmed_room_exit(self):
        d=self.fixture();d["events"][0]["event_type"]="person_leaving"
        self.assertEqual(self.result(d)["decision"],UNKNOWN)
    def test_other_authorized_custodian_negative_control(self):
        d=self.fixture();event(d,"authorized_custodian_present",("other",),10,100,data=dict(zone_id="R"))
        self.assertEqual(self.result(d)["decision"],CLEAR)
    def test_returned_custodian_ends_security_exposure(self):
        d=self.fixture();event(d,"authorized_custodian_present",("returned",),50,100,data=dict(zone_id="R"))
        r=self.result(d);self.assertEqual(r["decision"],VIOLATION)
        self.assertEqual(r["results"][0]["security_candidates"][0]["end_s"],50)
    def test_offcamera_gap_unknown(self):
        d=self.fixture();coverage(d,"door:R",20,30,"gap")
        self.assertEqual(self.result(d)["decision"],UNKNOWN)
    def test_unknown_item_protection_context_unknown(self):
        d=self.fixture();d["facts"]=[f for f in d["facts"] if f["name"]!="protected_asset_verified"]
        self.assertEqual(self.result(d)["decision"],UNKNOWN)
    def test_no_door_evidence_unknown(self):
        d=self.fixture();d["events"]=[e for e in d["events"] if e["event_type"]!="door_state"]
        self.assertEqual(self.result(d)["decision"],UNKNOWN)
    def test_contradictory_door_state_unknown(self):
        d=self.fixture();event(d,"door_state",("door",),10,100,data=dict(zone_id="R",door_state="closed",lock_state="verified_locked"))
        self.assertEqual(self.result(d)["decision"],UNKNOWN)


class QwenBridge(unittest.TestCase):
    def fixture(self):
        window=dict(window_id="w",frames=[dict(frame_ref="f0",timestamp_s=0,objects=[dict(display_id="cash-1",class_name="cash_stack")]),
                                           dict(frame_ref="f1",timestamp_s=1,objects=[dict(display_id="cash-1",class_name="cash_stack")])])
        response=dict(schema_version="datathon-qwen-events-v1",window_id="w",coverage_status="partial",events=[dict(
            event_type="completion_evidence",start_s=0,end_s=1,object_ids=["cash-1"],evidence_frame_refs=["f0","f1"],
            observed_facts=["candidate indicator"],visibility_limits=["sampling"],assessment="candidate",identity_status="track_hypothesis",
            completion_status="candidate_explicit_evidence",completion_cue="candidate visible cue",full_lot_observed=False)])
        return response,window
    def test_existing_qwen_completion_stays_candidate_without_session(self):
        response,window=self.fixture();s=candidate_sequence(response,window,"a"*64)
        self.assertEqual(s["evidence"][0]["review_status"],"candidate");self.assertNotIn("session_id",s["events"][0])
        self.assertEqual(evaluate(s,catalog())["decision"],UNKNOWN)
    def test_qwen_cannot_promote_full_lot(self):
        r,w=self.fixture();r["events"][0]["full_lot_observed"]=True
        with self.assertRaises(ContractError):candidate_sequence(r,w,"a"*64)
    def test_qwen_unseen_track_rejected(self):
        r,w=self.fixture();r["events"][0]["object_ids"]=["invented"]
        with self.assertRaises(ContractError):candidate_sequence(r,w,"a"*64)


class IdentityAndService(unittest.TestCase):
    def fixture(self):
        d=sequence(("A",),kind="receipt");contexts(d,"KZ-CASH-14");coverage(d,"identification:T")
        event(d,"cash_receipt",("A",),30,31,data=dict(customer_id="client-pseudonym"))
        return d
    def test_no_visible_id_alone_abstains(self):
        self.assertEqual(evaluate(self.fixture(),catalog("KZ-CASH-14"))["decision"],UNKNOWN)
    def test_exhaustive_authoritative_missing_check_candidate(self):
        d=self.fixture();fact(d,"verification_record_absence_confirmed")
        event(d,"identity_verification_record_absent",("client-pseudonym",),0,100)
        self.assertEqual(evaluate(d,catalog("KZ-CASH-14"))["decision"],VIOLATION)
    def test_digital_document_verification_is_accepted(self):
        d=self.fixture();event(d,"identity_verification_confirmed",("client-pseudonym",),10,11,data=dict(method="digital_document"))
        self.assertEqual(evaluate(d,catalog("KZ-CASH-14"))["decision"],CLEAR)
    def test_prior_or_electronic_history_unknown_abstains(self):
        d=self.fixture();fact(d,"verification_record_absence_confirmed");event(d,"identity_verification_record_absent",("client-pseudonym",),0,100)
        d["facts"]=[f for f in d["facts"] if f["name"]!="same_workflow_and_digital_checks_included"]
        self.assertEqual(evaluate(d,catalog("KZ-CASH-14"))["decision"],UNKNOWN)
    def test_collector_delivery_does_not_reuse_customer_id_rule(self):
        d=self.fixture();fact(d,"verification_record_absence_confirmed");event(d,"identity_verification_record_absent",("client-pseudonym",),0,100)
        d["facts"]=[f for f in d["facts"] if f["name"]!="deposit_client_not_collector_verified"]
        self.assertEqual(evaluate(d,catalog("KZ-CASH-14"))["decision"],UNKNOWN)
    def test_identification_during_same_receipt_workflow_is_accepted(self):
        d=self.fixture();event(d,"identity_verification_confirmed",("client-pseudonym",),50,51,data=dict(method="physical_document"))
        self.assertEqual(evaluate(d,catalog("KZ-CASH-14"))["decision"],CLEAR)
    def test_generic_prior_KYC_fact_is_not_an_exemption(self):
        d=self.fixture();fact(d,"prior_KYC_completed")
        self.assertEqual(evaluate(d,catalog("KZ-CASH-14"))["decision"],UNKNOWN)
    def test_departure_during_service_with_cashier_role(self):
        d=sequence();contexts(d,"KZ-CASH-15");fact(d,"cashier_role_verified","cashier");coverage(d,"workstation:T")
        event(d,"service_active",("service:T",),10,90);event(d,"workstation_exit",("cashier",),30,31)
        self.assertEqual(evaluate(d,catalog("KZ-CASH-15"))["decision"],VIOLATION)
        d["events"][-1]["event_type"]="person_leaving"
        self.assertEqual(evaluate(d,catalog("KZ-CASH-15"))["decision"],UNKNOWN)


if __name__ == "__main__":unittest.main()
