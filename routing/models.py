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
    router_is_evidence:bool=False
