from __future__ import annotations

import hashlib,json

from engineering_knowledge.extractors import extract_question_knowledge as _extract
from engineering_knowledge.models import EvidenceLink,KnowledgePackage,RequirementClaim,RetrievalPlan
from engineering_knowledge.schema_validator import validate_knowledge_package
from engineering_knowledge.topic_crosswalk import CrosswalkResult,TopicCrosswalk,build_topic_graph


def extract_question_knowledge(question:str,source_id:str="question"):
    return _extract(question,source_id)


def _topic_list(route:dict,crosswalk:CrosswalkResult|None,objects,relations)->list[dict]:
    topics=[];seen=set();crosswalk_ids=set(crosswalk.topic_ids if crosswalk else [])
    for item in route.get("themes",[]):
        topic_id=item.get("id","")
        if topic_id and topic_id not in seen:
            source="topic_crosswalk" if topic_id in crosswalk_ids else "topic_router"
            payload=dict(item,source=source)
            if topic_id in crosswalk_ids:payload["sources"]=["topic_router","topic_crosswalk"]
            topics.append(payload);seen.add(topic_id)
    if crosswalk is not None:
        for topic_id in crosswalk.topic_ids:
            if topic_id not in seen:topics.append({"id":topic_id,"source":"topic_crosswalk","confidence":"medium"});seen.add(topic_id)
    else:
        for source,items in (("engineering_object_deprecated_fallback",objects),("engineering_relation_deprecated_fallback",relations)):
            for item in items:
                for topic_id in item.topic_hints:
                    if topic_id not in seen:topics.append({"id":topic_id,"source":source,"confidence":"low"});seen.add(topic_id)
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


def _retrieval_plans(route:dict,topics:list[dict],objects,relations,crosswalk:CrosswalkResult|None)->list[RetrievalPlan]:
    object_by_id={x.object_id:x for x in objects};relation_by_id={x.relation_id:x for x in relations}
    routed_ids={x.get("id") for x in route.get("themes",[])};plans=[]
    from routing.standard_policy import StandardPolicyService
    candidate_codes=route.get("primary_codes",[])+route.get("secondary_codes",[])
    explicit_codes=route.get("explicit_codes",[]);explicit_code=explicit_codes[0] if explicit_codes else ""
    allowed_codes,_,_=StandardPolicyService().validate(candidate_codes,explicit_code,route.get("explicit_clause_no",""))
    for index,topic in enumerate(topics,start=1):
        topic_id=topic.get("id","");matches=[x for x in (crosswalk.matches if crosswalk else []) if topic_id in x.topic_ids]
        object_ids=list(dict.fromkeys(x for match in matches for x in match.object_ids));relation_ids=list(dict.fromkeys(x for match in matches for x in match.relation_ids))
        if not object_ids and topic_id in routed_ids:object_ids=[x.object_id for x in objects]
        reasons=list(dict.fromkeys(x.reason for x in matches));reason="；".join(reasons) or f"Topic Router 识别 {topic_id}"
        crosswalk_terms=[term for match in matches for term in match.query_terms]
        terms=list(dict.fromkeys(route.get("query_expansion",[])+crosswalk_terms+[object_by_id[x].canonical_name for x in object_ids]+[relation_by_id[x].relation_type for x in relation_ids]))[:32]
        required_relations=[]
        for relation_id in relation_ids:
            item=relation_by_id[relation_id];subject=object_by_id[item.subject_object_id];target=object_by_id[item.object_object_id]
            required_relations.append(f"{subject.object_type} {item.relation_type} {target.object_type} [{item.assertion_status}]")
        codes=allowed_codes if topic_id in routed_ids else []
        metadata=(crosswalk.topic_metadata.get(topic_id,{}) if crosswalk else {})
        confidence=topic.get("confidence","medium");confidence=confidence if confidence in {"low","medium","high"} else "medium"
        plans.append(RetrievalPlan(
            topic_id,list(dict.fromkeys(codes)),terms,object_ids,relation_ids,route.get("evidence_gate","requires_clause_evidence"),
            plan_id=f"plan-{index:03d}",profession=metadata.get("profession",topic.get("category","")),reason=reason,
            required_entities=list(dict.fromkeys(object_by_id[x].object_type for x in object_ids)),required_relations=required_relations,
            project_stage=route.get("project_stage","unknown"),intent=route.get("intent",""),required_evidence_role="normative_evidence",
            evidence_status="unverified",confidence=confidence,expanded_query_terms=terms,
        ))
    if not plans and explicit_codes:
        terms=list(dict.fromkeys(route.get("query_expansion",[])))[:32]
        plans.append(RetrievalPlan(
            "explicit_standard",list(dict.fromkeys(allowed_codes)),terms,[],[],route.get("evidence_gate","requires_clause_evidence"),
            plan_id="plan-001",profession="",reason=f"用户明示规范：{explicit_code}",project_stage=route.get("project_stage","unknown"),
            intent=route.get("intent",""),required_evidence_role="normative_evidence",evidence_status="unverified",confidence="high",expanded_query_terms=terms,
        ))
    return plans


