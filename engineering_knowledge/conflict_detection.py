from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from engineering_knowledge.evidence_trust import EvidenceTrustPolicy
from engineering_knowledge.json_schema import validate_json_file
from engineering_knowledge.models import ConflictResult,KnowledgePackage,RequirementClaim
from engineering_knowledge.schema_validator import validate_knowledge_package

ROOT=Path(__file__).resolve().parents[1]
RULES_FILE=ROOT/"data"/"conflict_detection_rules.json"
RULES_SCHEMA=ROOT/"data"/"conflict_detection_rules.schema.json"


def load_conflict_detection_rules(path:Path|None=None)->dict:
    return validate_json_file(path or RULES_FILE,RULES_SCHEMA)


@dataclass(frozen=True)
class _Bound:
    value:float
    inclusive:bool


@dataclass(frozen=True)
class _Interval:
    lower:_Bound|None
    upper:_Bound|None
    family:str
    unit:str


class ConflictDetector:
    """Deterministic Claim comparator. It never decides evidence trust or normative status itself."""

    def __init__(self,rules:dict|None=None,trust:EvidenceTrustPolicy|None=None):
        self.rules=rules or load_conflict_detection_rules();self.trust=trust or EvidenceTrustPolicy()
        self.units={item["unit"].lower():item for item in self.rules["unit_conversions"]}
        self.positive=set(self.rules["positive_claim_types"]);self.prohibition=set(self.rules["prohibition_claim_types"])

    @staticmethod
    def _is_project(claim:RequirementClaim)->bool:return claim.project_binding!="none"

    @staticmethod
    def _is_normative(claim:RequirementClaim)->bool:return claim.project_binding=="none" and claim.source_type in {"standard_clause","normative_clause"}

    def _trusted(self,claim:RequirementClaim,package:KnowledgePackage,project:bool)->tuple[bool,str]:
        if claim.verification_status!="supported":return False,f"Claim 状态为 {claim.verification_status}"
        evidence=[item for item in package.evidence_links if item.evidence_id in claim.evidence_links]
        decisions=[self.trust.can_support_project_claim(item) if project else self.trust.can_support_normative_claim(item) for item in evidence]
        allowed=next((item for item in decisions if item.allowed),None)
        return (True,allowed.reason) if allowed else (False,(decisions[0].reason if decisions else "缺少 EvidenceLink"))

    def _tokens(self,text:str)->set[str]:
        value=text
        for term in self.rules["semantic_stop_terms"]:value=value.replace(term,"")
        value=re.sub(r"[\W\d_]+","",value)
        return {value[i:i+2] for i in range(max(0,len(value)-1))}

    def _opposing(self,left:RequirementClaim,right:RequirementClaim)->bool:
        return (left.claim_type in self.prohibition and right.claim_type in self.positive) or (right.claim_type in self.prohibition and left.claim_type in self.positive)

    def _match(self,project:RequirementClaim,norm:RequirementClaim)->tuple[int,dict]:
        weights=self.rules["matching_weights"];score=0;basis={}
        shared=sorted(set(project.subject)&set(norm.subject));basis["shared_subjects"]=shared
        if shared:score+=weights["shared_subject"]
        same_property=bool(project.property and project.property==norm.property);basis["same_property"]=same_property
        if same_property:score+=weights["same_property"]
        numeric=project.claim_type==norm.claim_type=="numeric_requirement";basis["numeric_pair"]=numeric
        if numeric:score+=weights["numeric_pair"]
        same_type=project.claim_type==norm.claim_type;basis["same_claim_type"]=same_type
        if same_type:score+=weights["same_claim_type"]
        opposing=self._opposing(project,norm);basis["opposing_requirement"]=opposing
        if opposing:score+=weights["opposing_requirement"]
        overlap=sorted(self._tokens(project.original_text)&self._tokens(norm.original_text));basis["lexical_overlap"]=overlap
        if overlap:score+=weights["lexical_overlap"]
        basis["match_score"]=score
        return score,basis

    def _interval(self,claim:RequirementClaim)->_Interval|None:
        try:value=float(claim.value)
        except (TypeError,ValueError):return None
        unit=self.units.get((claim.unit or "").lower())
        if not unit:return None
        value*=float(unit["factor"]);operator=claim.operator or "eq"
        if operator=="eq":return _Interval(_Bound(value,True),_Bound(value,True),unit["family"],unit["canonical_unit"])
        if operator in {"gte","gt"}:return _Interval(_Bound(value,operator=="gte"),None,unit["family"],unit["canonical_unit"])
        if operator in {"lte","lt"}:return _Interval(None,_Bound(value,operator=="lte"),unit["family"],unit["canonical_unit"])
        return None

    @staticmethod
    def _lower_at_least(left:_Bound|None,right:_Bound|None)->bool:
        if right is None:return True
        if left is None:return False
        return left.value>right.value or (left.value==right.value and (right.inclusive or not left.inclusive))

    @staticmethod
    def _upper_at_most(left:_Bound|None,right:_Bound|None)->bool:
        if right is None:return True
        if left is None:return False
        return left.value<right.value or (left.value==right.value and (right.inclusive or not left.inclusive))

    def _subset(self,left:_Interval,right:_Interval)->bool:
        return self._lower_at_least(left.lower,right.lower) and self._upper_at_most(left.upper,right.upper)

    @staticmethod
    def _disjoint(left:_Interval,right:_Interval)->bool:
        for upper,lower in ((left.upper,right.lower),(right.upper,left.lower)):
            if upper is not None and lower is not None:
                if upper.value<lower.value:return True
                if upper.value==lower.value and not (upper.inclusive and lower.inclusive):return True
        return False

    @staticmethod
    def _interval_dict(interval:_Interval)->dict:
        bound=lambda item:None if item is None else {"value":item.value,"inclusive":item.inclusive}
        return {"lower":bound(interval.lower),"upper":bound(interval.upper),"family":interval.family,"unit":interval.unit}

    def _compare(self,project:RequirementClaim,norm:RequirementClaim,basis:dict)->tuple[str,str,str,bool,list[str],dict]:
        if project.conditions!=norm.conditions or project.exceptions!=norm.exceptions:
            if project.conditions or norm.conditions or project.exceptions or norm.exceptions:
                return "potential_conflict","适用条件或例外条款不同，不能直接作确定性强弱判断。","medium",True,["需核对项目与规范要求的适用范围。"],basis
        if project.claim_type==norm.claim_type=="numeric_requirement":
            p_interval=self._interval(project);n_interval=self._interval(norm)
            if not p_interval or not n_interval or p_interval.family!=n_interval.family:
                return "not_comparable","数值、单位或量纲不能可靠归一化。","low",True,["不得跨量纲比较要求。"],basis
            basis=dict(basis,project_interval=self._interval_dict(p_interval),normative_interval=self._interval_dict(n_interval))
            if self._disjoint(p_interval,n_interval):return "conflict","项目约束与规范约束的允许区间不相交。","high",True,["不得自动裁决项目文件合规性。"],basis
            p_subset=self._subset(p_interval,n_interval);n_subset=self._subset(n_interval,p_interval)
            if p_subset and n_subset:return "compatible","项目与规范数值约束等价。","high",False,[],basis
            if p_subset:return "project_stricter","项目允许区间是规范允许区间的真子集。","high",False,[],basis
            if n_subset:return "norm_stricter","规范允许区间是项目允许区间的真子集。","high",True,["项目要求可能低于规范底线，需人工复核。"],basis
            return "potential_conflict","项目与规范数值区间部分重叠但互不包含。","medium",True,["需结合具体取值和适用条件复核。"],basis
        enough_overlap=len(basis.get("lexical_overlap",[]))>=self.rules["minimum_semantic_overlap"]
        if self._opposing(project,norm) and enough_overlap:
            return "conflict","同一工程行为在项目与规范 Claim 中呈现相反要求。","high",True,["不得自动宣布项目文件不合规。"],basis
        if project.claim_type==norm.claim_type and (project.original_text==norm.original_text or (project.action==norm.action and enough_overlap)):
            return "compatible","项目与规范 Claim 的要求类型及行为一致。","medium",False,[],basis
        if basis.get("same_property") or basis.get("shared_subjects"):
            return "potential_conflict","Claim 指向相同对象或属性，但现有结构不足以确定兼容或冲突。","medium",True,["需要核对原文语义、条件和例外。"],basis
        return "not_comparable","两个 Claim 缺少可证明的共同对象或属性。","low",True,[],basis

    def detect(self,package:KnowledgePackage)->list[ConflictResult]:
        projects=[item for item in package.requirement_claims if self._is_project(item)]
        norms=[item for item in package.requirement_claims if self._is_normative(item)]
        results=[]
        if not projects and not norms:return results
        if not projects or not norms:
            project=projects[0] if projects else None;norm=norms[0] if norms else None
            return [self._result(1,"insufficient_evidence",project,norm,"缺少可配对的项目 Claim 或规范 Claim。",{},"low",True,["Router candidate 不能补足证据。"])]
        for project in projects:
            project_ok,project_reason=self._trusted(project,package,True)
            ranked=[]
            for norm in norms:
                score,basis=self._match(project,norm);norm_ok,norm_reason=self._trusted(norm,package,False)
                ranked.append((score,basis,norm,norm_ok,norm_reason))
            comparable=[item for item in ranked if item[3] and item[0]>=self.rules["minimum_comparable_score"]]
            selected=comparable if project_ok and comparable else [max(ranked,key=lambda item:(item[3],item[0]))]
            for score,basis,norm,norm_ok,norm_reason in selected:
                basis=dict(basis,project_evidence_trusted=project_ok,normative_evidence_trusted=norm_ok)
                if not project_ok or not norm_ok:
                    status="insufficient_evidence";reason=f"证据不足：项目 Claim（{project_reason}）；规范 Claim（{norm_reason}）。";confidence="low";review=True;warnings=["未验证项目文件、失效规范或被 override 条文不得参与确定性冲突结论。"]
                elif score<self.rules["minimum_comparable_score"]:
                    status="not_comparable";reason="两个已验证 Claim 的对象、属性或语义匹配度不足。";confidence="low";review=True;warnings=[]
                else:status,reason,confidence,review,warnings,basis=self._compare(project,norm,basis)
                results.append(self._result(len(results)+1,status,project,norm,reason,basis,confidence,review,warnings))
        return results

    @staticmethod
    def _result(index:int,status:str,project:RequirementClaim|None,norm:RequirementClaim|None,reason:str,basis:dict,confidence:str,review:bool,warnings:list[str])->ConflictResult:
        links=list(dict.fromkeys((project.evidence_links if project else [])+(norm.evidence_links if norm else [])))
        return ConflictResult(f"conflict-{index:03d}",status,project.claim_id if project else "",norm.claim_id if norm else "",links,reason,basis,confidence,review,warnings)


def detect_claim_conflicts(package:KnowledgePackage,detector:ConflictDetector|None=None)->KnowledgePackage:
    package.conflicts=(detector or ConflictDetector()).detect(package)
    if any(item.requires_human_review for item in package.conflicts):package.human_confirmation_required=True
    validate_knowledge_package(package)
    return package
