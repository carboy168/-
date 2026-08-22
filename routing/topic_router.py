from __future__ import annotations
import json
from pathlib import Path
from typing import Any

from routing.explicit_parser import parse_explicit_reference
from routing.models import NormalizedTerm,RouteResult,TopicMatch
from routing.standard_policy import StandardPolicyService

ROOT=Path(__file__).resolve().parents[1]
DATA_FILE=ROOT/"data"/"topic_router.json"
REQUIRED_TOPIC_FIELDS={"topic_id","profession","topic","subtopics","aliases","site_terms","positive_terms","negative_terms","governing_standards","primary_standards","companion_standards","query_expansions","confidence_threshold"}


def load_topic_catalog(path:Path|None=None)->dict[str,Any]:
    pack=json.loads((path or DATA_FILE).read_text(encoding="utf-8"))
    if not isinstance(pack.get("schema_version"),int) or not pack.get("router_version"):raise ValueError("Topic Router 配置缺少版本信息。")
    if not isinstance(pack.get("site_terms"),list) or not isinstance(pack.get("topics"),list):raise ValueError("Topic Router 配置结构无效。")
    ids=set()
    for topic in pack["topics"]:
        missing=REQUIRED_TOPIC_FIELDS-set(topic)
        if missing:raise ValueError(f"Topic {topic.get('topic_id','?')} 缺少字段：{sorted(missing)}")
        if topic["topic_id"] in ids:raise ValueError(f"Topic ID 重复：{topic['topic_id']}")
        ids.add(topic["topic_id"])
        thresholds=topic["confidence_threshold"]
        if thresholds.get("high",0)<=thresholds.get("medium",0):raise ValueError(f"Topic {topic['topic_id']} 置信度阈值无效。")
    return pack


def _unique(values):return list(dict.fromkeys(x for x in values if x))


class TopicRouter:
    def __init__(self,path:Path|None=None,policy:StandardPolicyService|None=None):
        self.pack=load_topic_catalog(path);self.policy=policy or StandardPolicyService()

    def normalize(self,question:str)->tuple[str,list[NormalizedTerm]]:
        original=(question or "").strip();expanded=original.lower();mappings=[]
        for item in sorted(self.pack["site_terms"],key=lambda x:-len(x["original"])):
            if item["original"].lower() in expanded:
                values=list(item.get("normalized",[]));mappings.append(NormalizedTerm(item["original"],values));expanded+=" "+" ".join(values).lower()
        return expanded,mappings

    @staticmethod
    def _project_blob(project_context:dict|str|None)->str:
        if isinstance(project_context,dict):return " ".join(str(x) for x in project_context.values()).lower()
        return str(project_context or "").lower()

    def _score(self,rule:dict,q:str,mappings:list[NormalizedTerm],project_blob:str,explicit_code:str)->tuple[float,list[str],list[str]]:
        score=0.0;hits=[];signals=set()
        for phrase in rule.get("phrases",[]):
            if phrase.lower() in q:score+=24+min(len(phrase),12)*0.4;hits.append(phrase);signals.add("phrase")
        for term in rule.get("site_terms",[]):
            if term.lower() in q:score+=20+min(len(term),10)*0.5;hits.append(term);signals.add("site")
        for alias in rule.get("aliases",[]):
            if alias.lower() in q:score+=14+min(len(alias),8)*0.4;hits.append(alias);signals.add("alias")
        for item in rule.get("positive_terms",[]):
            if item["term"].lower() in q:score+=float(item.get("weight",5));hits.append(item["term"]);signals.add("positive")
        context_hits=[x for x in rule.get("context_terms",[]) if x.lower() in q]
        if context_hits:score+=min(10,len(context_hits)*3);hits+=context_hits;signals.add("context")
        normalized_hits=[m.original for m in mappings if any(x.lower() in q for x in m.normalized)]
        if normalized_hits and any(x in hits for x in normalized_hits):score+=5
        if len(signals)>=3:score+=10
        elif len(signals)>=2:score+=5
        if project_blob and any(x.lower() in project_blob for x in (rule.get("profession","") ,rule.get("topic",""))):score+=6;signals.add("project")
        all_codes=rule.get("governing_standards",[])+rule.get("primary_standards",[])+rule.get("companion_standards",[])
        if explicit_code and explicit_code in all_codes:score+=18;signals.add("explicit")
        for item in rule.get("negative_terms",[]):
            if item["term"].lower() in q:score-=float(item.get("weight",10));hits.append("排除:"+item["term"]);signals.add("negative")
        score=max(0.0,min(100.0,score))
        subtopics=[]
        for sub in rule.get("subtopics",[]):
            pieces=[x for x in sub.replace("/"," ").split() if len(x)>=2]
            if any(x.lower() in q for x in pieces):subtopics.append(sub)
        if not subtopics and score>0:subtopics=rule.get("subtopics",[])[:1]
        return score,_unique(hits),subtopics

    def route(self,question:str,project_context:dict|str|None=None)->RouteResult:
        explicit=parse_explicit_reference(question);q,mappings=self.normalize(question);project_blob=self._project_blob(project_context)
        topics=[]
        for rule in self.pack["topics"]:
            score,hits,subtopics=self._score(rule,q,mappings,project_blob,explicit.standard_code)
            thresholds=rule["confidence_threshold"]
            if score<=0:continue
            confidence="high" if score>=thresholds["high"] else ("medium" if score>=thresholds["medium"] else "low")
            topic_mappings=[m for m in mappings if m.original in question]
            topics.append(TopicMatch(rule["topic_id"],rule["profession"],rule["topic"],subtopics,confidence,round(score,2),hits,topic_mappings,list(rule["governing_standards"]),list(rule["primary_standards"]),list(rule["companion_standards"]),list(rule["query_expansions"])))
        topics.sort(key=lambda x:(-x.numeric_score,x.topic_id));topics=topics[:5]
        candidates=[];expansions=[]
        if explicit.explicit_standard:candidates.append(explicit.standard_code)
        for topic in topics:
            if topic.confidence=="low":continue
            candidates+=topic.governing_standards+topic.primary_standards+topic.companion_standards
            expansions+=topic.query_expansions
        allowed,warnings,statuses=self.policy.validate(_unique(candidates),explicit.standard_code,explicit.clause_no)
        if explicit.explicit_standard and explicit.standard_code not in allowed and not warnings:warnings.append("用户明示规范未通过当前状态校验。")
        return RouteResult(question,explicit,topics,_unique(allowed),_unique(expansions),_unique(warnings),statuses,False)
