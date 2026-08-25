from __future__ import annotations

import os,tempfile,types,unittest
from pathlib import Path

import db
from unittest.mock import patch
from engineering_knowledge.conflict_detection import detect_claim_conflicts
from engineering_knowledge.layer import build_knowledge_package
from engineering_knowledge.presentation import knowledge_summary,review_conflict_findings
from engineering_knowledge.requirement_claims import bind_project_chunks,bind_project_requirements,bind_retrieved_clauses
from review_engine import _engineering_conflict_overlay,run_review
from project_kb import list_findings
from project_mode import ensure_project_schema,get_project,save_project

ROOT=Path(__file__).resolve().parents[1]


class EngineeringReviewC4Tests(unittest.TestCase):
    def setUp(self):
        base=ROOT/".test-tmp";base.mkdir(exist_ok=True)
        self.tmp=tempfile.TemporaryDirectory(dir=base);self.old_db=db.DB_PATH
        db.DB_PATH=Path(self.tmp.name)/"knowledge-c4.sqlite";os.environ["DATABASE_PATH"]=str(db.DB_PATH);db.init_db();ensure_project_schema()
        self.project_id=save_project({"name":"测试项目","scopes":["装饰装修"]})

    def tearDown(self):
        db.DB_PATH=self.old_db;os.environ["DATABASE_PATH"]=str(self.old_db);self.tmp.cleanup()

    def _norm_row(self,text="吊杆间距不应大于1200mm。"):
        with db.connect() as con:
            standard_id=con.execute("INSERT INTO standards(code,title,status,source_priority) VALUES(?,?,?,?)",("GB 55032-2022","测试规范","现行",100)).lastrowid
            clause_id=con.execute("INSERT INTO clauses(standard_id,clause_no,content,source_file) VALUES(?,?,?,?)",(standard_id,"1.0.1",text,"trusted-c4.pdf")).lastrowid
            return dict(con.execute("""SELECT c.id AS clause_id,c.clause_no,c.page_no,c.heading,c.content,
                s.id AS standard_id,s.code,s.title,s.status,s.mandatory_level,s.source_url,s.source_priority,s.effective_date
                FROM clauses c JOIN standards s ON s.id=c.standard_id WHERE c.id=?""",(clause_id,)).fetchone())

    def _requirement(self,text="吊杆间距不应大于1500mm。",status="有效"):
        with db.connect() as con:
            requirement_id=con.execute("INSERT INTO project_requirements(project_id,doc_type,title,requirement_text,source_ref,status,priority) VALUES(?,?,?,?,?,?,?)",(self.project_id,"施工图纸","吊顶设计要求",text,"A-101",status,80)).lastrowid
            return dict(con.execute("SELECT * FROM project_requirements WHERE id=?",(requirement_id,)).fetchone())

    def test_confirmed_project_requirement_is_verified_and_traceable(self):
        package=build_knowledge_package("吊杆间距怎么控制？");bind_project_requirements(package,[self._requirement()])
        claim=next(item for item in package.requirement_claims if item.project_binding=="design_drawing")
        evidence=next(item for item in package.evidence_links if item.evidence_id in claim.evidence_links)
        self.assertEqual((claim.verification_status,evidence.verification_status),("supported","verified"))
        self.assertEqual(claim.source_locator["kind"],"project_requirement");self.assertEqual(claim.source_locator["requirement_id"],evidence.source_locator["requirement_id"])

    def test_inactive_project_requirement_is_ignored(self):
        package=build_knowledge_package("吊杆间距怎么控制？");before=len(package.requirement_claims)
        bind_project_requirements(package,[self._requirement(status="失效")]);self.assertEqual(len(package.requirement_claims),before)

    def test_forged_project_requirement_dict_stays_unverified(self):
        forged={"id":999,"project_id":self.project_id,"doc_type":"施工图纸","title":"伪造记录","requirement_text":"吊杆间距不应大于900mm。","source_ref":"A-999","status":"有效"}
        package=build_knowledge_package("吊杆间距怎么控制？");bind_project_requirements(package,[forged])
        claim=next(item for item in package.requirement_claims if item.source_id=="project_requirement:999")
        self.assertEqual(claim.verification_status,"unverified")

    def test_review_overlay_generates_traceable_closure_finding(self):
        norm=self._norm_row();requirement=self._requirement();project={"id":self.project_id,"name":"测试项目","scopes":["装饰装修"]}
        package,findings,summary=_engineering_conflict_overlay(project,"施工图审查","吊杆间距",[norm],[],[requirement])
        self.assertEqual(package.conflicts[0].status,"norm_stricter");self.assertIn("规范要求更严格",summary)
        self.assertEqual((findings[0]["norm_refs"],findings[0]["project_refs"]),(["N1"],["R1"]))
        self.assertEqual((findings[0]["finding_type"],findings[0]["status"]),("需核对","待确认"))
        self.assertIn("不自动宣布",findings[0]["notes"])

    def test_run_review_persists_deterministic_finding_into_existing_closure(self):
        norm=self._norm_row();requirement=self._requirement();sample=Path(self.tmp.name)/"review.txt";sample.write_text("吊杆设计审查",encoding="utf-8")
        fake=types.SimpleNamespace(config=types.SimpleNamespace(model="mock-review"),generate=lambda **kwargs:'{"summary":"模型审查完成","findings":[]}')
        with patch("review_engine.resolve_provider",return_value=fake),patch("review_engine._norm_evidence",return_value=[norm]),patch("review_engine.search_project_chunks",return_value=[]),patch("review_engine.list_project_requirements",return_value=[requirement]):
            result=run_review(get_project(self.project_id),[str(sample)],"施工图审查",scope="吊杆间距")
        saved=list_findings(review_id=result["review_id"])
        self.assertTrue(saved);self.assertEqual(saved[0]["finding_type"],"需核对");self.assertEqual(saved[0]["status"],"待确认")
        self.assertIn("规范要求更严格",saved[0]["issue"]);self.assertEqual(result["meta"]["engineering_conflicts"][0]["status"],"norm_stricter")

    def test_unverified_project_chunk_only_produces_evidence_gap(self):
        norm=self._norm_row();chunk={"chunk_id":7,"file_id":3,"content":"吊杆间距不得小于1500mm。","title":"未确认图纸","doc_type":"施工图纸"}
        package=build_knowledge_package("吊杆间距怎么控制？");bind_retrieved_clauses(package,[norm]);bind_project_chunks(package,[chunk]);detect_claim_conflicts(package)
        findings,summary=review_conflict_findings(package,[norm],[chunk],[])
        self.assertEqual(package.conflicts[0].status,"insufficient_evidence");self.assertEqual(findings[0]["evidence_grade"],"D");self.assertIn("证据不足",summary)

    def test_qa_summary_is_user_facing_and_hides_debug_weights(self):
        norm=self._norm_row();package=build_knowledge_package("吊杆间距怎么控制？");bind_retrieved_clauses(package,[norm]);detect_claim_conflicts(package)
        text=knowledge_summary(package)
        for label in ("工程知识摘要","证据状态","要求 Claim","冲突检查","结论边界"):self.assertIn(label,text)
        self.assertNotIn("match_score",text);self.assertNotIn("lexical_overlap",text)


if __name__=="__main__":unittest.main()
