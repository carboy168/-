from __future__ import annotations

import hashlib,re
from pathlib import Path

from engineering_knowledge.evidence_trust import EvidenceTrustPolicy
from engineering_knowledge.json_schema import validate_json_file
from engineering_knowledge.models import EvidenceLink,KnowledgePackage,RequirementClaim
from engineering_knowledge.schema_validator import validate_knowledge_package

ROOT=Path(__file__).resolve().parents[1]
RULES_FILE=ROOT/"data"/"requirement_claim_rules.json"
RULES_SCHEMA=ROOT/"data"/"requirement_claim_rules.schema.json"
NUMBER_RE=re.compile(r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>MPa|kN|mm|cm|m|毫米|厘米|米|%)",re.I)


def load_requirement_claim_rules(path:Path|None=None)->dict:
    selected=path or RULES_FILE
    return validate_json_file(selected,RULES_SCHEMA)


def _sentence_spans(text:str,delimiters:list[str]):
    pattern="["+re.escape("".join(delimiters))+"]+";start=0
    for match in re.finditer(pattern,text):
        sentence=text[start:match.start()].strip()
        if sentence:
            actual=text.find(sentence,start,match.start());yield sentence,actual,actual+len(sentence)
        start=match.end()
    sentence=text[start:].strip()
    if sentence:
        actual=text.find(sentence,start);yield sentence,actual,actual+len(sentence)


class RequirementClaimExtractor:
    def __init__(self,rules:dict|None=None):
        self.rules=rules or load_requirement_claim_rules();self.claim_rules=sorted(self.rules["claim_rules"],key=lambda x:-x["priority"])

    def _rule(self,sentence:str):
        numeric=NUMBER_RE.search(sentence)
        for rule in self.claim_rules:
            if rule["claim_type"]=="numeric_requirement":
                if numeric:return rule
            elif any(term in sentence for term in rule["terms"]):return rule
        return None

    def _operator(self,sentence:str)->str:
        for item in self.rules["operators"]:
            if any(term in sentence for term in item["terms"]):return item["operator"]
        return "eq" if NUMBER_RE.search(sentence) else ""

    @staticmethod
    def _conditions(sentence:str)->list[str]:
        return list(dict.fromkeys(match.group(0).rstrip("，,") for match in re.finditer(r"(?:当|在).{1,30}?(?:时|情况下)[，,]?",sentence)))

    @staticmethod
    def _exceptions(sentence:str)->list[str]:
        return list(dict.fromkeys(match.group(0) for match in re.finditer(r"除.{1,30}?外",sentence)))

    @staticmethod
    def _subjects(sentence:str,package:KnowledgePackage)->list[str]:
        result=[]
        for item in package.objects:
            terms=[item.original_text,item.canonical_name]+item.aliases
            if any(term and term in sentence for term in terms):result.append(item.object_id)
        return list(dict.fromkeys(result))

    def extract(self,package:KnowledgePackage,evidence:EvidenceLink,start_index:int)->list[RequirementClaim]:
        claims=[];content=evidence.original_text
        properties={"numeric_requirement":"dimension","prohibition_requirement":"prohibition","acceptance_requirement":"acceptance","responsibility_requirement":"responsibility","sequence_requirement":"sequence","material_requirement":"material","mandatory_requirement":"mandatory","method_requirement":"method"}
        for sentence,start,end in _sentence_spans(content,self.rules["sentence_delimiters"]):
            rule=self._rule(sentence)
            if not rule:continue
            number=NUMBER_RE.search(sentence);claim_id=f"claim-{start_index+len(claims):03d}"
            action=next((term for term in rule["terms"] if term in sentence),rule["rule_id"])
            supported=evidence.verified and evidence.verification_status=="verified"
            claims.append(RequirementClaim(
                claim_id=claim_id,claim_type=rule["claim_type"],source_type=evidence.source_type,source_id=evidence.source_id,
                source_locator={"kind":"standard_clause","clause_id":evidence.source_locator.get("clause_id"),"standard_code":evidence.standard_code,"clause_no":evidence.clause_no,"start":start,"end":end},
                original_text=sentence,subject=self._subjects(sentence,package),property=properties.get(rule["claim_type"],"requirement"),
                operator=self._operator(sentence),value=number.group("value") if number else "",unit=number.group("unit") if number else "",action=action,
                conditions=self._conditions(sentence),exceptions=self._exceptions(sentence),project_stage=package.project_stage,
                normative_authority=evidence.normative_authority,project_binding=evidence.project_binding,standard_status=evidence.status,
                verification_status="supported" if supported else ("unverified" if evidence.verification_status=="unverified" else "blocked"),
                extraction_confidence=rule["confidence"],evidence_links=[evidence.evidence_id],
                warnings=[] if evidence.verified else list(evidence.warnings),
            ))
        return claims


