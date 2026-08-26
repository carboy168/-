from __future__ import annotations

import json,re
from dataclasses import asdict,dataclass,field
from datetime import datetime,timezone
from pathlib import Path
from typing import Any

from engineering_knowledge.evidence_trust import EvidenceTrustPolicy
from engineering_knowledge.json_schema import validate_instance
from engineering_knowledge.models import ConflictResult,EvidenceLink,KnowledgePackage


PROJECT_EVIDENCE_LIFECYCLE_STATUSES={
    "extracted","pending_confirmation","verified","rejected","superseded","expired",
}
TERMINAL_PROJECT_EVIDENCE_STATUSES={"rejected","superseded","expired"}
ALLOWED_TRANSITIONS={
    "extracted":{"pending_confirmation","rejected"},
    "pending_confirmation":{"verified","rejected","superseded","expired"},
    "verified":{"superseded","expired"},
    "rejected":{"pending_confirmation"},
    "superseded":set(),
    "expired":{"pending_confirmation"},
}
LOCATOR_KEYS={"page_no","drawing_no","chapter","section","paragraph","source_ref","chunk_id","requirement_id"}
ROOT=Path(__file__).resolve().parents[1]
LIFECYCLE_SCHEMA=json.loads((ROOT/"data"/"project_evidence_lifecycle.schema.json").read_text(encoding="utf-8"))


def utc_now()->str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class ProjectEvidenceLifecycleRecord:
    evidence_id:str
    lifecycle_status:str
    source_file_name:str
    source_version:str
    source_hash:str
    source_locator:dict[str,Any]
    extracted_time:str
    confirmed_by:str=""
    confirmed_time:str=""
    superseded_by:str=""
    related_claim_ids:list[str]=field(default_factory=list)
    reevaluation_required:bool=False
    warnings:list[str]=field(default_factory=list)

    def to_dict(self)->dict[str,Any]:return asdict(self)


@dataclass
class ProjectEvidenceLifecycleValidation:
    records:list[ProjectEvidenceLifecycleRecord]=field(default_factory=list)
    downgraded_evidence_ids:list[str]=field(default_factory=list)
    reevaluation_claim_ids:list[str]=field(default_factory=list)
    warnings:list[str]=field(default_factory=list)
    human_confirmation_required:bool=False


def _managed(evidence:EvidenceLink)->bool:
    return evidence.evidence_role=="project_evidence" and bool(evidence.source_locator.get("lifecycle_managed"))


def lifecycle_status(evidence:EvidenceLink)->str:
    if not _managed(evidence):return ""
    value=str(evidence.status or evidence.source_locator.get("lifecycle_status","") or "")
    return value if value in PROJECT_EVIDENCE_LIFECYCLE_STATUSES else "extracted"


def _record(evidence:EvidenceLink)->ProjectEvidenceLifecycleRecord:
    locator=evidence.source_locator
    return ProjectEvidenceLifecycleRecord(
        evidence_id=evidence.evidence_id,lifecycle_status=lifecycle_status(evidence),
        source_file_name=str(locator.get("source_file_name") or evidence.document_name or ""),
        source_version=str(locator.get("source_version") or ""),
        source_hash=str(locator.get("source_hash") or ""),source_locator=dict(locator),
        extracted_time=str(locator.get("extracted_time") or ""),confirmed_by=str(locator.get("confirmed_by") or ""),
        confirmed_time=str(locator.get("confirmed_time") or ""),superseded_by=str(locator.get("superseded_by") or ""),
        related_claim_ids=list(evidence.linked_claim_ids),
        reevaluation_required=bool(locator.get("reevaluation_required")),warnings=list(evidence.warnings),
    )


def enrich_project_evidence(evidence:EvidenceLink,row:dict,status:str)->EvidenceLink:
    if status not in PROJECT_EVIDENCE_LIFECYCLE_STATUSES:raise ValueError(f"无效项目证据生命周期状态：{status}")
    locator=evidence.source_locator
    locator["lifecycle_managed"]=True;locator["lifecycle_status"]=status
    locator["source_file_name"]=str(row.get("source_file_name") or row.get("original_name") or row.get("title") or evidence.document_name or "")
    locator["source_version"]=str(row.get("source_version") or row.get("file_version") or row.get("version") or "unversioned")
    locator["source_hash"]=str(row.get("source_hash") or row.get("sha256") or evidence.content_hash)
    locator["extracted_time"]=str(row.get("extracted_time") or utc_now())
    locator.setdefault("confirmed_by","");locator.setdefault("confirmed_time","");locator.setdefault("superseded_by","")
    locator.setdefault("reevaluation_required",False)
    evidence.status=status
    return evidence


