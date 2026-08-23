from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import db
from engineering_knowledge.catalog import load_object_catalog,load_relation_catalog
from engineering_knowledge.extractors import EngineeringObjectExtractor,EngineeringRelationExtractor
from engineering_knowledge.layer import build_knowledge_package
from engineering_knowledge.models import CLAIM_TYPES
from engineering_knowledge.schema_validator import validate_catalogs,validate_knowledge_package


ROOT=Path(__file__).resolve().parents[1]


class EngineeringKnowledgeTests(unittest.TestCase):
    def setUp(self):
        base=ROOT/".test-tmp";base.mkdir(exist_ok=True)
        self.tmp=tempfile.TemporaryDirectory(dir=base);self.old_db=db.DB_PATH
        db.DB_PATH=Path(self.tmp.name)/"knowledge.sqlite";os.environ["DATABASE_PATH"]=str(db.DB_PATH)

    def tearDown(self):
        db.DB_PATH=self.old_db;self.tmp.cleanup()

    def test_catalogs_and_json_schemas_load(self):
        validate_catalogs()
        objects=load_object_catalog();relations=load_relation_catalog(object_pack=objects)
        self.assertEqual(objects["catalog_version"],"1.2-c1")
        self.assertEqual(relations["catalog_version"],"1.2-c1")
        for name in ("engineering_objects.schema.json","engineering_relations.schema.json","engineering_knowledge.schema.json"):
            schema=json.loads((ROOT/"data"/name).read_text(encoding="utf-8"))
            self.assertEqual(schema["$schema"],"https://json-schema.org/draft/2020-12/schema")
        required_claims={"numeric_requirement","method_requirement","material_requirement","prohibition_requirement","mandatory_requirement","acceptance_requirement","responsibility_requirement","sequence_requirement","normative_status_claim","evidence_bypass_request"}
        self.assertTrue(required_claims.issubset(CLAIM_TYPES))

    @staticmethod
    def _relation_tuples(package):
        object_types={x.object_id:x.object_type for x in package.objects}
        return {(object_types[x.subject_object_id],x.relation_type,object_types[x.object_object_id]) for x in package.relations}

    def test_benchmark_objects_relations_topics_and_confidence(self):
        benchmark=json.loads((ROOT/"data"/"engineering_knowledge_benchmark.json").read_text(encoding="utf-8"))
        rank={value:index for index,value in enumerate(benchmark["confidence_order"])}
        for case in benchmark["cases"]:
            with self.subTest(case=case["id"]):
                package=build_knowledge_package(case["question"])
                object_types={x.object_type for x in package.objects};topics={x["id"] for x in package.topics}
                self.assertTrue(set(case["required_objects"]).issubset(object_types))
                self.assertFalse(set(case["forbidden_objects"]) & object_types)
                self.assertTrue(set(case["required_topics"]).issubset(topics))
                actual_relations=self._relation_tuples(package)
                for expected in case["required_relations"]:
                    triple=(expected["subject_type"],expected["relation_type"],expected["object_type"])
                    self.assertIn(triple,actual_relations)
                if case.get("minimum_object_confidence"):
                    required=set(case["required_objects"])
                    for item in package.objects:
                        if item.object_type in required:self.assertGreaterEqual(rank[item.confidence],rank[case["minimum_object_confidence"]])
                if case.get("minimum_relation_confidence"):
                    required={(x["subject_type"],x["relation_type"],x["object_type"]) for x in case["required_relations"]}
                    types={x.object_id:x.object_type for x in package.objects}
                    for item in package.relations:
                        triple=(types[item.subject_object_id],item.relation_type,types[item.object_object_id])
                        if triple in required:self.assertGreaterEqual(rank[item.confidence],rank[case["minimum_relation_confidence"]])

    def test_object_extraction_is_deterministic_and_preserves_source_span(self):
        question="吊顶里面电线贴着给水管走可以吗？"
        first=EngineeringObjectExtractor().extract(question);second=EngineeringObjectExtractor().extract(question)
        self.assertEqual([(x.object_id,x.object_type,x.source_span) for x in first],[(x.object_id,x.object_type,x.source_span) for x in second])
        self.assertEqual({x.object_type for x in first},{"ceiling_concealed_space","electrical_line","water_supply_pipe"})
        for item in first:self.assertEqual(question[item.source_span.start:item.source_span.end],item.original_text)

    def test_relation_extraction_requires_an_explicit_configured_expression(self):
        extractor=EngineeringObjectExtractor();relations=EngineeringRelationExtractor()
        plain="电线和给水管已经施工完成。";plain_objects=extractor.extract(plain)
        self.assertEqual(relations.extract(plain,plain_objects),[])
        explicit="电线贴着给水管走。";explicit_objects=extractor.extract(explicit);actual=relations.extract(explicit,explicit_objects)
        self.assertEqual([(x.relation_type,x.rule_id) for x in actual],[("adjacent_to","adjacent-between")])
        self.assertEqual(actual[0].confidence,"high")

    def test_relation_counterexamples_do_not_infer_from_cooccurrence(self):
        benchmark=json.loads((ROOT/"data"/"engineering_knowledge_benchmark.json").read_text(encoding="utf-8"))
        for case in benchmark["negative_relation_cases"]:
            with self.subTest(case=case["id"]):
                package=build_knowledge_package(case["question"])
                self.assertTrue(set(case["required_objects"]).issubset({x.object_type for x in package.objects}))
                self.assertEqual(package.relations,[])
                self.assertFalse(set(case["forbidden_relation_types"]) & {x.relation_type for x in package.relations})

    def test_evidence_is_traceable_and_router_candidate_is_forbidden(self):
        package=build_knowledge_package("吊顶里面电线贴着给水管走可以吗？")
        evidence=package.evidence_links[0]
        self.assertEqual(evidence.content_hash,hashlib.sha256(package.question.encode("utf-8")).hexdigest())
        self.assertFalse(evidence.verified);self.assertEqual(package.route_metadata["router_is_evidence"],False)
        validate_knowledge_package(package)
        evidence.source_type="router_candidate"
        with self.assertRaisesRegex(ValueError,"Router candidate"):validate_knowledge_package(package)

    def test_user_claim_is_unverified_and_linked_to_source_evidence(self):
        package=build_knowledge_package("国家规定吊顶吊杆间距必须600mm，对吧？")
        self.assertEqual(len(package.requirement_claims),1)
        claim=package.requirement_claims[0]
        self.assertEqual((claim.claim_type,claim.value,claim.unit,claim.verification_status),("numeric_requirement","600","mm","unverified"))
        self.assertEqual(claim.evidence_links,[package.evidence_links[0].evidence_id])
        self.assertIn(claim.claim_id,package.evidence_links[0].linked_claim_ids)

    def test_extractor_failure_falls_back_to_v12b_router(self):
        with patch("engineering_knowledge.layer._extract",side_effect=RuntimeError("broken extractor")):
            package=build_knowledge_package("施工现场电缆可以直接拖地吗？")
        self.assertEqual(package.objects,[]);self.assertEqual(package.relations,[])
        self.assertIn("temporary_power",{x["id"] for x in package.topics})
        self.assertTrue(any("已回退 V1.2-B Router" in x for x in package.warnings))

    def test_router_facade_is_optional_and_keeps_evidence_gate(self):
        from router import build_engineering_knowledge,route_question
        question="风管穿过防火墙后怎么处理？"
        route=route_question(question);package=build_engineering_knowledge(question)
        self.assertEqual(package.route_metadata["evidence_gate"],route["evidence_gate"])
        self.assertFalse(package.route_metadata["router_is_evidence"])
        self.assertIn(("duct","penetrates","fire_wall"),self._relation_tuples(package))


if __name__=="__main__":unittest.main()
