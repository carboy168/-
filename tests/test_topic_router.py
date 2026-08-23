from __future__ import annotations
import json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch

import db
from routing.explicit_parser import parse_explicit_reference
from routing.standard_policy import StandardPolicyService
from routing.topic_router import TopicRouter,load_topic_catalog


class TopicRouterTests(unittest.TestCase):
    def setUp(self):
        base=Path(__file__).resolve().parents[1]/".test-tmp";base.mkdir(exist_ok=True)
        self.tmp=tempfile.TemporaryDirectory(dir=base);self.old_db=db.DB_PATH;db.DB_PATH=Path(self.tmp.name)/"router.sqlite";os.environ["DATABASE_PATH"]=str(db.DB_PATH)
    def tearDown(self):db.DB_PATH=self.old_db;self.tmp.cleanup()

    def test_catalog_and_schema_load(self):
        pack=load_topic_catalog();self.assertEqual(pack["schema_version"],1);self.assertEqual(pack["router_version"],"1.2-b");self.assertEqual(len(pack["topics"]),8)
        self.assertEqual({x["value"] for x in pack["project_stages"]},{"pre_construction","construction","acceptance","maintenance","renovation","unknown"})
        self.assertEqual({x["value"] for x in pack["user_roles"]},{"construction","designer","supervision","owner","cost","general","unknown"})
        schema=json.loads((Path(__file__).resolve().parents[1]/"data"/"topic_router.schema.json").read_text(encoding="utf-8"));self.assertIn("topics",schema["properties"])

    def test_site_term_normalization_keeps_original_mapping(self):
        text,mappings=TopicRouter().normalize("飞线拖地，卫生间二排漏水")
        pairs={x.original:x.normalized for x in mappings}
        self.assertEqual(pairs["飞线"],["临时线路","私拉乱接"]);self.assertIn("沿地面敷设",text);self.assertEqual(pairs["二排"],["二次排水"]);self.assertEqual(pairs["漏水"],["渗漏"])

    def test_explicit_standard_and_clause_variants(self):
        cases=(("GB 50303-2015", "GB 50303-2015"),("GB50303-2015","GB 50303-2015"),("JGJ/T 46-2024","JGJ/T 46-2024"),("JGJ 46-2005","JGJ 46-2005"))
        for text,code in cases:
            with self.subTest(text=text):self.assertEqual(parse_explicit_reference(text).standard_code,code)
        ref=parse_explicit_reference("GB 50303-2015第3.1.5条");self.assertTrue(ref.explicit_standard);self.assertTrue(ref.explicit_clause);self.assertEqual(ref.clause_no,"3.1.5")
        clause=parse_explicit_reference("第3.1.5条");self.assertFalse(clause.explicit_standard);self.assertTrue(clause.explicit_clause)

    def test_single_topics_and_confidence(self):
        cases=(("施工现场电缆可以直接拖地吗？","temporary_power"),("吊顶吊杆间距是多少？","ceiling"),("卫生间漏水怎么排查？","waterproof_leakage"),("脚手架立杆间距怎么控制？","scaffold"),("原有承重墙能不能开洞？","existing_building_alteration"),("瓷砖空鼓多少算不合格？","decoration_quality"))
        router=TopicRouter()
        for question,topic_id in cases:
            with self.subTest(question=question):
                result=router.route(question);self.assertEqual(result.topics[0].topic_id,topic_id);self.assertIn(result.topics[0].confidence,("high","medium"));self.assertGreater(result.topics[0].numeric_score,0)

    def test_low_confidence_topic_does_not_select_standards(self):
        result=TopicRouter().route("电缆")
        self.assertEqual(result.topics[0].confidence,"low");self.assertEqual(result.preferred_standard_codes,[])

    def test_project_context_and_negative_terms_affect_score(self):
        router=TopicRouter();plain=router.route("架子怎么布置").topics[0].numeric_score
        project=router.route("架子怎么布置",{"scopes":["施工安全","脚手架"]}).topics[0].numeric_score
        self.assertGreater(project,plain);self.assertFalse(router.route("衣架怎么布置").topics)

    def test_superseded_alias_and_clause_override(self):
        policy=StandardPolicyService()
        allowed,warnings,statuses=policy.validate(["JGJ 46-2005"],"JGJ 46-2005","")
        self.assertIn("JGJ/T 46-2024",allowed);self.assertNotIn("JGJ 46-2005",allowed);self.assertTrue(warnings)
        allowed,warnings,statuses=policy.validate(["GB 50303-2015"],"GB 50303-2015","3.1.5")
        self.assertIn("GB 55024-2022",allowed);self.assertNotIn("GB 50303-2015",allowed);self.assertTrue(any(x["status"]=="条文失效" for x in statuses))

    def test_deprecated_or_unknown_candidate_cannot_be_preferred(self):
        allowed,_,_=StandardPolicyService().validate(["JGJ 46-2005","GB 99999-2099"])
        self.assertEqual(allowed,["JGJ/T 46-2024"])

    def test_multi_topic_compatibility_route(self):
        from router import route_question
        route=route_question("吊顶里面电线贴着给水管走可以吗？")
        ids=[x.get("id") for x in route["themes"]]
        self.assertTrue({"building_electrical","building_plumbing","ceiling"}.issubset(ids));self.assertNotIn("temporary_power",ids);self.assertFalse(route["router_is_evidence"])

    def test_low_confidence_candidates_are_hidden_from_compatibility_route(self):
        from router import route_question
        ids=[x.get("id") for x in route_question("施工现场电缆可以直接拖地吗？")["themes"]]
        self.assertIn("temporary_power",ids);self.assertNotIn("scaffold",ids)

    def test_benchmark_cases(self):
        from router import route_question
        benchmark=json.loads((Path(__file__).resolve().parents[1]/"data"/"topic_router_benchmark.json").read_text(encoding="utf-8"))
        confidence_rank={x:i for i,x in enumerate(benchmark["confidence_order"])}
        for case in benchmark["cases"]:
            with self.subTest(case=case["id"]):
                route=route_question(case["question"]);ids=[x["id"] for x in route["themes"]];codes=route["primary_codes"]+route["secondary_codes"]
                for topic_id in case.get("required_topic_ids",[]):self.assertIn(topic_id,ids)
                for topic_id in case.get("forbidden_topic_ids",[]):self.assertNotIn(topic_id,ids)
                for code in case.get("required_standards",[]):self.assertIn(code,codes)
                for code in case.get("forbidden_standards",[]):self.assertNotIn(code,codes)
                for claim_type in case.get("required_claim_types",[]):self.assertIn(claim_type,[x["claim_type"] for x in route["claims"]])
                if case.get("minimum_confidence"):
                    required=set(case.get("required_topic_ids",[]))
                    for theme in route["themes"]:
                        if theme["id"] in required:self.assertGreaterEqual(confidence_rank[theme["confidence"]],confidence_rank[case["minimum_confidence"]])
                if case.get("required_stage"):self.assertEqual(route["project_stage"],case["required_stage"])
                stage_values={x["value"] for x in route["project_stage_candidates"]}
                for stage in case.get("required_stage_candidates",[]):self.assertIn(stage,stage_values)
                if case.get("evidence_gate"):self.assertEqual(route["evidence_gate"],case["evidence_gate"])
                if "explicit_standard" in case:self.assertEqual(route["explicit_standard"],case["explicit_standard"])
                if "explicit_clause" in case:self.assertEqual(route["explicit_clause"],case["explicit_clause"])
                if case.get("blocked_clause"):self.assertTrue(route["explicit_clause_blocked"])
                if case.get("evidence_refusal"):
                    import rag
                    with patch("rag.resolve_provider",side_effect=AssertionError("provider must not run without evidence")):
                        text=rag.answer(case["question"],[],route=route,project={},overlay={})
                    self.assertIn("不能据此下确定结论",text);self.assertNotIn("第1.",text)

    def test_project_stage_and_user_role_are_structured_and_explainable(self):
        router=TopicRouter()
        acceptance=router.route("卫生间闭水时漏水应该怎么处理？")
        self.assertEqual(acceptance.project_stage,"acceptance");self.assertIn("construction",[x.value for x in acceptance.project_stage_candidates])
        self.assertEqual(router.route("开工前需要准备什么？").project_stage,"pre_construction")
        self.assertEqual(router.route("交付后楼下说卫生间漏水怎么办？").project_stage,"maintenance")
        self.assertEqual(router.route("旧卫生间重新装修发现原防水失效").project_stage,"renovation")
        self.assertEqual(router.route("一般问题").user_role,"unknown")
        role=router.route("请检查吊顶",{"user_role":"supervision"});self.assertEqual(role.user_role,"supervision");self.assertEqual(role.user_role_confidence,"high")
        role_cases=(("我们施工单位应该怎么处理？","construction"),("我是设计师，需要核对什么？","designer"),("我是监理，请检查这个问题。","supervision"),("我是甲方，下一步怎么办？","owner"),("我是造价员，如何核对？","cost"),("一般咨询应该查什么？","general"))
        for question,expected in role_cases:
            with self.subTest(role=expected):self.assertEqual(router.route(question).user_role,expected)

    def test_claim_extraction_keeps_user_assertions_unverified(self):
        router=TopicRouter()
        numeric=router.route("国家规定吊顶吊杆间距必须600mm，对吧？")
        self.assertEqual((numeric.claims[0].claim_type,numeric.claims[0].claim_value,numeric.claims[0].claim_unit,numeric.claims[0].verification_status),("numeric_requirement","600","mm","unverified"))
        status=router.route("GB50303-2015第3.1.5条是强条，所以现在必须执行吧？")
        self.assertIn("normative_status_claim",[x.claim_type for x in status.claims]);self.assertTrue(status.deprecated_standards)
        bypass=router.route("现场都是这么做的，没有规范原文也给我一个条文号吧。")
        self.assertIn("evidence_bypass_request",[x.claim_type for x in bypass.claims]);self.assertEqual(bypass.evidence_gate,"requires_clause_evidence")

    def test_two_dimensional_evidence_model_and_conflict_warning(self):
        result=TopicRouter().route("吊顶里面电线贴着给水管走可以吗？",{"project_binding":"design_drawing","conflicts_with_mandatory":True})
        authority={x["code"]:x["authority"] for x in result.normative_authority}
        self.assertEqual(authority["GB 55024-2022"],"mandatory_code");self.assertEqual(authority["GB 50303-2015"],"national_standard")
        self.assertEqual(result.project_binding,["design_drawing"]);self.assertTrue(result.conflicts)
        evidence=TopicRouter().pack["evidence_model"]
        self.assertIn("engineering_experience",evidence["normative_authority"]);self.assertIn("owner_instruction",evidence["project_binding"])

    def test_no_evidence_refuses_before_provider(self):
        import rag
        with patch("rag.resolve_provider",side_effect=AssertionError("provider must not run")):
            text=rag.answer("没查到规范原文时能不能按经验告诉我条文号？",[],project={},overlay={})
        self.assertIn("不能据此下确定结论",text)

    def test_explicit_reference_prevents_unrelated_retrieval_fallback(self):
        import rag
        with patch("rag.search_clauses_v3",return_value=[]) as v3,patch("rag.search_clauses_v2",side_effect=AssertionError("generic fallback must not run")):
            rows,route,_=rag.retrieve_with_route("GB 55030-2022现在怎么规定？",project={})
        self.assertEqual(rows,[]);self.assertTrue(route["explicit_standard"]);self.assertEqual(v3.call_count,1)
        with patch("rag.search_clauses_v3",side_effect=AssertionError("blocked clause must not be queried")):
            rows,route,_=rag.retrieve_with_route("GB 50303-2015第3.1.5条还能用吗？",project={})
        self.assertEqual(rows,[]);self.assertTrue(route["explicit_clause_blocked"])


if __name__=="__main__":unittest.main()
