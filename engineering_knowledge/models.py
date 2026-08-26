from __future__ import annotations

from dataclasses import asdict,dataclass,field
from typing import Any

CLAIM_TYPES = {
    "numeric_requirement", "method_requirement", "material_requirement",
    "prohibition_requirement", "mandatory_requirement", "acceptance_requirement",
    "responsibility_requirement", "sequence_requirement", "normative_status_claim",
    "evidence_bypass_request", "unknown",
}
EVIDENCE_ROLES = {"normative_evidence","project_evidence","user_statement","derived_analysis","reference_only"}
EVIDENCE_STATUSES = {"verified","unverified","rejected","blocked","superseded"}
ASSERTION_STATUSES = {"affirmed","negated","conditional","uncertain"}
CONFLICT_STATUSES = {"compatible","project_stricter","norm_stricter","conflict","potential_conflict","not_comparable","insufficient_evidence"}
ID_SCOPE = "package"


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
    id_scope:str=ID_SCOPE


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
    assertion_status:str="affirmed"
    relation_relevant_for_retrieval:bool=True
    id_scope:str=ID_SCOPE


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
    id_scope:str=ID_SCOPE


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
    evidence_role:str="reference_only"
    verification_status:str="unverified"
    id_scope:str=ID_SCOPE


@dataclass
class RetrievalPlan:
    topic_id:str
    preferred_standard_codes:list[str]
    query_terms:list[str]
    object_ids:list[str]
    relation_ids:list[str]
    evidence_gate:str="requires_clause_evidence"
    plan_id:str=""
    profession:str=""
    reason:str=""
    required_entities:list[str]=field(default_factory=list)
    required_relations:list[str]=field(default_factory=list)
    project_stage:str="unknown"
    intent:str=""
    required_evidence_role:str="normative_evidence"
    evidence_status:str="unverified"
    confidence:str="low"
    expanded_query_terms:list[str]=field(default_factory=list)
    id_scope:str=ID_SCOPE
    evidence_link_ids:list[str]=field(default_factory=list)


@dataclass
class ConflictResult:
    conflict_id:str
    status:str
    project_claim_id:str
    normative_claim_id:str
    evidence_links:list[str]
    reason:str
    comparison_basis:dict[str,Any]
    confidence:str
    requires_human_review:bool
    warnings:list[str]=field(default_factory=list)
    id_scope:str=ID_SCOPE


@dataclass
class TopicNode:
    topic_id:str
    profession:str
    system:str
    confidence:str


@dataclass
class TopicEdge:
    source_topic_id:str
    edge_type:str
    target_topic_id:str
    relation_ids:list[str]
    assertion_status:str
    relevant_for_retrieval:bool
    reason:str


@dataclass
class TopicGraph:
    nodes:list[TopicNode]=field(default_factory=list)
    edges:list[TopicEdge]=field(default_factory=list)


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
    topic_graph:TopicGraph=field(default_factory=TopicGraph)
    conflicts:list[ConflictResult]=field(default_factory=list)
    id_scope:str=ID_SCOPE

    def to_dict(self)->dict[str,Any]:
        return asdict(self)