def evidence_from_clause_row(row:dict,trust:EvidenceTrustPolicy|None=None)->EvidenceLink:
    trust=trust or EvidenceTrustPolicy();content=str(row.get("content","") or "");code=str(row.get("code","") or "");clause_no=str(row.get("clause_no","") or "")
    clause_id=row.get("clause_id")
    page_no=row.get("page_no");page_no=page_no if isinstance(page_no,int) and page_no>0 else None
    source_token=str(clause_id) if clause_id is not None else "missing-"+hashlib.sha256(f"{code}|{clause_no}|{content}".encode("utf-8")).hexdigest()[:12]
    evidence=EvidenceLink(
        evidence_id=f"ev-norm-{source_token}",source_type="standard_clause",source_id=f"clause:{source_token}",
        source_locator={"kind":"standard_clause","clause_id":clause_id,"source_file":row.get("source_file","")},original_text=content,
        standard_code=code,clause_no=clause_no,page_no=page_no,document_name=str(row.get("title","") or ""),
        status=str(row.get("status","") or "unverified"),normative_authority=trust.standard_policy.normative_authority(code),project_binding="none",
        content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),verified=True,evidence_role="normative_evidence",verification_status="verified",
    )
    decision=trust.can_support_normative_claim(evidence)
    if not decision.allowed:
        evidence.verified=False;evidence.verification_status="blocked"
        if any(x.get("status") in {"废止/被替代","条文失效"} for x in decision.status_details):evidence.verification_status="superseded"
        evidence.warnings.append(decision.reason)
    return evidence


def evidence_from_project_row(row:dict)->EvidenceLink:
    content=str(row.get("content","") or "");chunk_id=row.get("chunk_id")
    page_no=row.get("page_no");page_no=page_no if isinstance(page_no,int) and page_no>0 else None
    source_token=str(chunk_id) if chunk_id is not None else "missing-"+hashlib.sha256(content.encode("utf-8")).hexdigest()[:12]
    doc_type=str(row.get("doc_type","") or "")
    mappings=(
        (("设计变更",),"design_change","design_change"),
        (("图纸",),"design_drawing","design_drawing"),
        (("已审批施工方案","批准施工方案"),"approved_method_statement","approved_method_statement"),
        (("合同",),"contract_requirement","contract_requirement"),
        (("甲方指令","业主指令"),"owner_instruction","owner_instruction"),
    )
    source_type,project_binding="project_record","project_record"
    for terms,candidate_source,candidate_binding in mappings:
        if any(term in doc_type for term in terms):source_type,project_binding=candidate_source,candidate_binding;break
    return EvidenceLink(
        evidence_id=f"ev-project-{source_token}",source_type=source_type,source_id=f"project_chunk:{source_token}",
        source_locator={"kind":"project_file_chunk","chunk_id":chunk_id,"file_id":row.get("file_id"),"page_no":page_no,"section":row.get("section","")},
        original_text=content,page_no=page_no,document_name=str(row.get("title","") or row.get("original_name","") or ""),
        status="unverified",normative_authority="",project_binding=project_binding,content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        verified=False,evidence_role="project_evidence",verification_status="unverified",warnings=["项目文件尚未经过人工确认，不得作为现行规范要求。"],
    )


def bind_retrieved_clauses(package:KnowledgePackage,rows:list[dict])->KnowledgePackage:
    existing={item.evidence_id for item in package.evidence_links};extractor=RequirementClaimExtractor();trust=EvidenceTrustPolicy()
    next_claim=len(package.requirement_claims)+1
    for row in rows:
        evidence=evidence_from_clause_row(row,trust)
        if evidence.evidence_id in existing:continue
        claims=extractor.extract(package,evidence,next_claim);next_claim+=len(claims)
        evidence.linked_claim_ids.extend(item.claim_id for item in claims);package.evidence_links.append(evidence);package.requirement_claims.extend(claims);existing.add(evidence.evidence_id)
        for plan in package.retrieval_plans:
            terms=[term for term in plan.query_terms if len(term)>=2]
            if evidence.standard_code in plan.preferred_standard_codes or (not plan.preferred_standard_codes and any(term in evidence.original_text for term in terms)):
                if evidence.evidence_id not in plan.evidence_link_ids:plan.evidence_link_ids.append(evidence.evidence_id)
                if evidence.verified:plan.evidence_status="verified"
    package.human_confirmation_required=package.human_confirmation_required or any(x.verification_status!="supported" for x in package.requirement_claims)
    validate_knowledge_package(package)
    return package


def bind_project_chunks(package:KnowledgePackage,rows:list[dict])->KnowledgePackage:
    """Extract traceable, unverified project claims without promoting them to normative evidence."""
    existing={item.evidence_id for item in package.evidence_links};extractor=RequirementClaimExtractor();next_claim=len(package.requirement_claims)+1
    for row in rows:
        evidence=evidence_from_project_row(row)
        if evidence.evidence_id in existing:continue
        claims=extractor.extract(package,evidence,next_claim);next_claim+=len(claims)
        evidence.linked_claim_ids.extend(item.claim_id for item in claims);package.evidence_links.append(evidence);package.requirement_claims.extend(claims);existing.add(evidence.evidence_id)
    package.human_confirmation_required=package.human_confirmation_required or bool(rows)
    validate_knowledge_package(package)
    return package