def build_knowledge_package(question:str,project_context=None,route:dict|None=None)->KnowledgePackage:
    warnings=[]
    try:objects,relations=_extract(question,"question")
    except Exception as exc:
        objects,relations=[],[];warnings.append(f"Engineering Knowledge 抽取失败，已回退 V1.2-B Router：{type(exc).__name__}")
    if route is None:
        from router import route_question
        route=route_question(question,project_context=project_context)
    crosswalk=None
    try:crosswalk=TopicCrosswalk().resolve(objects,relations,route.get("intent",""),route.get("project_stage","unknown"))
    except Exception as exc:warnings.append(f"Topic Crosswalk 失败，已使用 deprecated topic_hints fallback：{type(exc).__name__}")
    topics=_topic_list(route,crosswalk,objects,relations)
    evidence=EvidenceLink(
        evidence_id="ev-question-001",source_type="user_statement",source_id="question",source_locator={"kind":"question","start":0,"end":len(question)},
        original_text=question,status="unverified",content_hash=hashlib.sha256(question.encode("utf-8")).hexdigest(),verified=False,
        linked_object_ids=[x.object_id for x in objects],linked_relation_ids=[x.relation_id for x in relations],
        evidence_role="user_statement",verification_status="unverified",
    )
    for relation in relations:relation.evidence_links=[evidence.evidence_id]
    claims=_user_claims(route,question,objects,evidence)
    package=KnowledgePackage(
        schema_version="1.2-c3",package_id="kp-"+hashlib.sha256((question+json.dumps(project_context or {},sort_keys=True,ensure_ascii=False)).encode("utf-8")).hexdigest()[:12],question=question,
        normalized_question=route.get("normalized",question),objects=objects,relations=relations,topics=topics,
        project_stage=route.get("project_stage","unknown"),user_role=route.get("user_role","unknown"),user_claims=list(route.get("claims",[])),
        retrieval_plans=_retrieval_plans(route,topics,objects,relations,crosswalk),evidence_links=[evidence],requirement_claims=claims,
        warnings=list(dict.fromkeys(warnings+route.get("warnings",[]))),human_confirmation_required=bool(claims or relations),
        route_metadata={"explicit_standard":route.get("explicit_standard",False),"explicit_clause":route.get("explicit_clause",False),
                        "explicit_clause_blocked":route.get("explicit_clause_blocked",False),"evidence_gate":route.get("evidence_gate","requires_clause_evidence"),
                        "router_is_evidence":False},
        topic_graph=build_topic_graph(crosswalk,relations) if crosswalk else build_topic_graph(CrosswalkResult([],{}),relations),
    )
    validate_knowledge_package(package)
    return package
