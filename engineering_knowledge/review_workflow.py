from __future__ import annotations

import hashlib,json,re
from dataclasses import asdict,dataclass,field
from datetime import datetime,timezone
from typing import Any,Callable,Iterable

from engineering_knowledge.conflict_detection import ConflictDetector,detect_claim_conflicts
from engineering_knowledge.models import ConflictResult,EvidenceLink,KnowledgePackage,RequirementClaim


REVIEW_STATUSES={
    "detected","pending_confirmation","confirmed","assigned","rectifying",
    "pending_review","closed","reopened",
}
DISCOVERY_MODES={"deterministic","model_assisted"}
ASSIGNMENT_ROLES={"owner","contractor","designer","supervision","other"}
REEVALUATION_TRIGGERS={"evidence_changed","norm_status_changed","claim_changed","review_unresolved"}
ALLOWED_REVIEW_TRANSITIONS={
    "detected":{"pending_confirmation","confirmed"},
    "pending_confirmation":{"confirmed"},
    "confirmed":{"assigned"},
    "assigned":{"rectifying"},
    "rectifying":{"pending_review"},
    "pending_review":{"closed"},
    "closed":{"reopened"},
    "reopened":{"pending_confirmation","confirmed"},
}


def utc_now()->str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _normalized(value:Any)->str:
    return re.sub(r"[\W_]+","",str(value or "").lower(),flags=re.UNICODE)


def _claim_signature(claim:RequirementClaim|dict[str,Any])->dict[str,Any]:
    get=(lambda key:getattr(claim,key,"")) if not isinstance(claim,dict) else (lambda key:claim.get(key,""))
    return {
        # Claim subject values are package-local object IDs; semantic object identity is supplied
        # separately through the canonical object payload.
        "claim_type":get("claim_type"),
        "property":_normalized(get("property")),"operator":get("operator"),"value":_normalized(get("value")),
        "unit":_normalized(get("unit")),"action":_normalized(get("action")),
        "conditions":sorted(_normalized(x) for x in (get("conditions") or [])),
        "exceptions":sorted(_normalized(x) for x in (get("exceptions") or [])),
    }


def _evidence_anchor(evidence:EvidenceLink|dict[str,Any])->dict[str,Any]:
    get=(lambda key:getattr(evidence,key,"")) if not isinstance(evidence,dict) else (lambda key:evidence.get(key,""))
    locator=dict(get("source_locator") or {})
    # File version/hash is deliberately excluded: replacing a source must re-evaluate the same finding,
    # not manufacture a second finding. The logical source position remains part of the identity.
    stable_locator={key:_normalized(locator.get(key)) for key in
        ("drawing_no","chapter","section","paragraph","source_ref","page_no") if locator.get(key) not in (None,"")}
    return {
        "source_type":get("source_type"),"standard_code":_normalized(get("standard_code")),
        "clause_no":_normalized(get("clause_no")),"project_binding":get("project_binding"),
        "locator":stable_locator,
    }


