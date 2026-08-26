from __future__ import annotations

import json,unittest
from pathlib import Path
from unittest.mock import patch

from engineering_knowledge.json_schema import validate_json_file,validate_instance
from engineering_knowledge.layer import build_knowledge_package
from engineering_knowledge.models import ConflictResult,EvidenceLink,RequirementClaim
from engineering_knowledge.review_workflow import (
    ASSIGNMENT_ROLES,DISCOVERY_MODES,REEVALUATION_TRIGGERS,REVIEW_STATUSES,
    ReviewFindingStore,ReviewWorkflowError,finding_fingerprint,
)


ROOT=Path(__file__).resolve().parents[1]


class _Clock:
    def __init__(self):self.value=0
    def __call__(self):
        self.value+=1;return f"2026-08-26T00:00:{self.value:02d}+00:00"


class ReviewWorkflowV13CTests(unittest.TestCase):
    def setUp(self):self.store=ReviewFindingStore(_Clock())

    @staticmethod
    def _discover(store:ReviewFindingStore|None=None,mode="deterministic",evidence=None):
        store=store or ReviewFindingStore(_Clock())
        return store.discover(fingerprint="a"*64,source_type="conflict_detection" if mode=="deterministic" else "model_review",
            source_claims=["project-claim"],norm_claims=["norm-claim"],conflict_type="conflict",
            evidence_links=evidence or ["project-evidence","norm-evidence"],discovery_mode=mode)

    @staticmethod
    def _claim(claim_id,project):
        return RequirementClaim(claim_id,"numeric_requirement","design_drawing" if project else "standard_clause","source",
            {"drawing_no":"A-101"} if project else {"standard_code":"GB 55032-2022","clause_no":"1.0.1"},
            "吊杆间距不应大于900mm。",["obj-1"],"间距","lte","900","mm","控制",[],[],"construction",
            "" if project else "mandatory_code","design_drawing" if project else "none","current","supported","high",
            ["ev-project" if project else "ev-norm"])

    @classmethod
    def _package(cls):
        package=build_knowledge_package("吊杆间距怎么控制？")
        project=cls._claim("project-claim",True);norm=cls._claim("norm-claim",False)
        project_ev=EvidenceLink("ev-project","design_drawing","drawing:A-101",{"drawing_no":"A-101","source_version":"1.0","source_hash":"1"*64},project.original_text,
            drawing_no="A-101",project_binding="design_drawing",verified=True,evidence_role="project_evidence",verification_status="verified",linked_claim_ids=[project.claim_id])
        norm_ev=EvidenceLink("ev-norm","standard_clause","clause:1",{"standard_code":"GB 55032-2022","clause_no":"1.0.1"},norm.original_text,
            standard_code="GB 55032-2022",clause_no="1.0.1",verified=True,evidence_role="normative_evidence",verification_status="verified",linked_claim_ids=[norm.claim_id])
        package.requirement_claims=[project,norm];package.evidence_links=[project_ev,norm_ev]
        package.conflicts=[ConflictResult("conflict-001","conflict",project.claim_id,norm.claim_id,[project_ev.evidence_id,norm_ev.evidence_id],"要求相反",{},"high",True)]
        return package

    def _to_closed(self,finding):
        self.store.transition(finding,"confirmed",actor="reviewer-a",note="确认成立")
        self.store.transition(finding,"assigned",actor="reviewer-a",assignee="contractor-a",role="contractor")
        self.store.transition(finding,"rectifying",actor="contractor-a",note="正在整改",expected_finish="2026-09-01")
        self.store.transition(finding,"pending_review",actor="contractor-a",note="整改完成")
        self.store.transition(finding,"closed",actor="reviewer-b",reason="复核通过")

    def test_schema_fixture_and_runtime_model_are_complete(self):
        fixture=validate_json_file(ROOT/"data"/"review_workflow_benchmark.json",ROOT/"data"/"review_workflow_benchmark.schema.json")
        self.assertEqual(len(fixture["cases"]),12)
        self.assertEqual(REVIEW_STATUSES,{"detected","pending_confirmation","confirmed","assigned","rectifying","pending_review","closed","reopened"})
        self.assertEqual(DISCOVERY_MODES,{"deterministic","model_assisted"});self.assertEqual(ASSIGNMENT_ROLES,{"owner","contractor","designer","supervision","other"})
        finding=self._discover(self.store);schema=json.loads((ROOT/"data"/"review_finding.schema.json").read_text(encoding="utf-8"));validate_instance(finding.to_dict(),schema)

    def test_same_issue_is_deduplicated_and_history_is_retained(self):
        first=self._discover(self.store);second=self._discover(self.store)
        self.assertIs(first,second);self.assertEqual(len(self.store.all()),1);self.assertEqual(second.revision,2)
        self.assertEqual([x.action for x in second.history],["discovered","rediscovered"])

    def test_fingerprint_is_stable_across_ids_and_source_versions(self):
        claim1={"claim_type":"numeric_requirement","subject":["obj-1"],"property":"间距","operator":"lte","value":"900","unit":"mm","action":"控制","conditions":[],"exceptions":[]}
        claim2=dict(claim1,subject=["obj-99"])
        ev1={"source_type":"design_drawing","source_locator":{"drawing_no":"A-101","source_version":"1.0","source_hash":"1"*64}}
        ev2={"source_type":"design_drawing","source_locator":{"drawing_no":"A-101","source_version":"2.0","source_hash":"2"*64}}
        one=finding_fingerprint(conflict_type="conflict",source_claims=[claim1],norm_claims=[],evidence_links=[ev1],objects=[{"object_type":"吊杆","canonical_name":"吊顶吊杆"}])
        two=finding_fingerprint(conflict_type="conflict",source_claims=[claim2],norm_claims=[],evidence_links=[ev2],objects=[{"object_type":"吊杆","canonical_name":"吊顶吊杆"}])
        self.assertEqual(one,two)

    def test_required_happy_path_transitions_and_audit_fields(self):
        finding=self._discover(self.store);self.assertEqual(finding.status,"detected")
        self.store.transition(finding,"confirmed",actor="reviewer-a",note="确认成立")
        self.assertEqual((finding.confirmed_by,finding.confirmation_note),("reviewer-a","确认成立"))
        self.store.transition(finding,"assigned",actor="reviewer-a",assignee="contractor-a",role="contractor")
        self.assertEqual((finding.status,finding.assignee,finding.assignment_role),("assigned","contractor-a","contractor"))
        self.store.transition(finding,"rectifying",actor="contractor-a",note="正在整改",expected_finish="2026-09-01")
        self.assertEqual((finding.status,finding.rectification_note,finding.expected_finish),("rectifying","正在整改","2026-09-01"))
        self.store.transition(finding,"pending_review",actor="contractor-a",note="整改完成")
        self.store.transition(finding,"closed",actor="reviewer-b",reason="复核通过")
        self.assertEqual((finding.status,finding.reviewer,finding.close_reason),("closed","reviewer-b","复核通过"))

    def test_required_transition_metadata_fails_closed(self):
        finding=self._discover(self.store)
        with self.assertRaises(ReviewWorkflowError):self.store.transition(finding,"confirmed",actor="reviewer-a")
        self.store.transition(finding,"confirmed",actor="reviewer-a",note="确认成立")
        with self.assertRaises(ReviewWorkflowError):self.store.transition(finding,"assigned",actor="reviewer-a",role="contractor")

    def test_closed_finding_can_be_reopened_explicitly(self):
        finding=self._discover(self.store);self._to_closed(finding)
        self.store.transition(finding,"reopened",actor="reviewer-b",reason="复核发现未解决")
        self.assertEqual(finding.status,"reopened");self.assertTrue(finding.reevaluation_required)

    def test_new_evidence_deduplicates_and_triggers_reevaluation(self):
        finding=self._discover(self.store,evidence=["ev-old"])
        updated=self._discover(self.store,evidence=["ev-old","ev-new"])
        self.assertIs(finding,updated);self.assertEqual(len(self.store.all()),1);self.assertTrue(updated.reevaluation_required)
        self.assertIn("ev-new",updated.evidence_links);self.assertTrue(any(x.metadata.get("trigger")=="evidence_changed" for x in updated.history))

    def test_norm_status_change_reopens_closed_finding(self):
        finding=self._discover(self.store);self._to_closed(finding)
        self.store.request_reevaluation(finding,"norm_status_changed",note="规范由 current 变为 superseded")
        self.assertEqual(finding.status,"reopened");self.assertTrue(finding.reevaluation_required)

    def test_claim_change_reruns_existing_matcher_and_conflict_evaluator(self):
        package=self._package();self.store.ingest_conflicts(package)
        with patch("engineering_knowledge.review_workflow.detect_claim_conflicts",side_effect=lambda value,detector=None:value) as detect:
            result=self.store.reevaluate_package(package,"claim_changed")
        detect.assert_called_once();self.assertTrue(result);self.assertTrue(all(x.reevaluation_required for x in self.store.all()))
        self.assertEqual(REEVALUATION_TRIGGERS,{"evidence_changed","norm_status_changed","claim_changed","review_unresolved"})

    def test_model_assisted_finding_requires_confirmation_and_cannot_auto_close(self):
        finding=self._discover(self.store,mode="model_assisted")
        self.assertEqual(finding.status,"pending_confirmation")
        with self.assertRaises(ReviewWorkflowError):self.store.transition(finding,"closed",actor="model",reason="自动关闭")
        self.assertEqual(finding.status,"pending_confirmation")

    def test_uncertain_deterministic_result_waits_for_confirmation(self):
        finding=self.store.discover(fingerprint="b"*64,source_type="evidence_gate",source_claims=["project-claim"],norm_claims=[],
            conflict_type="insufficient_evidence",evidence_links=["project-evidence"],discovery_mode="deterministic")
        self.assertEqual((finding.status,finding.discovery_mode),("pending_confirmation","deterministic"))

    def test_deterministic_and_model_findings_are_explicitly_distinguished(self):
        package=self._package();deterministic=self.store.ingest_conflicts(package)[0]
        model=self.store.ingest_model_findings([{"severity":"中","category":"机电协调","issue":"疑似贴邻","finding_type":"需核对","norm_refs":[],"project_refs":["P1"]}])[0]
        self.assertEqual((deterministic.discovery_mode,deterministic.status),("deterministic","detected"))
        self.assertEqual((model.discovery_mode,model.status),("model_assisted","pending_confirmation"))

    def test_runtime_state_is_not_part_of_frozen_knowledge_package_contract(self):
        package=self._package();self.store.ingest_conflicts(package)
        self.assertNotIn("review_workflow",package.to_dict());self.assertEqual(package.schema_version,"1.2-c3")


if __name__=="__main__":unittest.main()
