from __future__ import annotations

import json
from dataclasses import dataclass,field
from pathlib import Path

from engineering_knowledge.models import EngineeringObject,EngineeringRelation,TopicEdge,TopicGraph,TopicNode

ROOT=Path(__file__).resolve().parents[1]
CROSSWALK_FILE=ROOT/"data"/"topic_crosswalk.json"
CROSSWALK_SCHEMA=ROOT/"data"/"topic_crosswalk.schema.json"


@dataclass
class CrosswalkMatch:
    topic_ids:list[str]
    reason:str
    query_terms:list[str]
    object_ids:list[str]=field(default_factory=list)
    relation_ids:list[str]=field(default_factory=list)
    graph_edges:list[dict]=field(default_factory=list)
    confidence:str="medium"


@dataclass
class CrosswalkResult:
    matches:list[CrosswalkMatch]
    topic_metadata:dict[str,dict]

    @property
    def topic_ids(self)->list[str]:
        return list(dict.fromkeys(topic for match in self.matches for topic in match.topic_ids))


class TopicCrosswalk:
    def __init__(self,path:Path|None=None):
        selected=path or CROSSWALK_FILE
        if selected==CROSSWALK_FILE:
            from engineering_knowledge.json_schema import validate_json_file
            self.pack=validate_json_file(selected,CROSSWALK_SCHEMA)
        else:self.pack=json.loads(selected.read_text(encoding="utf-8"))

    @staticmethod
    def _context_matches(mapping:dict,intent:str,project_stage:str)->bool:
        return (not mapping.get("intent") or mapping["intent"]==intent) and (not mapping.get("project_stage") or mapping["project_stage"]==project_stage)

    def resolve(self,objects:list[EngineeringObject],relations:list[EngineeringRelation],intent:str="",project_stage:str="unknown")->CrosswalkResult:
        matches=[];by_id={item.object_id:item for item in objects}
        for item in objects:
            for mapping in self.pack["object_mappings"]:
                if item.object_type in mapping["object_types"] and self._context_matches(mapping,intent,project_stage):
                    matches.append(CrosswalkMatch(mapping["topic_ids"],f"object_type={item.object_type}",mapping["query_terms"],[item.object_id],confidence=item.confidence))
        for relation in relations:
            subject=by_id.get(relation.subject_object_id);target=by_id.get(relation.object_object_id)
            if not subject or not target:continue
            for mapping in self.pack["relation_mappings"]:
                if not self._context_matches(mapping,intent,project_stage):continue
                if subject.object_type not in mapping["subject_types"] or relation.relation_type not in mapping["relation_types"] or target.object_type not in mapping["object_types"]:continue
                reason=mapping["reason_template"].format(subject_type=subject.object_type,relation_type=relation.relation_type,object_type=target.object_type)
                reason=f"{reason} [{relation.assertion_status}]"
                matches.append(CrosswalkMatch(mapping["topic_ids"],reason,mapping["query_terms"],[subject.object_id,target.object_id],[relation.relation_id],mapping["graph_edges"],relation.confidence))
        return CrosswalkResult(matches,self.pack["topics"])


def build_topic_graph(result:CrosswalkResult,relations:list[EngineeringRelation])->TopicGraph:
    relation_by_id={item.relation_id:item for item in relations};nodes=[];edges=[];seen_nodes=set();seen_edges=set()
    confidence_by_topic={}
    for match in result.matches:
        for topic_id in match.topic_ids:
            confidence_by_topic.setdefault(topic_id,match.confidence)
    for topic_id in result.topic_ids:
        metadata=result.topic_metadata.get(topic_id,{})
        nodes.append(TopicNode(topic_id,metadata.get("profession",""),metadata.get("system",""),confidence_by_topic.get(topic_id,"medium")));seen_nodes.add(topic_id)
    for match in result.matches:
        for edge in match.graph_edges:
            relation=relation_by_id.get(match.relation_ids[0]) if match.relation_ids else None
            assertion=relation.assertion_status if relation else "affirmed";relevant=relation.relation_relevant_for_retrieval if relation else True
            key=(edge["source"],edge["edge_type"],edge["target"],assertion)
            if key in seen_edges:continue
            seen_edges.add(key);edges.append(TopicEdge(edge["source"],edge["edge_type"],edge["target"],list(match.relation_ids),assertion,relevant,match.reason))
    return TopicGraph(nodes,edges)
