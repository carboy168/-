from __future__ import annotations

import hashlib,json
from pathlib import Path

from engineering_knowledge.catalog import load_object_catalog,load_relation_catalog
from engineering_knowledge.models import CLAIM_TYPES,CONFLICT_STATUSES,KnowledgePackage
from engineering_knowledge.models import ASSERTION_STATUSES,EVIDENCE_ROLES,EVIDENCE_STATUSES
from engineering_knowledge.evidence_trust import EvidenceTrustPolicy,FORBIDDEN_NORMATIVE_SOURCE_TYPES
from engineering_knowledge.json_schema import validate_instance,validate_json_file

FORBIDDEN_EVIDENCE_TYPES={"router_candidate"}
ROOT=Path(__file__).resolve().parents[1]


def validate_catalogs()->None:
    validate_json_file(ROOT/"data"/"engineering_objects.json",ROOT/"data"/"engineering_objects.schema.json")
    validate_json_file(ROOT/"data"/"engineering_relations.json",ROOT/"data"/"engineering_relations.schema.json")
    validate_json_file(ROOT/"data"/"topic_crosswalk.json",ROOT/"data"/"topic_crosswalk.schema.json")
    validate_json_file(ROOT/"data"/"requirement_claim_rules.json",ROOT/"data"/"requirement_claim_rules.schema.json")
    validate_json_file(ROOT/"data"/"conflict_detection_rules.json",ROOT/"data"/"conflict_detection_rules.schema.json")
    objects=load_object_catalog();relations=load_relation_catalog(object_pack=objects)
    required_objects={"electrical_line","cable","conduit","cable_tray","distribution_box","water_supply_pipe","drainage_pipe","sprinkler_pipe","fire_pipe","duct","ceiling","ceiling_concealed_space","hanger","support","partition_wall","load_bearing_wall","beam","slab","floor","waterproof_layer","floor_drain","door","fire_door"}
    actual={x["object_type"] for x in objects["object_types"]}
    if not required_objects.issubset(actual):raise ValueError(f"EngineeringObject 类型不完整：{sorted(required_objects-actual)}")
    required_relations={"adjacent_to","intersects_with","crosses","penetrates","located_in","located_above","located_below","supported_by","shares_support_with","connected_to","conflicts_with","affects","requires_coordination_with"}
    actual_relations=set(relations["relation_types"])
    if not required_relations.issubset(actual_relations):raise ValueError(f"EngineeringRelation 类型不完整：{sorted(required_relations-actual_relations)}")
    crosswalk=json.loads((ROOT/"data"/"topic_crosswalk.json").read_text(encoding="utf-8"));topic_ids=set(crosswalk["topics"])
    for mapping in crosswalk["object_mappings"]:
        unknown_objects=set(mapping["object_types"])-actual
        unknown_topics=set(mapping["topic_ids"])-topic_ids
        if unknown_objects or unknown_topics:raise ValueError(f"Topic Crosswalk 对象映射引用无效：{sorted(unknown_objects|unknown_topics)}")
    for mapping in crosswalk["relation_mappings"]:
        unknown_objects=(set(mapping["subject_types"])|set(mapping["object_types"]))-actual
        unknown_relations=set(mapping["relation_types"])-actual_relations
        unknown_topics=set(mapping["topic_ids"])-topic_ids
        edge_topics={x[key] for x in mapping["graph_edges"] for key in ("source","target")}-topic_ids
        if unknown_objects or unknown_relations or unknown_topics or edge_topics:raise ValueError(f"Topic Crosswalk 关系映射引用无效：{sorted(unknown_objects|unknown_relations|unknown_topics|edge_topics)}")


