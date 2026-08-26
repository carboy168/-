from __future__ import annotations

import json,os,tempfile,unittest
from pathlib import Path

import db
from engineering_knowledge.conflict_detection import detect_claim_conflicts
from engineering_knowledge.evidence_trust import EvidenceTrustPolicy
from engineering_knowledge.json_schema import validate_json_file
from engineering_knowledge.layer import build_knowledge_package
from engineering_knowledge.project_evidence_lifecycle import (
    PROJECT_EVIDENCE_LIFECYCLE_STATUSES,ProjectEvidenceLifecycleValidator,
    lifecycle_status,supersede_project_evidence,transition_project_evidence,
)
from engineering_knowledge.requirement_claims import bind_project_chunks,bind_retrieved_clauses
from engineering_knowledge.schema_validator import validate_knowledge_package


ROOT=Path(__file__).resolve().parents[1]


class ProjectEvidenceLifecycleV13BTests(unittest.TestCase):
    def setUp(self):
        base=ROOT/".test-tmp";base.mkdir(exist_ok=True)
        self.tmp=tempfile.TemporaryDirectory(dir=base);self.old_db=db.DB_PATH
        db.DB_PATH=Path(self.tmp.name)/"lifecycle.sqlite";os.environ["DATABASE_PATH"]=str(db.DB_PATH);db.init_db()

    def tearDown(self):
        db.DB_PATH=self.old_db;os.environ["DATABASE_PATH"]=str(self.old_db);self.tmp.cleanup()

    @staticmethod
    def _chunk(chunk_id:int,version:str,text:str="吊杆间距不应大于900mm。"):
        return {"chunk_id":chunk_id,"file_id":10+chunk_id,"content":text,"title":f"设计变更-{version}","original_name":f"设计变更-{version}.pdf","doc_type":"设计变更","source_version":version,"page_no":2,"section":"吊顶工程"}

    def _norm(self,text:str="吊杆间距不应大于1200mm。"):
        with db.connect() as con:
            standard_id=con.execute("INSERT INTO standards(code,title,status,source_priority) VALUES(?,?,?,?)",("GB 55032-2022","测试规范","现行",100)).lastrowid
            clause_id=con.execute("INSERT INTO clauses(standard_id,clause_no,content,source_file) VALUES(?,?,?,?)",(standard_id,"1.0.1",text,"trusted-v13b.pdf")).lastrowid
            return dict(con.execute("""SELECT c.id AS clause_id,c.clause_no,c.page_no,c.content,s.code,s.title,s.status
                FROM clauses c JOIN standards s ON s.id=c.standard_id WHERE c.id=?""",(clause_id,)).fetchone())

    @staticmethod
    def _project_evidence(package):return [x for x in package.evidence_links if x.evidence_role=="project_evidence"]

    def test_schema_fixture_and_declared_lifecycle_are_complete(self):
        validate_json_file(ROOT/"data"/"project_evidence_lifecycle_benchmark.json",ROOT/"data"/"project_evidence_lifecycle_benchmark.schema.json")
        fixture=json.loads((ROOT/"data"/"project_evidence_lifecycle_benchmark.json").read_text(encoding="utf-8"))
        self.assertEqual(len(fixture["cases"]),12)
        self.assertEqual(PROJECT_EVIDENCE_LIFECYCLE_STATUSES,{"extracted","pending_confirmation","verified","rejected","superseded","expired"})

    def test_project_chunk_enters_pending_with_traceable_governance_metadata(self):
        package=build_knowledge_package("吊杆间距怎么控制？");bind_project_chunks(package,[self._chunk(1,"1.0")])
        evidence=self._project_evidence(package)[0];claim=package.requirement_claims[-1]
        self.assertEqual((lifecycle_status(evidence),evidence.verification_status,claim.verification_status),("pending_confirmation","unverified","unverified"))
        for key in ("source_file_name","source_version","source_hash","extracted_time","chunk_id","page_no","section"):self.assertIn(key,evidence.source_locator)
        self.assertIn(claim.claim_id,evidence.linked_claim_ids);self.assertIn(evidence.evidence_id,claim.evidence_links);self.assertTrue(package.human_confirmation_required)

    def test_extracted_state_must_transition_to_pending_before_confirmation(self):
        package=build_knowledge_package("吊杆间距怎么控制？");bind_project_chunks(package,[self._chunk(1,"1.0")]);evidence=self._project_evidence(package)[0]
        evidence.status="extracted";evidence.source_locator["lifecycle_status"]="extracted"
        result=ProjectEvidenceLifecycleValidator().validate(package)
        self.assertEqual((result.records[0].lifecycle_status,evidence.verification_status),("extracted","unverified"))
        transition_project_evidence(package,evidence.evidence_id,"pending_confirmation")
        self.assertEqual(lifecycle_status(evidence),"pending_confirmation")

    def test_confirmation_is_audited_and_remains_project_only(self):
        package=build_knowledge_package("吊杆间距怎么控制？");bind_project_chunks(package,[self._chunk(1,"1.0")]);evidence=self._project_evidence(package)[0]
        transition_project_evidence(package,evidence.evidence_id,"verified",actor="reviewer-001",at="2026-08-25T10:00:00+00:00")
        claim=package.requirement_claims[-1];trust=EvidenceTrustPolicy()
        self.assertEqual((evidence.status,evidence.verification_status,claim.verification_status),("verified","verified","supported"))
        self.assertEqual((evidence.source_locator["confirmed_by"],evidence.source_locator["confirmed_time"]),("reviewer-001","2026-08-25T10:00:00+00:00"))
        self.assertTrue(trust.can_support_project_claim(evidence).allowed);self.assertFalse(trust.can_support_normative_claim(evidence).allowed)
        self.assertNotEqual(claim.project_binding,"none");self.assertNotIn(claim.source_type,{"standard_clause","normative_clause"})

    def test_rejected_and_expired_evidence_cannot_support_claims(self):
        rejected=build_knowledge_package("吊杆间距怎么控制？");bind_project_chunks(rejected,[self._chunk(1,"1.0")]);ev=self._project_evidence(rejected)[0]
        transition_project_evidence(rejected,ev.evidence_id,"rejected");self.assertEqual((ev.status,rejected.requirement_claims[-1].verification_status),("rejected","blocked"))
        expired=build_knowledge_package("吊杆间距怎么控制？");bind_project_chunks(expired,[self._chunk(2,"2.0")]);ev=self._project_evidence(expired)[0]
        transition_project_evidence(expired,ev.evidence_id,"verified",actor="reviewer");transition_project_evidence(expired,ev.evidence_id,"expired")
        self.assertEqual((ev.status,ev.verification_status,expired.requirement_claims[-1].verification_status),("expired","blocked","blocked"))

    def test_newer_version_supersedes_old_and_triggers_claim_reevaluation(self):
        package=build_knowledge_package("吊杆间距怎么控制？");bind_project_chunks(package,[self._chunk(1,"1.0"),self._chunk(2,"2.0")])
        old,new=self._project_evidence(package);transition_project_evidence(package,old.evidence_id,"verified",actor="reviewer")
        result=supersede_project_evidence(package,old.evidence_id,new.evidence_id)
        old_claim=next(x for x in package.requirement_claims if old.evidence_id in x.evidence_links);new_claim=next(x for x in package.requirement_claims if new.evidence_id in x.evidence_links)
        self.assertEqual((old.status,old.verification_status,old.source_locator["superseded_by"]),("superseded","superseded",new.evidence_id))
        self.assertEqual((new.status,new.verification_status),("pending_confirmation","unverified"))
        self.assertEqual((old_claim.verification_status,new_claim.verification_status),("blocked","unverified"));self.assertIn(old_claim.claim_id,result.reevaluation_claim_ids)

    def test_older_version_cannot_overwrite_current_evidence(self):
        package=build_knowledge_package("吊杆间距怎么控制？");bind_project_chunks(package,[self._chunk(1,"2.0"),self._chunk(2,"1.0")])
        current,older=self._project_evidence(package);transition_project_evidence(package,current.evidence_id,"verified",actor="reviewer")
        result=supersede_project_evidence(package,current.evidence_id,older.evidence_id)
        self.assertEqual((current.status,current.verification_status),("verified","verified"));self.assertEqual((older.status,older.verification_status),("rejected","rejected"))
        self.assertTrue(any("禁止覆盖" in x for x in result.warnings+older.warnings))

    def test_verified_without_locator_fails_closed_to_pending(self):
        package=build_knowledge_package("吊杆间距怎么控制？");bind_project_chunks(package,[self._chunk(1,"1.0")]);evidence=self._project_evidence(package)[0]
        for key in ("page_no","drawing_no","chapter","section","paragraph","source_ref","chunk_id","requirement_id"):evidence.source_locator.pop(key,None)
        result=transition_project_evidence(package,evidence.evidence_id,"verified",actor="reviewer")
        self.assertEqual((evidence.status,evidence.verification_status),("pending_confirmation","unverified"));self.assertIn(evidence.evidence_id,result.downgraded_evidence_ids)
        self.assertEqual(package.requirement_claims[-1].verification_status,"unverified");self.assertTrue(package.human_confirmation_required)

    def test_pending_evidence_only_yields_potential_conflict(self):
        package=build_knowledge_package("吊杆间距怎么控制？");bind_retrieved_clauses(package,[self._norm()]);bind_project_chunks(package,[self._chunk(1,"1.0")]);detect_claim_conflicts(package)
        self.assertEqual(package.conflicts[0].status,"potential_conflict");self.assertTrue(package.conflicts[0].requires_human_review)

    def test_legacy_unverified_evidence_remains_insufficient(self):
        package=build_knowledge_package("吊杆间距怎么控制？");bind_retrieved_clauses(package,[self._norm()]);bind_project_chunks(package,[self._chunk(1,"1.0")])
        evidence=self._project_evidence(package)[0];evidence.status="unverified";evidence.source_locator.pop("lifecycle_managed",None);evidence.source_locator.pop("lifecycle_status",None)
        detect_claim_conflicts(package);self.assertEqual(package.conflicts[0].status,"insufficient_evidence")

    def test_superseded_evidence_is_never_a_current_deterministic_basis(self):
        package=build_knowledge_package("吊杆间距怎么控制？");bind_retrieved_clauses(package,[self._norm()]);bind_project_chunks(package,[self._chunk(1,"1.0"),self._chunk(2,"2.0")])
        old,new=self._project_evidence(package);transition_project_evidence(package,old.evidence_id,"verified",actor="reviewer");supersede_project_evidence(package,old.evidence_id,new.evidence_id);detect_claim_conflicts(package)
        old_claim=next(x for x in package.requirement_claims if old.evidence_id in x.evidence_links)
        old_result=next(x for x in package.conflicts if x.project_claim_id==old_claim.claim_id)
        self.assertEqual(old_result.status,"insufficient_evidence");self.assertFalse(EvidenceTrustPolicy().can_support_project_claim(old).allowed)
        validate_knowledge_package(package)


if __name__=="__main__":unittest.main()
