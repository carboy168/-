from __future__ import annotations
import json,re
from pathlib import Path
from typing import Any

from routing.explicit_parser import parse_explicit_reference
from routing.models import ContextCandidate,NormalizedTerm,RouteResult,TopicMatch,UserClaim
from routing.standard_policy import StandardPolicyService

ROOT=Path(__file__).resolve().parents[1]
DATA_FILE=ROOT/"data"/"topic_router.json"
REQUIRED_TOPIC_FIELDS={"topic_id","profession","topic","subtopics","aliases","site_terms","positive_terms","negative_terms","governing_standards","primary_standards","companion_standards","query_expansions","confidence_threshold"}
PROJECT_BINDINGS={"design_drawing","design_change","approved_method_statement","contract_requirement","owner_instruction","project_record","none"}


def load_topic_catalog(path:Path|None=None)->dict[str,Any]:
    pack=json.loads((path or DATA_FILE).read_text(encoding="utf-8"))
    required={"schema_version","router_version","site_terms","project_stages","user_roles","project_binding_terms","evidence_model","topics"}
    if required-set(pack):raise ValueError(f"Topic Router 配置缺少字段：{sorted(required-set(pack))}")
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
    for group in ("project_stages","user_roles"):
        values=[x.get("value") for x in pack[group]]
        if len(values)!=len(set(values)) or "unknown" not in values:raise ValueError(f"{group} 必须包含唯一值及 unknown。")
    authority=set(pack["evidence_model"].get("normative_authority",[]))
    if not {"mandatory_code","national_standard","industry_standard","local_standard","technical_reference","engineering_experience"}.issubset(authority):raise ValueError("normative_authority 证据模型不完整。")
    if set(pack["evidence_model"].get("project_binding",[]))!=PROJECT_BINDINGS:raise ValueError("project_binding 证据模型不完整。")
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

    @staticmethod
    def _confidence(score:float,thresholds:dict)->str:
        return "high" if score>=thresholds.get("high",60) else ("medium" if score>=thresholds.get("medium",30) else "low")

    @staticmethod
    def _term_is_negated(text:str,term:str)->bool:
        """Return true when every occurrence is inside a short, explicit negation scope."""
        matches=list(re.finditer(re.escape(term.lower()),text.lower()))
        if not matches:return False
        return all(re.search(r"(?:不是|并非|不属于|不算|非)\s*$",text[max(0,m.start()-6):m.start()]) for m in matches)

    def _score(self,rule:dict,q:str,mappings:list[NormalizedTerm],project_blob:str,explicit_code:str)->tuple[float,list[str],list[str]]:
        score=0.0;hits=[];signals=set()
        for phrase in rule.get("phrases",[]):
            if phrase.lower() in q and not self._term_is_negated(q,phrase):score+=24+min(len(phrase),12)*0.4;hits.append(phrase);signals.add("phrase")
        for term in rule.get("site_terms",[]):
            if term.lower() in q and not self._term_is_negated(q,term):score+=20+min(len(term),10)*0.5;hits.append(term);signals.add("site")
        for alias in rule.get("aliases",[]):
            if alias.lower() in q and not self._term_is_negated(q,alias):score+=14+min(len(alias),8)*0.4;hits.append(alias);signals.add("alias")
        for item in rule.get("positive_terms",[]):
            if item["term"].lower() in q and not self._term_is_negated(q,item["term"]):score+=float(item.get("weight",5));hits.append(item["term"]);signals.add("positive")
        context_hits=[x for x in rule.get("context_terms",[]) if x.lower() in q and not self._term_is_negated(q,x)]
        if context_hits:score+=min(10,len(context_hits)*3);hits+=context_hits;signals.add("context")
        normalized_hits=[m.original for m in mappings if any(x.lower() in q for x in m.normalized)]
        if normalized_hits and any(x in hits for x in normalized_hits):score+=5
        if len(signals)>=3:score+=10
        elif len(signals)>=2:score+=5
        if project_blob and any(x.lower() in project_blob for x in (rule.get("profession","") ,rule.get("topic",""))):score+=6;signals.add("project")
        all_codes=rule.get("governing_standards",[])+rule.get("primary_standards",[])+rule.get("companion_standards",[])
        if explicit_code and explicit_code in all_codes:score+=18;signals.add("explicit")
        for item in rule.get("negative_terms",[]):
            if item["term"].lower() in q and not self._term_is_negated(q,item["term"]):score-=float(item.get("weight",10));hits.append("排除:"+item["term"]);signals.add("negative")
        score=max(0.0,min(100.0,score))
        subtopics=[]
        for sub,terms in rule.get("subtopic_terms",{}).items():
            if any(x.lower() in q for x in terms):subtopics.append(sub)
        if not subtopics:
            for sub in rule.get("subtopics",[]):
                pieces=[x for x in sub.replace("/"," ").split() if len(x)>=2]
                if any(x.lower() in q for x in pieces):subtopics.append(sub)
        if not subtopics and score>=rule["confidence_threshold"].get("medium",30):subtopics=rule.get("subtopics",[])[:1]
        return score,_unique(hits),_unique(subtopics)

    def _classify_context(self,question:str,project_context:dict|str|None,group:str,explicit_key:str)->list[ContextCandidate]:
        q=(question or "").lower();project_blob=self._project_blob(project_context);explicit=""
        if isinstance(project_context,dict):explicit=str(project_context.get(explicit_key,project_context.get("phase" if group=="project_stages" else "role",""))).lower()
        candidates=[]
        for rule in self.pack[group]:
            if rule["value"]=="unknown":continue
            score=0.0;hits=[]
            for item in rule.get("terms",[]):
                if item["term"].lower() in q:score+=float(item.get("weight",20));hits.append(item["term"])
            aliases=[str(x).lower() for x in rule.get("aliases",[])]
            if explicit and (explicit==rule["value"] or explicit in aliases):score+=70;hits.append("project_context:"+explicit)
            elif group=="project_stages" and project_blob and any(x in project_blob for x in aliases):score+=35;hits.append("project_context")
            score=min(100.0,score)
            if score>0:candidates.append(ContextCandidate(rule["value"],self._confidence(score,rule.get("confidence_threshold",{"high":55,"medium":25})),round(score,2),_unique(hits)))
        candidates.sort(key=lambda x:(-x.numeric_score,x.value))
        return candidates

    @staticmethod
    def _extract_claims(question:str)->list[UserClaim]:
        text=question or "";claims=[]
        numeric=re.search(r"(?P<text>[^，。！？?]{0,24}(?:必须|规定|要求|应为|间距)[^，。！？?]{0,12}?(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>mm|毫米|cm|厘米|m|米))",text,re.I)
        if numeric:claims.append(UserClaim("numeric_requirement",numeric.group("text"),numeric.group("value"),numeric.group("unit").lower()))
        status=re.search(r"(?P<text>[^，。！？?]{0,28}(?:是现行规范|是强条|是强制条文|属于强条)[^，。！？?]{0,12})",text)
        if status:claims.append(UserClaim("normative_status_claim",status.group("text"),status.group("text")))
        if re.search(r"(?:没|没有|未).{0,12}(?:规范|原文|证据).{0,18}(?:给|编|告诉).{0,8}条文号",text):claims.append(UserClaim("evidence_bypass_request",text))
        return claims

    def _project_bindings(self,project_context:dict|str|None)->list[str]:
        if not project_context:return ["none"]
        explicit=[]
        if isinstance(project_context,dict):
            raw=project_context.get("project_binding",[]);explicit=[raw] if isinstance(raw,str) else list(raw or [])
        valid=[x for x in explicit if x in PROJECT_BINDINGS and x!="none"]
        blob=self._project_blob(project_context)
        for rule in self.pack["project_binding_terms"]:
            if any(term.lower() in blob for term in rule.get("terms",[])):valid.append(rule["value"])
        return _unique(valid) or ["none"]

    def route(self,question:str,project_context:dict|str|None=None)->RouteResult:
        explicit=parse_explicit_reference(question);q,mappings=self.normalize(question);project_blob=self._project_blob(project_context)
        topics=[]
        for rule in self.pack["topics"]:
            score,hits,subtopics=self._score(rule,q,mappings,project_blob,explicit.standard_code)
            if score<=0:continue
            confidence=self._confidence(score,rule["confidence_threshold"])
            topic_mappings=[m for m in mappings if m.original in question]
            topics.append(TopicMatch(rule["topic_id"],rule["profession"],rule["topic"],subtopics,confidence,round(score,2),hits,topic_mappings,list(rule["governing_standards"]),list(rule["primary_standards"]),list(rule["companion_standards"]),list(rule["query_expansions"])))
        topics.sort(key=lambda x:(-x.numeric_score,x.topic_id));topics=topics[:8]
        candidates=[];expansions=[]
        if explicit.explicit_standard:candidates.append(explicit.standard_code)
        for topic in topics:
            if topic.confidence=="low":continue
            candidates+=topic.governing_standards+topic.primary_standards+topic.companion_standards
            expansions+=topic.query_expansions
        allowed,warnings,statuses=self.policy.validate(_unique(candidates),explicit.standard_code,explicit.clause_no)
        if explicit.explicit_standard and explicit.standard_code not in allowed and not warnings:warnings.append("用户明示规范未通过当前状态校验。")
        stages=self._classify_context(question,project_context,"project_stages","project_stage")
        roles=self._classify_context(question,project_context,"user_roles","user_role")
        stage_rules={x["value"]:x for x in self.pack["project_stages"]}
        for stage in stages:
            if stage.confidence!="low":expansions+=stage_rules.get(stage.value,{}).get("query_expansions",[])
        claims=self._extract_claims(question)
        for claim in claims:
            if claim.claim_type=="numeric_requirement":warnings.append(f"检测到待验证数值主张：{claim.claim_value}{claim.claim_unit}；取得条文证据前不得确认。")
            elif claim.claim_type=="normative_status_claim":warnings.append("检测到用户提供的规范状态/强制属性主张，必须以状态服务和条文证据核验。")
            elif claim.claim_type=="evidence_bypass_request":warnings.append("检测到无证据索取条文号的请求；禁止编造条文号或把工程经验伪装为规范依据。")
        bindings=self._project_bindings(project_context);conflicts=[]
        if isinstance(project_context,dict) and project_context.get("conflicts_with_mandatory") and bindings!=["none"]:
            conflicts.append("项目文件要求与强制性工程建设规范存在明确冲突标记；项目文件不得突破强制性规范底线，需设计/审查/主管方确认。")
        warnings+=conflicts
        filtered=[x for x in statuses if not x.get("allowed")]
        deprecated=[x for x in filtered if any(k in x.get("status","") for k in ("废止","被替代","条文失效"))]
        authority=[{"code":code,"authority":self.policy.normative_authority(code)} for code in allowed]
        return RouteResult(
            question=question,explicit=explicit,topics=topics,preferred_standard_codes=_unique(allowed),expanded_query_terms=_unique(expansions),
            warnings=_unique(warnings),standard_statuses=statuses,
            project_stage=stages[0].value if stages else "unknown",project_stage_confidence=stages[0].confidence if stages else "low",project_stage_candidates=stages,
            user_role=roles[0].value if roles else "unknown",user_role_confidence=roles[0].confidence if roles else "low",user_role_candidates=roles,
            claims=claims,normative_authority=authority,project_binding=bindings,filtered_standards=filtered,deprecated_standards=deprecated,
            conflicts=conflicts,evidence_gate="requires_clause_evidence",router_is_evidence=False,
        )
