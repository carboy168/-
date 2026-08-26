from __future__ import annotations

import re
from dataclasses import dataclass

from engineering_knowledge.catalog import load_object_catalog,load_relation_catalog
from engineering_knowledge.models import EngineeringObject,EngineeringRelation,SourceSpan


def _confidence(score:float)->str:
    return "high" if score>=85 else ("medium" if score>=65 else "low")


def _assertion_status(text:str,expression_start:int)->str:
    prefix=(text or "")[max(0,expression_start-16):expression_start]
    context=(text or "")[max(0,expression_start-16):expression_start+10]
    if re.search(r"(?:没有|没|并未|未曾|不是|不再).{0,8}$",prefix):return "negated"
    if re.search(r"(?:怀疑|疑似|可能|也许|或许|似乎)",context):return "uncertain"
    if re.search(r"(?:如果|假如|要是|一旦|能不能|是否|可不可以|可以.{0,10}吗|能.{0,10}吗)",text or ""):return "conditional"
    return "affirmed"


class EngineeringObjectExtractor:
    def __init__(self,catalog:dict|None=None):self.catalog=catalog or load_object_catalog()

    def extract(self,text:str,source_id:str="question")->list[EngineeringObject]:
        value=text or "";candidates=[]
        for rule in self.catalog["object_types"]:
            for alias in sorted(set(rule["aliases"]),key=lambda x:-len(x)):
                for match in re.finditer(re.escape(alias),value,re.I):
                    score=96 if alias==rule["canonical_name"] else min(94,72+len(alias)*4)
                    candidates.append((match.start(),match.end(),score,rule,match.group(0)))
        accepted=[];occupied=[]
        for item in sorted(candidates,key=lambda x:(-(x[1]-x[0]),-x[2],x[0],x[3]["object_type"])):
            start,end=item[:2]
            if any(start<right and end>left for left,right in occupied):continue
            occupied.append((start,end));accepted.append(item)
        result=[]
        for index,(start,end,score,rule,original) in enumerate(sorted(accepted,key=lambda x:(x[0],x[1])),start=1):
            result.append(EngineeringObject(
                object_id=f"obj-{index:03d}",object_type=rule["object_type"],canonical_name=rule["canonical_name"],
                original_text=original,aliases=list(rule["aliases"]),profession=rule["profession"],system=rule["system"],
                attributes={},source_span=SourceSpan(source_id,start,end),confidence=_confidence(score),numeric_score=round(score,2),
                topic_hints=list(rule.get("topic_hints",[])),
            ))
        return result


class EngineeringRelationExtractor:
    def __init__(self,catalog:dict|None=None):self.catalog=catalog or load_relation_catalog()

    @staticmethod
    def _before(objects,position,types,distance):
        return [x for x in objects if x.object_type in types and x.source_span.end<=position and position-x.source_span.end<=distance]

    @staticmethod
    def _after(objects,position,types,distance):
        return [x for x in objects if x.object_type in types and x.source_span.start>=position and x.source_span.start-position<=distance]

    def _pairs(self,rule:dict,objects:list[EngineeringObject],start:int,end:int):
        max_distance=int(rule["max_distance"]);subjects=set(rule["subject_types"]);targets=set(rule["object_types"]);mode=rule["mode"]
        if mode=="between":
            before=sorted(self._before(objects,start,subjects,max_distance),key=lambda x:x.source_span.end,reverse=True)
            after=sorted(self._after(objects,start,targets,max_distance),key=lambda x:x.source_span.start)
            return [(before[0],after[0])] if before and after and before[0].object_id!=after[0].object_id else []
        if mode=="two_before":
            eligible=[x for x in objects if x.source_span.end<=end and end-x.source_span.end<=max_distance and x.object_type in subjects|targets]
            eligible=sorted(eligible,key=lambda x:x.source_span.start)[-2:]
            if len(eligible)<2:return []
            left,right=eligible
            if left.object_type in subjects and right.object_type in targets:return [(left,right)]
            if right.object_type in subjects and left.object_type in targets:return [(right,left)]
            return []
        containers=[x for x in objects if x.object_type in targets and x.source_span.start<=start and x.source_span.end>=end]
        if not containers:return []
        container=containers[0]
        contained=[x for x in objects if x.object_type in subjects and x.source_span.start>=container.source_span.end and x.source_span.start-container.source_span.end<=max_distance]
        return [(x,container) for x in contained]

    def extract(self,text:str,objects:list[EngineeringObject],source_id:str="question")->list[EngineeringRelation]:
        value=text or "";relations=[];seen=set()
        for rule in self.catalog["rules"]:
            for expression in sorted(set(rule["expressions"]),key=lambda x:-len(x)):
                for match in re.finditer(re.escape(expression),value,re.I):
                    for subject,target in self._pairs(rule,objects,match.start(),match.end()):
                        key=(rule["relation_type"],subject.object_id,target.object_id)
                        if key in seen:continue
                        seen.add(key);left=min(subject.source_span.start,target.source_span.start,match.start());right=max(subject.source_span.end,target.source_span.end,match.end())
                        score=float(rule["score"]);relations.append(EngineeringRelation(
                            relation_id="",subject_object_id=subject.object_id,relation_type=rule["relation_type"],object_object_id=target.object_id,
                            original_text=value[left:right],attributes={"expression":match.group(0)},source_span=SourceSpan(source_id,left,right),
                            confidence=_confidence(score),numeric_score=score,rule_id=rule["rule_id"],topic_hints=list(rule.get("topic_hints",[])),
                            assertion_status=_assertion_status(value,match.start()),relation_relevant_for_retrieval=True,
                        ))
        relations.sort(key=lambda x:(x.source_span.start,x.relation_type,x.subject_object_id,x.object_object_id))
        for index,item in enumerate(relations,start=1):item.relation_id=f"rel-{index:03d}"
        return relations


def extract_question_knowledge(question:str,source_id:str="question"):
    objects=EngineeringObjectExtractor().extract(question,source_id)
    relations=EngineeringRelationExtractor().extract(question,objects,source_id)
    return objects,relations
