from __future__ import annotations

import copy,hashlib,json,os,tempfile,unittest
from pathlib import Path

import db
from unittest.mock import patch
from engineering_knowledge.evidence_trust import EvidenceTrustPolicy
from engineering_knowledge.json_schema import JsonSchemaValidationError,validate_instance,validate_json_file
from engineering_knowledge.layer import build_knowledge_package
from engineering_knowledge.models import EvidenceLink
from engineering_knowledge.schema_validator import validate_catalogs,validate_knowledge_package

ROOT=Path(__file__).resolve().parents[1]


class EngineeringKnowledgeC1Tests(unittest.TestCase):
    def setUp(self):
        base=ROOT/".test-tmp";base.mkdir(exist_ok=True)
        self.tmp=tempfile.TemporaryDirectory(dir=base);self.old_db=db.DB_PATH
        db.DB_PATH=Path(self.tmp.name)/"knowledge-c1.sqlite";os.environ["DATABASE_PATH"]=str(db.DB_PATH);db.init_db()

    def tearDown(self):db.DB_PATH=self.old_db;self.tmp.cleanup()

    @staticmethod
    def _relation_tuples(package):
        types={x.object_id:x.object_type for x in package.objects}
        return {(types[x.subject_object_id],x.relation_type,types[x.object_object_id],x.assertion_status) for x in package.relations}

    def _insert_clause(self,code,clause_no,content):
        with db.connect() as con:
            row=con.execute("SELECT id FROM standards WHERE code=?",(code,)).fetchone()
            if row:standard_id=row["id"]
            else:standard_id=con.execute("INSERT INTO standards(code,title,status,source_priority) VALUES(?,?,?,?)",(code,"测试规范","现行",100)).lastrowid
            return con.execute("INSERT INTO clauses(standard_id,clause_no,content,source_file) VALUES(?,?,?,?)",(standard_id,clause_no,content,"trusted-test.pdf")).lastrowid

    @staticmethod
    def _evidence(evidence_id,source_type,role,text,**kwargs):
        return EvidenceLink(
            evidence_id=evidence_id,source_type=source_type,source_id=kwargs.pop("source_id","test-source"),
            source_locator=kwargs.pop("source_locator",{"kind":source_type}),original_text=text,
            content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),evidence_role=role,
            verification_status=kwargs.pop("verification_status","verified"),verified=kwargs.pop("verified",True),**kwargs,
        )

    def test_c1_benchmark(self):
        benchmark=json.loads((ROOT/"data"/"engineering_knowledge_c1_benchmark.json").read_text(encoding="utf-8"))
        for case in benchmark["cases"]:
            with self.subTest(case=case["id"]):
                package=build_knowledge_package(case["question"]);objects={x.object_type for x in package.objects};relations=self._relation_tuples(package)
                self.assertTrue(set(case["required_objects"]).issubset(objects));topics={x["id"] for x in package.topics};self.assertTrue(set(case.get("required_topics",[])).issubset(topics))
                expected=case.get("required_relation")
                if expected:self.assertIn((expected["subject_type"],expected["relation_type"],expected["object_type"],expected["assertion_status"]),relations)
                if case.get("forbid_affirmed"):self.assertFalse(any(x[3]=="affirmed" for x in relations))
                self.assertFalse(set(case.get("forbidden_relations",[])) & {x[1] for x in relations})
                if case.get("minimum_relations"):self.assertGreaterEqual(len(package.relations),case["minimum_relations"]);self.assertEqual({x.relation_type for x in package.relations},{case["required_relation_type"]})
                self.assertTrue(set(case.get("required_plans",[])).issubset({x.topic_id for x in package.retrieval_plans}))
                if case.get("explicit_standard"):
                    self.assertTrue(package.route_metadata["explicit_standard"]);route_codes={code for plan in package.retrieval_plans for code in plan.preferred_standard_codes}
                    self.assertFalse(set(case["forbidden_preferred_standards"]) & route_codes);self.assertTrue(any(case["required_warning_text"] in x for x in package.warnings))

    def test_relation_assertions_remain_relevant_without_becoming_affirmed(self):
        cases=(("电线贴着给水管走。","affirmed"),("电线没有贴着给水管走。","negated"),("电线如果贴着给水管走怎么办？","conditional"),("怀疑电线可能贴到给水管。","uncertain"))
        for question,status in cases:
            with self.subTest(status=status):
                package=build_knowledge_package(question);relation=package.relations[0]
                self.assertEqual(relation.assertion_status,status);self.assertTrue(relation.relation_relevant_for_retrieval)
                plans=[x for x in package.retrieval_plans if relation.relation_id in x.relation_ids]
                self.assertTrue(plans);self.assertTrue(all(f"[{status}]" in x.reason for x in plans))

    def test_crosswalk_is_primary_and_builds_topic_graph(self):
        package=build_knowledge_package("吊顶里面电线贴着给水管走可以吗？")
        self.assertIn("topic_crosswalk",{x.get("source") for x in package.topics})
        self.assertFalse(any("deprecated_fallback" in x.get("source","") for x in package.topics))
        edges={(x.source_topic_id,x.edge_type,x.target_topic_id) for x in package.topic_graph.edges}
        self.assertIn(("building_electrical","requires_coordination_with","building_plumbing"),edges)
        self.assertIn(("building_electrical","located_in","ceiling"),edges)

    def test_crosswalk_failure_preserves_v12b_and_uses_deprecated_fallback(self):
        with patch("engineering_knowledge.layer.TopicCrosswalk.resolve",side_effect=RuntimeError("broken crosswalk")):
            package=build_knowledge_package("施工现场电缆可以直接拖地吗？")
        self.assertIn("temporary_power",{x["id"] for x in package.topics})
        self.assertTrue(any("deprecated_fallback" in x.get("source","") for x in package.topics))
        self.assertTrue(any("Topic Crosswalk 失败" in x for x in package.warnings))

    def test_retrieval_plan_explains_why_and_is_not_evidence(self):
        package=build_knowledge_package("风管穿过防火墙后怎么处理？")
        plan=next(x for x in package.retrieval_plans if x.topic_id=="penetration_firestopping")
        self.assertIn("duct penetrates fire_wall",plan.reason);self.assertEqual(plan.required_evidence_role,"normative_evidence")
        self.assertEqual(plan.evidence_status,"unverified");self.assertEqual(package.route_metadata["router_is_evidence"],False)

    def test_retrieval_plan_revalidates_caller_supplied_standard_candidates(self):
        route={"themes":[{"id":"temporary_power","confidence":"high"}],"primary_codes":["JGJ 46-2005"],"secondary_codes":[],"query_expansion":[],"evidence_gate":"requires_clause_evidence","project_stage":"construction","user_role":"unknown","claims":[],"warnings":[]}
        package=build_knowledge_package("施工电缆怎么敷设？",route=route)
        codes={code for plan in package.retrieval_plans for code in plan.preferred_standard_codes}
        self.assertNotIn("JGJ 46-2005",codes);self.assertIn("JGJ/T 46-2024",codes)

    def test_user_and_router_sources_cannot_support_normative_claims(self):
        trust=EvidenceTrustPolicy()
        for source_type,role in (("user_statement","user_statement"),("router_candidate","normative_evidence"),("topic_candidate","normative_evidence"),("llm_derived_analysis","derived_analysis")):
            with self.subTest(source_type=source_type):
                evidence=self._evidence("ev-"+source_type,source_type,role,"候选内容",standard_code="GB 55034-2022",clause_no="1.0.1")
                self.assertFalse(trust.can_support_normative_claim(evidence).allowed)

    def test_trust_benchmark_fixture_declares_all_safety_cases(self):
        benchmark=json.loads((ROOT/"data"/"engineering_knowledge_c1_benchmark.json").read_text(encoding="utf-8"))
        actual={x["id"] for x in benchmark["trust_cases"]}
        expected={"user-statement-not-normative","router-candidate-not-normative","verified-current-clause","clause-override-blocked","project-evidence-scope"}
        self.assertEqual(actual,expected)

    def test_verified_current_clause_can_support_normative_claim(self):
        text="现行规范测试条文原文";clause_id=self._insert_clause("GB 55034-2022","1.0.1",text)
        evidence=self._evidence("ev-current","standard_clause","normative_evidence",text,source_locator={"kind":"standard_clause","clause_id":clause_id},standard_code="GB 55034-2022",clause_no="1.0.1",normative_authority="mandatory_code",status="现行")
        trust=EvidenceTrustPolicy();self.assertTrue(trust.can_support_normative_claim(evidence).allowed)
        evidence.normative_authority="engineering_experience";self.assertFalse(trust.can_support_normative_claim(evidence).allowed)

    def test_clause_override_blocks_real_traceable_clause(self):
        text="已被条文级调整的真实原文";clause_id=self._insert_clause("GB 50303-2015","3.1.5",text)
        evidence=self._evidence("ev-override","standard_clause","normative_evidence",text,source_locator={"kind":"standard_clause","clause_id":clause_id},standard_code="GB 50303-2015",clause_no="3.1.5",normative_authority="national_standard",status="现行")
        decision=EvidenceTrustPolicy().can_support_normative_claim(evidence)
        self.assertFalse(decision.allowed);self.assertTrue(any(x.get("status")=="条文失效" for x in decision.status_details))

    def test_project_evidence_has_project_scope_only(self):
        text="设计图要求本处设置检修口"
        evidence=self._evidence("ev-project","design_drawing","project_evidence",text,source_locator={"kind":"drawing","drawing_no":"A-101"},drawing_no="A-101",document_name="建筑设计图",project_binding="design_drawing")
        trust=EvidenceTrustPolicy();self.assertTrue(trust.can_support_project_claim(evidence).allowed);self.assertFalse(trust.can_support_normative_claim(evidence).allowed)

    def test_supported_claim_rejects_user_statement_evidence(self):
        package=build_knowledge_package("国家规范规定吊杆间距必须600mm。")
        package.requirement_claims[0].verification_status="supported"
        with self.assertRaisesRegex(ValueError,"无合格信任证据"):validate_knowledge_package(package)

    def test_json_schemas_are_executed_and_reject_invalid_data(self):
        validate_catalogs()
        validate_json_file(ROOT/"data"/"topic_crosswalk.json",ROOT/"data"/"topic_crosswalk.schema.json")
        package=build_knowledge_package("电线贴着给水管走。");validate_knowledge_package(package)
        schema=json.loads((ROOT/"data"/"engineering_knowledge.schema.json").read_text(encoding="utf-8"));invalid=copy.deepcopy(package.to_dict());invalid["objects"][0]["id_scope"]="global"
        with self.assertRaises(JsonSchemaValidationError):validate_instance(invalid,schema)

    def test_ids_are_explicitly_package_local_and_context_affects_package_id(self):
        question="电线贴着给水管走。";plain=build_knowledge_package(question);project=build_knowledge_package(question,{"project_id":"P-001"})
        self.assertNotEqual(plain.package_id,project.package_id);self.assertEqual(plain.id_scope,"package")
        self.assertTrue(all(x.id_scope=="package" for x in plain.objects+plain.relations+plain.evidence_links+plain.retrieval_plans))


if __name__=="__main__":unittest.main()
