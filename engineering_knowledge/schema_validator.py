from __future__ import annotations

import hashlib

from engineering_knowledge.catalog import load_object_catalog,load_relation_catalog
from engineering_knowledge.models import CLAIM_TYPES,KnowledgePackage

FORBIDDEN_EVIDENCE_TYPES={"router_candidate"}


def validate_catalogs()->None:
    objects=load_object_catalog();relations=load_relation_catalog(object_pack=objects)
    required_objects={"electrical_line","cable","conduit","cable_tray","distribution_box","water_supply_pipe","drainage_pipe","sprinkler_pipe","fire_pipe","duct","ceiling","ceiling_concealed_space","hanger","support","partition_wall","load_bearing_wall","beam","slab","floor","waterproof_layer","floor_drain","door","fire_door"}
    actual={x["object_type"] for x in objects["object_types"]}
    if not required_objects.issubset(actual):raise ValueError(f"EngineeringObject 类型不完整：{sorted(required_objects-actual)}")
    required_relations={"adjacent_to","intersects_with","crosses","penetrates","located_in","located_above","located_below","supported_by","shares_support_with","connected_to","conflicts_with","affects","requires_coordination_with"}
    actual_relations=set(relations["relation_types"])
    if not required_relations.issubset(actual_relations):raise ValueError(f"EngineeringRelation 类型不完整：{sorted(required_relations-actual_relations)}")


def validate_knowledge_package(package:KnowledgePackage)->None:
    errors=[]
    if package.schema_version!="1.2-c0":errors.append("schema_version 无效")
    object_catalog=load_object_catalog();relation_catalog=load_relation_catalog(object_pack=object_catalog)
    allowed_object_types={x["object_type"] for x in object_catalog["object_types"]};allowed_relation_types=set(relation_catalog["relation_types"])
    object_ids=[x.object_id for x in package.objects];relation_ids=[x.relation_id for x in package.relations];claim_ids=[x.claim_id for x in package.requirement_claims];evidence_ids=[x.evidence_id for x in package.evidence_links]
    for label,values in (("object",object_ids),("relation",relation_ids),("claim",claim_ids),("evidence",evidence_ids)):
        if len(values)!=len(set(values)):errors.append(f"{label} ID 重复")
    object_set=set(object_ids);relation_set=set(relation_ids);claim_set=set(claim_ids);evidence_set=set(evidence_ids)
    for item in package.objects:
        if item.object_type not in allowed_object_types:errors.append(f"Object {item.object_id} 类型无效")
        if item.confidence not in {"high","medium","low"}:errors.append(f"Object {item.object_id} 置信度无效")
        if item.source_span.source_id=="question" and package.question[item.source_span.start:item.source_span.end]!=item.original_text:errors.append(f"Object {item.object_id} 来源位置不可回溯")
    for relation in package.relations:
        if relation.subject_object_id not in object_set or relation.object_object_id not in object_set:errors.append(f"Relation {relation.relation_id} 引用未知对象")
        if relation.relation_type not in allowed_relation_types:errors.append(f"Relation {relation.relation_id} 类型无效")
        if not relation.rule_id:errors.append(f"Relation {relation.relation_id} 缺少可解释规则")
        if any(x not in evidence_set for x in relation.evidence_links):errors.append(f"Relation {relation.relation_id} 引用未知证据")
    for claim in package.requirement_claims:
        if claim.claim_type not in CLAIM_TYPES:errors.append(f"Claim {claim.claim_id} 类型无效")
        if any(x not in object_set for x in claim.subject):errors.append(f"Claim {claim.claim_id} 引用未知对象")
        if not claim.evidence_links:errors.append(f"Claim {claim.claim_id} 无 EvidenceLink")
        if any(x not in evidence_set for x in claim.evidence_links):errors.append(f"Claim {claim.claim_id} 引用未知证据")
        if claim.verification_status=="supported" and not any(x.verified and x.evidence_id in claim.evidence_links for x in package.evidence_links):errors.append(f"Claim {claim.claim_id} 无已验证证据却标记 supported")
    for evidence in package.evidence_links:
        if evidence.source_type in FORBIDDEN_EVIDENCE_TYPES:errors.append("Router candidate 不能成为 EvidenceLink")
        if not evidence.source_id or not evidence.source_locator or not evidence.original_text:errors.append(f"Evidence {evidence.evidence_id} 无法回溯原始来源")
        if any(x not in object_set for x in evidence.linked_object_ids):errors.append(f"Evidence {evidence.evidence_id} 引用未知对象")
        if any(x not in relation_set for x in evidence.linked_relation_ids):errors.append(f"Evidence {evidence.evidence_id} 引用未知关系")
        if any(x not in claim_set for x in evidence.linked_claim_ids):errors.append(f"Evidence {evidence.evidence_id} 引用未知 Claim")
        expected=hashlib.sha256(evidence.original_text.encode("utf-8")).hexdigest()
        if evidence.content_hash!=expected:errors.append(f"Evidence {evidence.evidence_id} content_hash 不匹配")
    for plan in package.retrieval_plans:
        if any(x not in object_set for x in plan.object_ids) or any(x not in relation_set for x in plan.relation_ids):errors.append(f"RetrievalPlan {plan.topic_id} 引用未知知识对象")
    if package.route_metadata.get("router_is_evidence") is not False:errors.append("Router 不能标记为 Evidence")
    if errors:raise ValueError("；".join(errors))
