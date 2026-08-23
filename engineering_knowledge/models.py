from __future__ import annotations

from dataclasses import asdict,dataclass,field
from typing import Any

CLAIM_TYPES = {
    "numeric_requirement", "method_requirement", "material_requirement",
    "prohibition_requirement", "mandatory_requirement", "acceptance_requirement",
    "responsibility_requirement", "sequence_requirement", "normative_status_claim",
    "evidence_bypass_request", "unknown",
}


@dataclass(frozen=True)
class SourceSpan:
    source_id:str
    start:int
    end:int


@dataclass
class EngineeringObject:
    object_id:str
    object_type:str
    canonical_name:str
    original_text:str
    aliases:list[str]
    profession:str
    system:str
    attributes:dict[str,Any]
    source_span:SourceSpan
    confidence:str
    numeric_score:float
    topic_hints:list[str]=field(default_factory=list)
    warnings:list[str]=field(default_factory=list)


@dataclass
class EngineeringRelation:
    relation_id:str
    subject_object_id:str
    relation_type:str
    object_object_id:str
    original_text:str
    attributes:dict[str,Any]
    source_span:SourceSpan
    confidence:str
    numeric_score:float
    rule_id:str
    topic_hints:list[str]=field(default_factory=list)
    evidence_links:list[str]=field(default_factory=list)
    verification_status:str="unverified"
    warnings:list[str]=field(default_factory=list)


@dataclass
class RequirementClaim:
    claim_id:str
    claim_type:str
    source_type:str
    source_id:str
    source_locator:dict[str,Any]
    original_text:str
    subject:list[str]
    property:str
    operator:str
    value:str
    unit:str
    action:str
    conditions:list[str]
    exceptions:list[str]
    project_stage:str
    normative_authority:str
    project_binding:str
    standard_status:str
    verification_status:str
    extraction_confidence:str
    evidence_links:list[str]
    warnings:list[str]=field(default_factory=list)


@dataclass
class EvidenceLink:
    evidence_id:str
    source_type:str
    source_id:str
    source_locator:dict[str,Any]
    original_text:str
    standard_code:str=""
    clause_no:str=""
    page_no:int|None=None
    drawing_no:str=""
    document_name:str=""
    status:str="unverified"
    normative_authority:str=""
    project_binding:str="none"
    content_hash:str=""
    verified:bool=False
    linked_object_ids:list[str]=field(default_factory=list)
    linked_relation_ids:list[str]=field(default_factory=list)
    linked_claim_ids:list[str]=field(default_factory=list)
    warnings:list[str]=field(default_factory=list)


@dataclass
class RetrievalPlan:
    topic_id:str
    preferred_standard_codes:list[str]
    query_terms:list[str]
    object_ids:list[str]
    relation_ids:list[str]
    evidence_gate:str="requires_clause_evidence"


@dataclass
class KnowledgePackage:
    schema_version:str
    package_id:str
    question:str
    normalized_question:str
    objects:list[EngineeringObject]
    relations:list[EngineeringRelation]
    topics:list[dict[str,Any]]
    project_stage:str
    user_role:str
    user_claims:list[dict[str,Any]]
    retrieval_plans:list[RetrievalPlan]
    evidence_links:list[EvidenceLink]
    requirement_claims:list[RequirementClaim]
    warnings:list[str]
    human_confirmation_required:bool
    route_metadata:dict[str,Any]=field(default_factory=dict)

    def to_dict(self)->dict[str,Any]:
        return asdict(self)
