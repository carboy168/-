from __future__ import annotations

import json,os,tempfile,unittest
from pathlib import Path

import db,rag
from engineering_knowledge.layer import build_knowledge_package
from engineering_knowledge.requirement_claims import bind_project_chunks,bind_retrieved_clauses,load_requirement_claim_rules
from engineering_knowledge.schema_validator import validate_catalogs,validate_knowledge_package

ROOT=Path(__file__).resolve().parents[1]


class RequirementClaimsC2Tests(unittest.TestCase):
    def setUp(self):
        base=ROOT/".test-tmp";base.mkdir(exist_ok=True)
        self.tmp=tempfile.TemporaryDirectory(dir=base);self.old_db=db.DB_PATH
        db.DB_PATH=Path(self.tmp.name)/"knowledge-c2.sqlite";os.environ["DATABASE_PATH"]=str(db.DB_PATH);db.init_db()

    def tearDown(self):
        db.DB_PATH=self.old_db;os.environ["DATABASE_PATH"]=str(self.old_db);self.tmp.cleanup()

    def _insert_clause(self,code,clause_no,content,status="现行"):
        with db.connect() as con:
            standard_id=con.execute("INSERT INTO standards(code,title,status,source_priority) VALUES(?,?,?,?)",(code,"测试规范",status,100)).lastrowid
            clause_id=con.execute("INSERT INTO clauses(standard_id,clause_no,content,source_file) VALUES(?,?,?,?)",(standard_id,clause_no,content,"trusted-c2.pdf")).lastrowid
            return dict(con.execute("""SELECT c.id AS clause_id,c.clause_no,c.page_no,c.heading,c.content,
                s.id AS standard_id,s.code,s.title,s.status,s.mandatory_level,s.source_url,
                s.source_priority,s.effective_date FROM clauses c JOIN standards s ON s.id=c.standard_id
                WHERE c.id=?""",(clause_id,)).fetchone())

    @staticmethod
    def _normative_claims(package):
        return [item for item in package.requirement_claims if item.source_type=="standard_clause"]

    def test_rules_and_package_schemas_validate(self):
        rules=load_requirement_claim_rules();self.assertEqual(rules["rules_version"],"1.2-c2")
        validate_catalogs();validate_knowledge_package(build_knowledge_package("施工现场电缆怎么敷设？"))

    def test_c2_fixture_declares_required_safety_boundaries(self):
        fixture=json.loads((ROOT/"data"/"engineering_knowledge_c2_benchmark.json").read_text(encoding="utf-8"))
        actual={item["id"] for item in fixture["safety_cases"]}
        required={"real-current-clause-supported","user-claim-remains-unverified","missing-db-source-blocked","clause-override-superseded","project-file-remains-unverified","unapproved-method-not-approved","empty-retrieval-no-normative-claim","repeat-binding-idempotent"}
        self.assertEqual(actual,required)

    def test_c2_claim_extraction_benchmark(self):
        fixture=json.loads((ROOT/"data"/"engineering_knowledge_c2_benchmark.json").read_text(encoding="utf-8"))
        for index,case in enumerate(fixture["cases"],1):
            with self.subTest(case=case["id"]):
                row=self._insert_clause(f"GB 55034-{2021+index}","1.0.1",case["clause"])
                # Synthetic codes are deliberately not authoritative; extraction is tested independently of trust.
                package=build_knowledge_package("检查施工要求");bind_retrieved_clauses(package,[row]);claims=self._normative_claims(package)
                self.assertEqual(len(claims),case.get("claim_count",1))
                if claims:
                    claim=claims[0];self.assertEqual(claim.claim_type,case["claim_type"]);self.assertEqual(claim.operator,case["operator"])
                    self.assertEqual(claim.value,case["value"]);self.assertEqual(claim.unit,case["unit"])
                    if case.get("has_condition"):self.assertTrue(claim.conditions)

    def test_real_current_clause_becomes_supported_traceable_claim(self):
        text="电缆不得沿地面明敷。";row=self._insert_clause("GB 55034-2022","1.0.1",text)
        package=build_knowledge_package("施工现场电缆可以直接拖地吗？");bind_retrieved_clauses(package,[row])
        claim=self._normative_claims(package)[0];evidence=package.evidence_links[-1]
        self.assertEqual(claim.verification_status,"supported");self.assertTrue(evidence.verified)
        self.assertEqual(claim.original_text,evidence.original_text[claim.source_locator["start"]:claim.source_locator["end"]])
        self.assertIn(claim.claim_id,evidence.linked_claim_ids);self.assertIn(evidence.evidence_id,claim.evidence_links)

    def test_missing_database_source_is_blocked(self):
        row={"clause_id":999999,"code":"GB 55034-2022","clause_no":"1.0.1","content":"电缆不得沿地面明敷。","status":"现行","title":"测试"}
        package=build_knowledge_package("施工电缆怎么敷设？");bind_retrieved_clauses(package,[row])
        self.assertEqual(package.evidence_links[-1].verification_status,"blocked");self.assertEqual(self._normative_claims(package)[0].verification_status,"blocked")

    def test_clause_override_is_superseded_and_never_supported(self):
        row=self._insert_clause("GB 50303-2015","3.1.5","本条应按规定执行。")
        package=build_knowledge_package("GB 50303-2015第3.1.5条还能用吗？");bind_retrieved_clauses(package,[row])
        self.assertEqual(package.evidence_links[-1].verification_status,"superseded");self.assertNotEqual(self._normative_claims(package)[0].verification_status,"supported")

    def test_user_claim_is_not_upgraded_by_empty_retrieval(self):
        package=build_knowledge_package("国家规范规定吊杆间距必须600mm，对吧？")
        before=[(x.claim_id,x.verification_status,x.source_type) for x in package.requirement_claims]
        bind_retrieved_clauses(package,[])
        self.assertEqual(before,[(x.claim_id,x.verification_status,x.source_type) for x in package.requirement_claims])
        self.assertFalse(self._normative_claims(package))

    def test_repeat_binding_is_idempotent(self):
        row=self._insert_clause("GB 55034-2022","1.0.1","电缆不得沿地面明敷。")
        package=build_knowledge_package("施工现场电缆怎么敷设？");bind_retrieved_clauses(package,[row])
        counts=(len(package.evidence_links),len(package.requirement_claims));bind_retrieved_clauses(package,[row])
        self.assertEqual(counts,(len(package.evidence_links),len(package.requirement_claims)))

    def test_project_requirement_is_traceable_but_remains_unverified(self):
        row={"chunk_id":7,"file_id":3,"page_no":2,"section":"设计说明","content":"吊杆间距应为600mm。","title":"装修设计图","doc_type":"施工图纸"}
        package=build_knowledge_package("本项目吊杆怎么设置？");bind_project_chunks(package,[row])
        claim=next(x for x in package.requirement_claims if x.source_type=="design_drawing")
        evidence=next(x for x in package.evidence_links if x.evidence_id==claim.evidence_links[0])
        self.assertEqual((claim.project_binding,claim.verification_status),("design_drawing","unverified"))
        self.assertEqual((evidence.evidence_role,evidence.verification_status),("project_evidence","unverified"))
        self.assertFalse(evidence.verified);self.assertEqual(claim.normative_authority,"")
        self.assertEqual(claim.original_text,evidence.original_text[claim.source_locator["start"]:claim.source_locator["end"]])

    def test_unapproved_method_statement_is_not_marked_approved(self):
        row={"chunk_id":8,"file_id":4,"content":"管道应采用支架固定。","title":"方案","doc_type":"施工方案"}
        package=build_knowledge_package("管道怎么固定？");bind_project_chunks(package,[row])
        evidence=package.evidence_links[-1]
        self.assertEqual((evidence.source_type,evidence.project_binding),("project_record","project_record"))
        self.assertEqual(package.requirement_claims[-1].verification_status,"unverified")

    def test_retrieve_with_knowledge_is_additive_and_binds_real_rows(self):
        self._insert_clause("GB 55034-2022","1.0.1","电缆不得沿地面明敷。")
        rows,route,overlay,package=rag.retrieve_with_knowledge("GB 55034-2022第1.0.1条有什么要求？",project={})
        self.assertTrue(rows);self.assertTrue(route["explicit_standard"]);self.assertIsInstance(overlay,dict)
        self.assertEqual(self._normative_claims(package)[0].verification_status,"supported")
        self.assertTrue(any(plan.evidence_link_ids for plan in package.retrieval_plans))

    def test_claim_source_tampering_is_rejected(self):
        row=self._insert_clause("GB 55034-2022","1.0.1","电缆不得沿地面明敷。")
        package=build_knowledge_package("施工现场电缆怎么敷设？");bind_retrieved_clauses(package,[row])
        self._normative_claims(package)[0].original_text="被篡改的条文"
        with self.assertRaisesRegex(ValueError,"原文位置不可回溯"):validate_knowledge_package(package)


if __name__=="__main__":unittest.main()