def validate_knowledge_package(package:KnowledgePackage)->None:
    errors=[]
    if package.schema_version!="1.2-c3":errors.append("schema_version 无效")
    object_catalog=load_object_catalog();relation_catalog=load_relation_catalog(object_pack=object_catalog)
    allowed_object_types={x["object_type"] for x in object_catalog["object_types"]};allowed_relation_types=set(relation_catalog["relation_types"])
    object_ids=[x.object_id for x in package.objects];relation_ids=[x.relation_id for x in package.relations];claim_ids=[x.claim_id for x in package.requirement_claims];evidence_ids=[x.evidence_id for x in package.evidence_links]
    conflict_ids=[x.conflict_id for x in package.conflicts]
    for label,values in (("object",object_ids),("relation",relation_ids),("claim",claim_ids),("evidence",evidence_ids),("conflict",conflict_ids)):
        if len(values)!=len(set(values)):errors.append(f"{label} ID 重复")
    object_set=set(object_ids);relation_set=set(relation_ids);claim_set=set(claim_ids);evidence_set=set(evidence_ids);claim_by_id={x.claim_id:x for x in package.requirement_claims}
    for item in package.objects:
        if item.object_type not in allowed_object_types:errors.append(f"Object {item.object_id} 类型无效")
        if item.id_scope!="package":errors.append(f"Object {item.object_id} ID 作用域必须为 package")
        if item.confidence not in {"high","medium","low"}:errors.append(f"Object {item.object_id} 置信度无效")
        if item.source_span.start<0 or item.source_span.end<item.source_span.start:errors.append(f"Object {item.object_id} source_span 无效")
        if item.source_span.source_id=="question" and package.question[item.source_span.start:item.source_span.end]!=item.original_text:errors.append(f"Object {item.object_id} 来源位置不可回溯")
    for relation in package.relations:
        if relation.subject_object_id not in object_set or relation.object_object_id not in object_set:errors.append(f"Relation {relation.relation_id} 引用未知对象")
        if relation.relation_type not in allowed_relation_types:errors.append(f"Relation {relation.relation_id} 类型无效")
        if relation.id_scope!="package":errors.append(f"Relation {relation.relation_id} ID 作用域必须为 package")
        if relation.assertion_status not in ASSERTION_STATUSES:errors.append(f"Relation {relation.relation_id} assertion_status 无效")
        if not relation.rule_id:errors.append(f"Relation {relation.relation_id} 缺少可解释规则")
        if any(x not in evidence_set for x in relation.evidence_links):errors.append(f"Relation {relation.relation_id} 引用未知证据")
    for claim in package.requirement_claims:
        if claim.claim_type not in CLAIM_TYPES:errors.append(f"Claim {claim.claim_id} 类型无效")
        if claim.id_scope!="package":errors.append(f"Claim {claim.claim_id} ID 作用域必须为 package")
        if any(x not in object_set for x in claim.subject):errors.append(f"Claim {claim.claim_id} 引用未知对象")
        if not claim.evidence_links:errors.append(f"Claim {claim.claim_id} 无 EvidenceLink")
        if any(x not in evidence_set for x in claim.evidence_links):errors.append(f"Claim {claim.claim_id} 引用未知证据")
        linked=[x for x in package.evidence_links if x.evidence_id in claim.evidence_links]
        start=claim.source_locator.get("start");end=claim.source_locator.get("end")
        if isinstance(start,int) and isinstance(end,int) and not any(x.original_text[start:end]==claim.original_text for x in linked):errors.append(f"Claim {claim.claim_id} 原文位置不可回溯")
        if any(claim.claim_id not in x.linked_claim_ids for x in linked):errors.append(f"Claim {claim.claim_id} 与 EvidenceLink 未双向关联")
        if claim.verification_status=="supported":
            trust=EvidenceTrustPolicy()
            if claim.project_binding!="none":decisions=[trust.can_support_project_claim(x) for x in linked]
            else:decisions=[trust.can_support_normative_claim(x) for x in linked]
            if not any(x.allowed for x in decisions):errors.append(f"Claim {claim.claim_id} 无合格信任证据却标记 supported")
    for evidence in package.evidence_links:
        if evidence.source_type in FORBIDDEN_EVIDENCE_TYPES:errors.append("Router candidate 不能成为 EvidenceLink")
        if evidence.evidence_role not in EVIDENCE_ROLES or evidence.verification_status not in EVIDENCE_STATUSES:errors.append(f"Evidence {evidence.evidence_id} 信任字段无效")
        if evidence.id_scope!="package":errors.append(f"Evidence {evidence.evidence_id} ID 作用域必须为 package")
        if evidence.source_type in FORBIDDEN_NORMATIVE_SOURCE_TYPES and evidence.evidence_role=="normative_evidence":errors.append(f"Evidence {evidence.evidence_id} 禁止成为规范证据")
        if evidence.source_type=="user_statement" and evidence.evidence_role!="user_statement":errors.append(f"Evidence {evidence.evidence_id} 用户陈述角色错误")
        if evidence.verified!=(evidence.verification_status=="verified"):errors.append(f"Evidence {evidence.evidence_id} verified 与 verification_status 不一致")
        if not evidence.source_id or not evidence.source_locator or not evidence.original_text:errors.append(f"Evidence {evidence.evidence_id} 无法回溯原始来源")
        if any(x not in object_set for x in evidence.linked_object_ids):errors.append(f"Evidence {evidence.evidence_id} 引用未知对象")
        if any(x not in relation_set for x in evidence.linked_relation_ids):errors.append(f"Evidence {evidence.evidence_id} 引用未知关系")
        if any(x not in claim_set for x in evidence.linked_claim_ids):errors.append(f"Evidence {evidence.evidence_id} 引用未知 Claim")
        expected=hashlib.sha256(evidence.original_text.encode("utf-8")).hexdigest()
        if evidence.content_hash!=expected:errors.append(f"Evidence {evidence.evidence_id} content_hash 不匹配")
    for plan in package.retrieval_plans:
        if any(x not in object_set for x in plan.object_ids) or any(x not in relation_set for x in plan.relation_ids):errors.append(f"RetrievalPlan {plan.topic_id} 引用未知知识对象")
        if any(x not in evidence_set for x in plan.evidence_link_ids):errors.append(f"RetrievalPlan {plan.topic_id} 引用未知证据")
        if plan.id_scope!="package" or plan.required_evidence_role!="normative_evidence":errors.append(f"RetrievalPlan {plan.topic_id} 信任边界无效")
    for conflict in package.conflicts:
        if conflict.status not in CONFLICT_STATUSES:errors.append(f"Conflict {conflict.conflict_id} 状态无效")
        if conflict.id_scope!="package":errors.append(f"Conflict {conflict.conflict_id} ID 作用域必须为 package")
        if conflict.project_claim_id and conflict.project_claim_id not in claim_set:errors.append(f"Conflict {conflict.conflict_id} 引用未知项目 Claim")
        if conflict.normative_claim_id and conflict.normative_claim_id not in claim_set:errors.append(f"Conflict {conflict.conflict_id} 引用未知规范 Claim")
        if any(x not in evidence_set for x in conflict.evidence_links):errors.append(f"Conflict {conflict.conflict_id} 引用未知证据")
        project_claim=claim_by_id.get(conflict.project_claim_id);norm_claim=claim_by_id.get(conflict.normative_claim_id)
        if project_claim and project_claim.project_binding=="none":errors.append(f"Conflict {conflict.conflict_id} 项目 Claim 作用域错误")
        if norm_claim and (norm_claim.project_binding!="none" or norm_claim.source_type not in {"standard_clause","normative_clause"}):errors.append(f"Conflict {conflict.conflict_id} 规范 Claim 作用域错误")
        expected_links=set((project_claim.evidence_links if project_claim else [])+(norm_claim.evidence_links if norm_claim else []))
        if not expected_links.issubset(set(conflict.evidence_links)):errors.append(f"Conflict {conflict.conflict_id} 未完整保留 Claim EvidenceLink")
    if package.route_metadata.get("router_is_evidence") is not False:errors.append("Router 不能标记为 Evidence")
    if errors:raise ValueError("；".join(errors))
    schema=json.loads((ROOT/"data"/"engineering_knowledge.schema.json").read_text(encoding="utf-8"))
    validate_instance(package.to_dict(),schema)