def _locator_is_traceable(locator:dict)->bool:
    return any(locator.get(key) not in (None,"") for key in LOCATOR_KEYS)


def _set_claim_state(package:KnowledgePackage,evidence:EvidenceLink,status:str)->list[str]:
    affected=[]
    for claim in package.requirement_claims:
        if evidence.evidence_id not in claim.evidence_links:continue
        affected.append(claim.claim_id)
        if status=="verified":claim.verification_status="supported"
        elif status in {"extracted","pending_confirmation"}:claim.verification_status="unverified"
        else:claim.verification_status="blocked"
        if status in {"superseded","expired"}:
            warning=f"项目证据已{('被替代' if status=='superseded' else '失效')}，Claim 必须重新评估。"
            if warning not in claim.warnings:claim.warnings.append(warning)
    return affected


class ProjectEvidenceLifecycleValidator:
    """Fail-closed runtime validator; it never promotes project evidence to normative evidence."""

    def validate(self,package:KnowledgePackage)->ProjectEvidenceLifecycleValidation:
        result=ProjectEvidenceLifecycleValidation()
        for evidence in package.evidence_links:
            if not _managed(evidence):continue
            status=lifecycle_status(evidence);locator=evidence.source_locator;problems=[]
            if evidence.evidence_role!="project_evidence" or evidence.normative_authority:
                problems.append("项目证据不得升级为规范证据或携带规范效力。")
            if not locator.get("source_file_name") or not locator.get("source_hash") or not locator.get("extracted_time"):
                problems.append("项目证据缺少文件名、来源哈希或提取时间。")
            if not re.fullmatch(r"[a-fA-F0-9]{64}",str(locator.get("source_hash") or "")):
                problems.append("项目证据 source_hash 格式无效。")
            if status=="verified":
                if not _locator_is_traceable(locator):problems.append("已确认项目证据缺少页码、图号、章节或记录定位。")
                if not locator.get("confirmed_by") or not locator.get("confirmed_time"):problems.append("已确认项目证据缺少确认人或确认时间。")
                if not EvidenceTrustPolicy.is_traceable(evidence):problems.append("项目证据原文哈希校验失败。")
            if status=="superseded" and not locator.get("superseded_by"):problems.append("被替代证据缺少 superseded_by。")
            if problems:
                locator["source_file_name"]=str(locator.get("source_file_name") or evidence.document_name or "unknown")
                locator["source_hash"]=evidence.content_hash
                locator["extracted_time"]=str(locator.get("extracted_time") or utc_now())
                evidence.verified=False;evidence.verification_status="unverified";evidence.status="pending_confirmation"
                locator["lifecycle_status"]="pending_confirmation";locator["reevaluation_required"]=True
                evidence.warnings.extend(x for x in problems if x not in evidence.warnings)
                result.downgraded_evidence_ids.append(evidence.evidence_id);result.warnings.extend(problems)
                result.reevaluation_claim_ids.extend(_set_claim_state(package,evidence,"pending_confirmation"))
                status="pending_confirmation"
            elif status=="verified":
                evidence.verified=True;evidence.verification_status="verified";_set_claim_state(package,evidence,status)
            elif status=="rejected":
                evidence.verified=False;evidence.verification_status="rejected";result.reevaluation_claim_ids.extend(_set_claim_state(package,evidence,status))
            elif status=="superseded":
                evidence.verified=False;evidence.verification_status="superseded";result.reevaluation_claim_ids.extend(_set_claim_state(package,evidence,status))
            elif status=="expired":
                evidence.verified=False;evidence.verification_status="blocked";result.reevaluation_claim_ids.extend(_set_claim_state(package,evidence,status))
            else:
                evidence.verified=False;evidence.verification_status="unverified";_set_claim_state(package,evidence,status)
            if status!="verified":result.human_confirmation_required=True
            record=_record(evidence);validate_instance(record.to_dict(),LIFECYCLE_SCHEMA);result.records.append(record)
        result.reevaluation_claim_ids=list(dict.fromkeys(result.reevaluation_claim_ids))
        result.warnings=list(dict.fromkeys(result.warnings))
        package.human_confirmation_required=package.human_confirmation_required or result.human_confirmation_required
        package.warnings.extend(x for x in result.warnings if x not in package.warnings)
        return result


