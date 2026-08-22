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
        pack=load_topic_catalog();self.assertEqual(pack["schema_version"],1);self.assertEqual(len(pack["topics"]),5)
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
        cases=(("施工现场电缆可以直接拖地吗？","temporary_power"),("吊顶吊杆间距是多少？","ceiling_work"),("卫生间漏水怎么排查？","waterproof_leakage"),("脚手架立杆间距怎么控制？","scaffold"),("原有承重墙能不能开洞？","existing_building_alteration"))
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
        self.assertGreaterEqual(len(ids),2);self.assertIn("ceiling_work",ids);self.assertNotIn("temporary_power",ids);self.assertFalse(route["router_is_evidence"])

    def test_low_confidence_candidates_are_hidden_from_compatibility_route(self):
        from router import route_question
        ids=[x.get("id") for x in route_question("施工现场电缆可以直接拖地吗？")["themes"]]
        self.assertIn("temporary_power",ids);self.assertNotIn("scaffold",ids)

    def test_benchmark_cases(self):
        from router import route_question
        benchmark=json.loads((Path(__file__).resolve().parents[1]/"data"/"topic_router_benchmark.json").read_text(encoding="utf-8"))
        for case in benchmark["cases"]:
            with self.subTest(case=case["id"]):
                route=route_question(case["question"]);ids=[x["id"] for x in route["themes"]];codes=route["primary_codes"]+route["secondary_codes"]
                for topic_id in case.get("expected_topic_ids",[]):self.assertIn(topic_id,ids)
                for code in case.get("expected_codes",[]):self.assertIn(code,codes)
                self.assertGreaterEqual(len(route["themes"]),case.get("minimum_topics",0))
                if "explicit_standard" in case:self.assertEqual(route["explicit_standard"],case["explicit_standard"])
                if "explicit_clause" in case:self.assertEqual(route["explicit_clause"],case["explicit_clause"])
                if case.get("blocked_clause"):self.assertTrue(route["explicit_clause_blocked"])

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
