from __future__ import annotations

import hashlib
from dataclasses import asdict

from engineering_knowledge.extractors import extract_question_knowledge as _extract
from engineering_knowledge.models import EvidenceLink,KnowledgePackage,RequirementClaim,RetrievalPlan
from engineering_knowledge.schema_validator import validate_knowledge_package


def extract_question_knowledge(question:str,source_id:str="question"):
    return _extract(question,source_id)


def _topic_list(route:dict,objects,relations)->list[dict]:
    topics=[];seen=set()
    for item in route.get("themes",[]):
        topic_id=item.get("id","")
        if topic_id and topic_id not in seen:topics.append(dict(item,source="topic_router"));seen.add(topic_id)
    for source,items in (("engineering_object",objects),("engineering_relation",relations)):
        for item in items:
            for topic_id in item.topic_hints:
                if topic_id not in seen:topics.append({"id":topic_id,"source":source,"confidence":"medium"});seen.add(topic_id)
    return topics


def _user_claims(route:dict,question:str,objects,evidence:EvidenceLink):
    claims=[]
    for index,item in enumerate(route.get("claims",[]),start=1):
        claim_id=f"claim-{index:03d}";text=item.get("claim_text") or question;start=question.find(text);start=max(0,start)
        subjects=[x.object_id for x in objects if x.original_text in text]
        property_name="spacing" if "间距" in text else ("standard_status" if item.get("claim_type")=="normative_status_claim" else "evidence_requirement")
        action="verify" if item.get("claim_type")!="evidence_bypass_request" else "refuse_fabrication"
        claims.append(RequirementClaim(
            claim_id=claim_id,claim_type=item.get("claim_type","unknown"),source_type="user_statement",source_id="question",
            source_locator={"kind":"question","start":start,"end":start+len(text)},original_text=text,subject=subjects,
            property=property_name,operator="eq" if item.get("claim_value") else "",value=str(item.get("claim_value","") or ""),unit=item.get("claim_unit","") or "",
            action=action,conditions=[],exceptions=[],project_stage=route.get("project_stage","unknown"),normative_authority="",
            project_binding="none",standard_status="unverified",verification_status="unverified",extraction_confidence="high",evidence_links=[evidence.evidence_id],
        ))
        evidence.linked_claim_ids.append(claim_id)
    return claims


def _retrieval_plans(route:dict,topics:list[dict],objects,relations)->list[RetrievalPlan]:
    object_ids=[x.object_id for x in objects];relation_ids=[x.relation_id for x in relations]
    terms=list(dict.fromkeys(route.get("query_expansion",[])+[x.canonical_name for x in objects]+[x.relation_type for x in relations]))
    routed_ids={x.get("id") for x in route.get("themes",[])};plans=[]
    for topic in topics:
        topic_id=topic.get("id","");codes=(route.get("primary_codes",[])+route.get("secondary_codes",[])) if topic_id in routed_ids else []
        plans.append(RetrievalPlan(topic_id,list(dict.fromkeys(codes)),terms[:24],object_ids,relation_ids,route.get("evidence_gate","requires_clause_evidence")))
    return plans


def build_knowledge_package(question:str,project_context=None,route:dict|None=None)->KnowledgePackage:
    warnings=[]
    try:objects,relations=_extract(question,"question")
    except Exception as exc:
        objects,relations=[],[];warnings.append(f"Engineering Knowledge 抽取失败，已回退 V1.2-B Router：{type(exc).__name__}")
    if route is None:
        from router import route_question
        route=route_question(question,project_context=project_context)
    topics=_topic_list(route,objects,relations)
    evidence=EvidenceLink(
        evidence_id="ev-question-001",source_type="user_statement",source_id="question",source_locator={"kind":"question","start":0,"end":len(question)},
        original_text=question,status="unverified",content_hash=hashlib.sha256(question.encode("utf-8")).hexdigest(),verified=False,
        linked_object_ids=[x.object_id for x in objects],linked_relation_ids=[x.relation_id for x in relations],
    )
    for relation in relations:relation.evidence_links=[evidence.evidence_id]
    claims=_user_claims(route,question,objects,evidence)
    package=KnowledgePackage(
        schema_version="1.2-c0",package_id="kp-"+hashlib.sha256(question.encode("utf-8")).hexdigest()[:12],question=question,
        normalized_question=route.get("normalized",question),objects=objects,relations=relations,topics=topics,
        project_stage=route.get("project_stage","unknown"),user_role=route.get("user_role","unknown"),user_claims=list(route.get("claims",[])),
        retrieval_plans=_retrieval_plans(route,topics,objects,relations),evidence_links=[evidence],requirement_claims=claims,
        warnings=list(dict.fromkeys(warnings+route.get("warnings",[]))),human_confirmation_required=bool(claims or relations),
        route_metadata={"explicit_standard":route.get("explicit_standard",False),"explicit_clause":route.get("explicit_clause",False),
                        "explicit_clause_blocked":route.get("explicit_clause_blocked",False),"evidence_gate":route.get("evidence_gate","requires_clause_evidence"),
                        "router_is_evidence":False},
    )
    validate_knowledge_package(package)
    return package
