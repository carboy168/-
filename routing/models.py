from __future__ import annotations
from dataclasses import dataclass,field


@dataclass(frozen=True)
class NormalizedTerm:
    original:str
    normalized:list[str]


@dataclass(frozen=True)
class ExplicitReference:
    standard_code:str=""
    clause_no:str=""
    explicit_standard:bool=False
    explicit_clause:bool=False


@dataclass(frozen=True)
class ContextCandidate:
    value:str
    confidence:str
    numeric_score:float
    matched_terms:list[str]=field(default_factory=list)


@dataclass(frozen=True)
class UserClaim:
    claim_type:str
    claim_text:str
    claim_value:str=""
    claim_unit:str=""
    verification_status:str="unverified"


@dataclass
class TopicMatch:
    topic_id:str
    profession:str
    topic:str
    subtopics:list[str]
    confidence:str
    numeric_score:float
    matched_terms:list[str]=field(default_factory=list)
    normalized_terms:list[NormalizedTerm]=field(default_factory=list)
    governing_standards:list[str]=field(default_factory=list)
    primary_standards:list[str]=field(default_factory=list)
    companion_standards:list[str]=field(default_factory=list)
    query_expansions:list[str]=field(default_factory=list)
    warnings:list[str]=field(default_factory=list)


@dataclass
class RouteResult:
    question:str
    explicit:ExplicitReference
    topics:list[TopicMatch]=field(default_factory=list)
    preferred_standard_codes:list[str]=field(default_factory=list)
    expanded_query_terms:list[str]=field(default_factory=list)
    warnings:list[str]=field(default_factory=list)
    standard_statuses:list[dict]=field(default_factory=list)
    project_stage:str="unknown"
    project_stage_confidence:str="low"
    project_stage_candidates:list[ContextCandidate]=field(default_factory=list)
    user_role:str="unknown"
    user_role_confidence:str="low"
    user_role_candidates:list[ContextCandidate]=field(default_factory=list)
    claims:list[UserClaim]=field(default_factory=list)
    normative_authority:list[dict]=field(default_factory=list)
    project_binding:list[str]=field(default_factory=lambda:["none"])
    filtered_standards:list[dict]=field(default_factory=list)
    deprecated_standards:list[dict]=field(default_factory=list)
    conflicts:list[str]=field(default_factory=list)
    evidence_gate:str="requires_clause_evidence"
    router_is_evidence:bool=False