def finding_fingerprint(*,conflict_type:str,source_claims:Iterable[RequirementClaim|dict[str,Any]],
                        norm_claims:Iterable[RequirementClaim|dict[str,Any]],
                        evidence_links:Iterable[EvidenceLink|dict[str,Any]],
                        objects:Iterable[dict[str,Any]]=(),relations:Iterable[dict[str,Any]]=())->str:
    payload={
        "conflict_type":conflict_type,
        "objects":sorted((_normalized(x.get("object_type")),_normalized(x.get("canonical_name"))) for x in objects),
        "relations":sorted(_normalized(x.get("relation_type")) for x in relations),
        "source_claims":sorted((_claim_signature(x) for x in source_claims),key=lambda x:json.dumps(x,ensure_ascii=False,sort_keys=True)),
        "norm_claims":sorted((_claim_signature(x) for x in norm_claims),key=lambda x:json.dumps(x,ensure_ascii=False,sort_keys=True)),
        "evidence":sorted((_evidence_anchor(x) for x in evidence_links),key=lambda x:json.dumps(x,ensure_ascii=False,sort_keys=True)),
    }
    canonical=json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(",",":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass
class ReviewHistoryEntry:
    action:str
    user:str
    time:str
    note:str=""
    metadata:dict[str,Any]=field(default_factory=dict)


@dataclass
class ReviewFinding:
    finding_id:str
    fingerprint:str
    source_type:str
    source_claims:list[str]
    norm_claims:list[str]
    conflict_type:str
    evidence_links:list[str]
    severity:str
    status:str
    assignee:str
    reviewer:str
    created_time:str
    updated_time:str
    history:list[ReviewHistoryEntry]
    discovery_mode:str="deterministic"
    assignment_role:str=""
    confirmed_by:str=""
    confirmed_time:str=""
    confirmation_note:str=""
    rectification_note:str=""
    expected_finish:str=""
    review_time:str=""
    close_reason:str=""
    reevaluation_required:bool=False
    revision:int=1

    def to_dict(self)->dict[str,Any]:return asdict(self)


class ReviewWorkflowError(ValueError):pass


def _severity(conflict_type:str)->str:
    if conflict_type in {"conflict","norm_stricter"}:return "high"
    if conflict_type in {"potential_conflict","insufficient_evidence"}:return "medium"
    return "low"


class ReviewFindingStore:
    """Explicit in-memory workflow store. No review history is written to the database."""

    def __init__(self,clock:Callable[[],str]=utc_now):
        self._clock=clock;self._by_fingerprint:dict[str,ReviewFinding]={};self._sequence=0

    def all(self)->list[ReviewFinding]:return list(self._by_fingerprint.values())

    def get(self,fingerprint:str)->ReviewFinding|None:return self._by_fingerprint.get(fingerprint)

    def _new_id(self)->str:
        self._sequence+=1;return f"review-finding-{self._sequence:04d}"

    def discover(self,*,fingerprint:str,source_type:str,source_claims:list[str],norm_claims:list[str],
                 conflict_type:str,evidence_links:list[str],severity:str="",discovery_mode:str="deterministic")->ReviewFinding:
        if discovery_mode not in DISCOVERY_MODES:raise ReviewWorkflowError(f"未知发现模式：{discovery_mode}")
        now=self._clock();existing=self._by_fingerprint.get(fingerprint)
        if existing:
            existing.source_claims=list(dict.fromkeys(existing.source_claims+source_claims))
            existing.norm_claims=list(dict.fromkeys(existing.norm_claims+norm_claims))
            changed_evidence=any(x not in existing.evidence_links for x in evidence_links)
            existing.evidence_links=list(dict.fromkeys(existing.evidence_links+evidence_links))
            existing.updated_time=now;existing.revision+=1
            existing.history.append(ReviewHistoryEntry("rediscovered","system",now,metadata={"evidence_changed":changed_evidence}))
            if changed_evidence:self.request_reevaluation(existing,"evidence_changed",actor="system")
            return existing
        status="pending_confirmation" if discovery_mode=="model_assisted" or conflict_type in {"potential_conflict","insufficient_evidence","not_comparable"} else "detected"
        finding=ReviewFinding(
            self._new_id(),fingerprint,source_type,list(dict.fromkeys(source_claims)),list(dict.fromkeys(norm_claims)),
            conflict_type,list(dict.fromkeys(evidence_links)),severity or _severity(conflict_type),status,"","",now,now,
            [ReviewHistoryEntry("discovered","system",now,metadata={"discovery_mode":discovery_mode})],discovery_mode,
        )
        self._by_fingerprint[fingerprint]=finding;return finding

    def transition(self,finding:ReviewFinding,target_status:str,*,actor:str,note:str="",role:str="",assignee:str="",expected_finish:str="",reason:str="")->ReviewFinding:
        if target_status not in REVIEW_STATUSES:raise ReviewWorkflowError(f"未知审查状态：{target_status}")
        if target_status not in ALLOWED_REVIEW_TRANSITIONS.get(finding.status,set()):
            raise ReviewWorkflowError(f"不允许的审查状态迁移：{finding.status} -> {target_status}")
        if not actor:raise ReviewWorkflowError("状态迁移必须记录操作人。")
        if target_status=="confirmed" and not note:raise ReviewWorkflowError("确认问题必须记录 confirmation_note。")
        if target_status=="assigned" and (role not in ASSIGNMENT_ROLES or not assignee):raise ReviewWorkflowError("分配问题必须提供有效责任方角色和 assignee。")
        if target_status=="rectifying" and (not note or not expected_finish):raise ReviewWorkflowError("进入整改中必须记录 rectification_note 和 expected_finish。")
        if target_status=="closed" and not reason:raise ReviewWorkflowError("关闭问题必须记录 close_reason。")
        if target_status=="reopened" and not reason:raise ReviewWorkflowError("重新打开必须记录原因。")
        now=self._clock();finding.status=target_status;finding.updated_time=now;finding.revision+=1
        if target_status=="confirmed":
            finding.confirmed_by=actor;finding.confirmed_time=now;finding.confirmation_note=note
        elif target_status=="assigned":finding.assignee=assignee;finding.assignment_role=role
        elif target_status=="rectifying":finding.rectification_note=note;finding.expected_finish=expected_finish
        elif target_status=="closed":finding.reviewer=actor;finding.review_time=now;finding.close_reason=reason;finding.reevaluation_required=False
        elif target_status=="reopened":finding.reevaluation_required=True
        finding.history.append(ReviewHistoryEntry(target_status,actor,now,note or reason,{"role":role,"assignee":assignee,"expected_finish":expected_finish}))
        return finding

    def request_reevaluation(self,finding:ReviewFinding,trigger:str,*,actor:str="system",note:str="")->ReviewFinding:
        if trigger not in REEVALUATION_TRIGGERS:raise ReviewWorkflowError(f"未知重新评估触发器：{trigger}")
        now=self._clock();finding.reevaluation_required=True;finding.updated_time=now;finding.revision+=1
        finding.history.append(ReviewHistoryEntry("reevaluation_requested",actor,now,note,{"trigger":trigger}))
        if finding.status=="closed":
            finding.status="reopened";finding.history.append(ReviewHistoryEntry("reopened",actor,now,note or trigger,{"trigger":trigger}))
        return finding

    def ingest_conflicts(self,package:KnowledgePackage)->list[ReviewFinding]:
        claims={x.claim_id:x for x in package.requirement_claims};evidence={x.evidence_id:x for x in package.evidence_links};result=[]
        for conflict in package.conflicts:
            if not conflict.requires_human_review:continue
            project=[claims[conflict.project_claim_id]] if conflict.project_claim_id in claims else []
            norm=[claims[conflict.normative_claim_id]] if conflict.normative_claim_id in claims else []
            links=[evidence[x] for x in conflict.evidence_links if x in evidence]
            subject_ids={subject for claim in project+norm for subject in claim.subject}
            selected_objects=[x for x in package.objects if x.object_id in subject_ids]
            selected_relations=[x for x in package.relations if x.subject_object_id in subject_ids or x.object_object_id in subject_ids]
            objects=[{"object_type":x.object_type,"canonical_name":x.canonical_name} for x in selected_objects]
            relations=[{"relation_type":x.relation_type,"original_text":x.original_text} for x in selected_relations]
            fingerprint=finding_fingerprint(conflict_type=conflict.status,source_claims=project,norm_claims=norm,evidence_links=links,objects=objects,relations=relations)
            result.append(self.discover(fingerprint=fingerprint,source_type="conflict_detection",
                source_claims=[x.claim_id for x in project],norm_claims=[x.claim_id for x in norm],conflict_type=conflict.status,
                evidence_links=conflict.evidence_links,discovery_mode="deterministic"))
        return result

    def ingest_model_findings(self,items:list[dict[str,Any]])->list[ReviewFinding]:
        result=[]
        for item in items:
            pseudo_claim={"claim_type":"unknown","subject":[],"property":item.get("category",""),"operator":"",
                "value":"","unit":"","action":item.get("issue",""),"conditions":[item.get("location","")],"exceptions":[]}
            refs=list(item.get("norm_refs",[]))+list(item.get("project_refs",[]))
            links=[{"source_type":"review_reference","source_locator":{"source_ref":x}} for x in refs]
            fingerprint=finding_fingerprint(conflict_type=item.get("finding_type","model_observation"),source_claims=[pseudo_claim],norm_claims=[],evidence_links=links)
            result.append(self.discover(fingerprint=fingerprint,source_type="model_review",source_claims=[],norm_claims=[],
                conflict_type=item.get("finding_type","model_observation"),evidence_links=refs,
                severity={"高":"high","中":"medium","低":"low","提示":"low"}.get(item.get("severity"),"medium"),discovery_mode="model_assisted"))
        return result

    def reevaluate_package(self,package:KnowledgePackage,trigger:str,*,detector:ConflictDetector|None=None)->list[ReviewFinding]:
        for finding in self.all():self.request_reevaluation(finding,trigger)
        detect_claim_conflicts(package,detector)
        return self.ingest_conflicts(package)
