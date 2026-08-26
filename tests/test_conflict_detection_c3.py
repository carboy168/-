from __future__ import annotations

import hashlib,json,os,tempfile,unittest
from pathlib import Path

import db
from engineering_knowledge.conflict_detection import detect_claim_conflicts,load_conflict_detection_rules
from engineering_knowledge.layer import build_knowledge_package
from engineering_knowledge.models import EvidenceLink
from engineering_knowledge.requirement_claims import RequirementClaimExtractor,bind_retrieved_clauses
from engineering_knowledge.schema_validator import validate_catalogs,validate_knowledge_package

ROOT=Path(__file__).resolve().parents[1]


class ConflictDetectionC3Tests(unittest.TestCase):
    def setUp(self):
        base=ROOT/".test-tmp";base.mkdir(exist_ok=True)
        self.tmp=tempfile.TemporaryDirectory(dir=base);self.old_db=db.DB_PATH
        db.DB_PATH=Path(self.tmp.name)/"knowledge-c3.sqlite";os.environ["DATABASE_PATH"]=str(db.DB_PATH);db.init_db()

    def tearDown(self):
        db.DB_PATH=self.old_db;os.environ["DATABASE_PATH"]=str(self.old_db);self.tmp.cleanup()

    def _norm_row(self,text,code="GB 55032-2022",clause_no="1.0.1"):
        with db.connect() as con:
            row=con.execute("SELECT id FROM standards WHERE code=? AND title=?",(code,"测试规范")).fetchone()
            standard_id=row["id"] if row else con.execute("INSERT INTO standards(code,title,status,source_priority) VALUES(?,?,?,?)",(code,"测试规范","现行",100)).lastrowid
            clause_id=con.execute("INSERT INTO clauses(standard_id,clause_no,content,source_file) VALUES(?,?,?,?)",(standard_id,clause_no,text,"trusted-c3.pdf")).lastrowid
            return dict(con.execute("""SELECT c.id AS clause_id,c.clause_no,c.page_no,c.heading,c.content,
                s.id AS standard_id,s.code,s.title,s.status,s.mandatory_level,s.source_url,s.source_priority,s.effective_date
                FROM clauses c JOIN standards s ON s.id=c.standard_id WHERE c.id=?""",(clause_id,)).fetchone())

    @staticmethod
    def _project_evidence(text,verified=True,evidence_id="ev-project-confirmed"):
        return EvidenceLink(
            evidence_id=evidence_id,source_type="design_drawing",source_id="drawing:A-101",
            source_locator={"kind":"drawing","drawing_no":"A-101"},original_text=text,drawing_no="A-101",document_name="已确认设计图",
            status="verified" if verified else "unverified",project_binding="design_drawing",content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            verified=verified,evidence_role="project_evidence",verification_status="verified" if verified else "unverified",
        )

    def _package(self,norm_text,project_text,project_verified=True,code="GB 55032-2022",clause_no="1.0.1",question="吊杆间距怎么控制？"):
        package=build_knowledge_package(question);bind_retrieved_clauses(package,[self._norm_row(norm_text,code,clause_no)])
        evidence=self._project_evidence(project_text,project_verified);claims=RequirementClaimExtractor().extract(package,evidence,len(package.requirement_claims)+1)
        evidence.linked_claim_ids=[item.claim_id for item in claims];package.evidence_links.append(evidence);package.requirement_claims.extend(claims)
        return detect_claim_conflicts(package)

    def test_rules_schema_and_package_schema_validate(self):
        self.assertEqual(load_conflict_detection_rules()["rules_version"],"1.2-c3");validate_catalogs()
        validate_knowledge_package(build_knowledge_package("吊杆间距怎么控制？"))

    def test_seven_required_conflict_statuses_from_benchmark(self):
        fixture=json.loads((ROOT/"data"/"engineering_knowledge_c3_benchmark.json").read_text(encoding="utf-8"))
        for case in fixture["cases"]:
            with self.subTest(case=case["id"]):
                package=self._package(case["norm"],case["project"],case.get("project_verified",True))
                self.assertEqual(package.conflicts[0].status,case["expected_status"])

    def test_opposing_project_and_normative_actions_conflict(self):
        package=self._package("电缆不得沿地面敷设。","电缆应沿地面敷设。",question="电缆沿地面敷设可以吗？")
        result=package.conflicts[0];self.assertEqual(result.status,"conflict");self.assertTrue(result.requires_human_review)

    def test_opposing_modal_words_without_same_action_are_not_false_conflict(self):
        package=self._package("电缆不得沿地面敷设。","电缆应架空敷设。",question="电缆应该怎么敷设？")
        self.assertNotEqual(package.conflicts[0].status,"conflict")

    def test_trusted_current_clause_is_preferred_over_better_matching_override(self):
        package=build_knowledge_package("吊杆间距怎么控制？")
        bind_retrieved_clauses(package,[self._norm_row("吊杆间距不应大于900mm。","GB 50303-2015","3.1.5")])
        bind_retrieved_clauses(package,[self._norm_row("吊杆间距不应大于1200mm。","GB 55032-2022","1.0.2")])
        evidence=self._project_evidence("吊杆间距不应大于800mm。");claims=RequirementClaimExtractor().extract(package,evidence,len(package.requirement_claims)+1)
        evidence.linked_claim_ids=[item.claim_id for item in claims];package.evidence_links.append(evidence);package.requirement_claims.extend(claims);detect_claim_conflicts(package)
        result=package.conflicts[0];self.assertEqual(result.status,"project_stricter")
        self.assertEqual(result.normative_claim_id,next(x.claim_id for x in package.requirement_claims if x.source_locator.get("clause_no")=="1.0.2"))

    def test_one_project_claim_is_checked_against_all_comparable_current_norms(self):
        package=build_knowledge_package("吊杆间距怎么控制？")
        bind_retrieved_clauses(package,[self._norm_row("吊杆间距不应大于1200mm。","GB 55032-2022","1.0.1")])
        bind_retrieved_clauses(package,[self._norm_row("吊杆间距不应小于1000mm。","GB 55032-2022","1.0.2")])
        evidence=self._project_evidence("吊杆间距应为900mm。");claims=RequirementClaimExtractor().extract(package,evidence,len(package.requirement_claims)+1)
        evidence.linked_claim_ids=[item.claim_id for item in claims];package.evidence_links.append(evidence);package.requirement_claims.extend(claims);detect_claim_conflicts(package)
        self.assertEqual({item.status for item in package.conflicts},{"project_stricter","conflict"})

    def test_unrelated_numeric_requirements_with_same_unit_are_not_compared(self):
        package=self._package("吊杆间距不应大于1200mm。","门洞宽度不应小于900mm。",question="吊杆和门洞怎么控制？")
        self.assertEqual(package.conflicts[0].status,"not_comparable")

    def test_clause_override_forces_insufficient_evidence(self):
        package=self._package("本条规定吊杆间距不应大于1200mm。","吊杆间距不应大于900mm。",code="GB 50303-2015",clause_no="3.1.5")
        self.assertEqual(package.conflicts[0].status,"insufficient_evidence")
        self.assertTrue(any(item.verification_status=="superseded" for item in package.evidence_links))

    def test_unverified_project_file_never_produces_deterministic_conflict(self):
        package=self._package("吊杆间距不应大于1200mm。","吊杆间距不得小于1500mm。",False)
        self.assertEqual(package.conflicts[0].status,"insufficient_evidence")

    def test_missing_side_is_insufficient_and_router_is_not_evidence(self):
        package=build_knowledge_package("吊杆间距怎么控制？");bind_retrieved_clauses(package,[self._norm_row("吊杆间距不应大于1200mm。")]);detect_claim_conflicts(package)
        self.assertEqual(package.conflicts[0].status,"insufficient_evidence")
        self.assertFalse(any(item.source_type=="router_candidate" for item in package.evidence_links))

    def test_detection_is_idempotent_and_links_both_evidence_sources(self):
        package=self._package("吊杆间距不应大于1200mm。","吊杆间距不应大于900mm。")
        first=package.to_dict()["conflicts"];detect_claim_conflicts(package)
        self.assertEqual(first,package.to_dict()["conflicts"]);self.assertEqual(len(package.conflicts[0].evidence_links),2)

    def test_conflict_reference_tampering_is_rejected(self):
        package=self._package("吊杆间距不应大于1200mm。","吊杆间距不应大于900mm。")
        package.conflicts[0].project_claim_id="claim-999"
        with self.assertRaisesRegex(ValueError,"未知项目 Claim"):validate_knowledge_package(package)

    def test_conflict_cannot_swap_claim_scope_or_drop_evidence_trace(self):
        package=self._package("吊杆间距不应大于1200mm。","吊杆间距不应大于900mm。")
        conflict=package.conflicts[0];conflict.project_claim_id=conflict.normative_claim_id
        with self.assertRaisesRegex(ValueError,"项目 Claim 作用域错误"):validate_knowledge_package(package)
        package=self._package("吊杆间距不应大于1200mm。","吊杆间距不应大于900mm。")
        package.conflicts[0].evidence_links=[]
        with self.assertRaisesRegex(ValueError,"未完整保留 Claim EvidenceLink"):validate_knowledge_package(package)

    def test_safety_fixture_declares_required_boundaries(self):
        fixture=json.loads((ROOT/"data"/"engineering_knowledge_c3_benchmark.json").read_text(encoding="utf-8"))
        self.assertEqual({item["id"] for item in fixture["safety_cases"]},{"unverified-project-blocked","clause-override-blocked","router-candidate-not-evidence","opposing-actions","repeat-detection-idempotent"})


if __name__=="__main__":unittest.main()