def _find(package:KnowledgePackage,evidence_id:str)->EvidenceLink:
    evidence=next((item for item in package.evidence_links if item.evidence_id==evidence_id),None)
    if not evidence or evidence.evidence_role!="project_evidence":raise ValueError(f"未找到项目 EvidenceLink：{evidence_id}")
    return evidence


def transition_project_evidence(package:KnowledgePackage,evidence_id:str,target_status:str,*,actor:str="",at:str="",superseded_by:str="")->ProjectEvidenceLifecycleValidation:
    evidence=_find(package,evidence_id);current=lifecycle_status(evidence)
    if not current:raise ValueError("旧版未治理 EvidenceLink 必须先补齐生命周期元数据。")
    if target_status not in ALLOWED_TRANSITIONS.get(current,set()):raise ValueError(f"不允许的项目证据状态迁移：{current} -> {target_status}")
    locator=evidence.source_locator;evidence.status=target_status;locator["lifecycle_status"]=target_status
    if target_status=="verified":
        if not actor:raise ValueError("确认项目证据必须提供 confirmed_by。")
        locator["confirmed_by"]=actor;locator["confirmed_time"]=at or utc_now();locator["reevaluation_required"]=False
    if target_status=="superseded":
        if not superseded_by:raise ValueError("标记 superseded 必须提供新 EvidenceLink ID。")
        locator["superseded_by"]=superseded_by;locator["reevaluation_required"]=True
    if target_status in {"expired","rejected"}:locator["reevaluation_required"]=True
    result=ProjectEvidenceLifecycleValidator().validate(package)
    from engineering_knowledge.schema_validator import validate_knowledge_package
    validate_knowledge_package(package)
    return result


def _version_tuple(value:str)->tuple[int,...]|None:
    parts=[int(x) for x in re.findall(r"\d+",value or "")]
    return tuple(parts) if parts else None


def supersede_project_evidence(package:KnowledgePackage,old_evidence_id:str,new_evidence_id:str)->ProjectEvidenceLifecycleValidation:
    old=_find(package,old_evidence_id);new=_find(package,new_evidence_id)
    old_version=_version_tuple(str(old.source_locator.get("source_version","") or ""));new_version=_version_tuple(str(new.source_locator.get("source_version","") or ""))
    if old_version is None or new_version is None or new_version<=old_version:
        new.status="rejected";new.source_locator["lifecycle_status"]="rejected";new.source_locator["reevaluation_required"]=True
        warning="新项目文件版本不能证明晚于当前有效版本，禁止覆盖现有证据。"
        if warning not in new.warnings:new.warnings.append(warning)
        result=ProjectEvidenceLifecycleValidator().validate(package);result.warnings.append(warning);result.human_confirmation_required=True
        return result
    transition_project_evidence(package,old_evidence_id,"superseded",superseded_by=new_evidence_id)
    current=lifecycle_status(new)
    if current=="extracted":transition_project_evidence(package,new_evidence_id,"pending_confirmation")
    elif current!="pending_confirmation":
        new.status="pending_confirmation";new.source_locator["lifecycle_status"]="pending_confirmation";new.source_locator["reevaluation_required"]=True
    result=ProjectEvidenceLifecycleValidator().validate(package)
    from engineering_knowledge.schema_validator import validate_knowledge_package
    validate_knowledge_package(package)
    return result


def apply_lifecycle_conflict_policy(package:KnowledgePackage,conflicts:list[ConflictResult])->list[ConflictResult]:
    """Only refines evidence readiness; deterministic comparison rules remain untouched."""
    claims={item.claim_id:item for item in package.requirement_claims};evidence={item.evidence_id:item for item in package.evidence_links}
    trust=EvidenceTrustPolicy()
    for conflict in conflicts:
        if conflict.status!="insufficient_evidence":continue
        project=claims.get(conflict.project_claim_id);norm=claims.get(conflict.normative_claim_id)
        if not project or not norm:continue
        project_evidence=[evidence[x] for x in project.evidence_links if x in evidence]
        norm_evidence=[evidence[x] for x in norm.evidence_links if x in evidence]
        pending=any(lifecycle_status(item) in {"extracted","pending_confirmation"} for item in project_evidence)
        norm_trusted=any(trust.can_support_normative_claim(item).allowed for item in norm_evidence)
        if pending and norm_trusted:
            conflict.status="potential_conflict";conflict.confidence="low";conflict.requires_human_review=True
            conflict.reason="项目 Claim 已提取但证据仍待人工确认，当前只能提示潜在冲突。"
            warning="待确认项目证据不能参与确定性比较；确认后必须重新评估。"
            if warning not in conflict.warnings:conflict.warnings.append(warning)
    return conflicts
