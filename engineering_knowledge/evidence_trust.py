from __future__ import annotations

import hashlib,re
from dataclasses import dataclass

from engineering_knowledge.models import EvidenceLink
from routing.standard_policy import StandardPolicyService


NORMATIVE_SOURCE_TYPES={"standard_clause","normative_clause"}
PROJECT_SOURCE_TYPES={"design_drawing","design_change","approved_method_statement","contract_requirement","owner_instruction","project_record"}
FORBIDDEN_NORMATIVE_SOURCE_TYPES={"user_statement","router_candidate","topic_candidate","retrieval_hint","llm_derived_analysis","derived_analysis"}


@dataclass(frozen=True)
class EvidenceTrustDecision:
    allowed:bool
    reason:str
    status_details:list[dict]


class EvidenceTrustPolicy:
    """Deterministic trust boundary. UI and LLM callers must not infer trust themselves."""

    def __init__(self,standard_policy:StandardPolicyService|None=None):
        self.standard_policy=standard_policy or StandardPolicyService()

    @staticmethod
    def is_traceable(evidence:EvidenceLink)->bool:
        if not evidence.source_id or not evidence.source_locator or not evidence.original_text:return False
        digest=hashlib.sha256(evidence.original_text.encode("utf-8")).hexdigest()
        return evidence.content_hash==digest

    @staticmethod
    def _verified(evidence:EvidenceLink)->bool:
        return evidence.verified is True and evidence.verification_status=="verified"

    @staticmethod
    def _normative_source_exists(evidence:EvidenceLink)->bool:
        clause_id=evidence.source_locator.get("clause_id") if isinstance(evidence.source_locator,dict) else None
        if not clause_id:return False
        try:
            from db import connect
            with connect() as con:
                row=con.execute("""SELECT c.clause_no,c.content,s.code FROM clauses c
                    JOIN standards s ON s.id=c.standard_id WHERE c.id=? LIMIT 1""",(clause_id,)).fetchone()
            if not row:return False
            norm=lambda value:re.sub(r"\s+","",(value or "").upper())
            return norm(row["code"])==norm(evidence.standard_code) and row["clause_no"]==evidence.clause_no and row["content"]==evidence.original_text
        except Exception:return False

    def is_current_normative_evidence(self,evidence:EvidenceLink)->EvidenceTrustDecision:
        if evidence.evidence_role!="normative_evidence":return EvidenceTrustDecision(False,"evidence_role 不是 normative_evidence",[])
        if evidence.source_type in FORBIDDEN_NORMATIVE_SOURCE_TYPES or evidence.source_type not in NORMATIVE_SOURCE_TYPES:return EvidenceTrustDecision(False,"来源类型不能作为规范证据",[])
        if not self._verified(evidence):return EvidenceTrustDecision(False,"证据尚未通过验证",[])
        if not self.is_traceable(evidence):return EvidenceTrustDecision(False,"证据无法回溯原始来源",[])
        if not evidence.standard_code or not evidence.clause_no:return EvidenceTrustDecision(False,"缺少规范编号或条文号",[])
        if not self._normative_source_exists(evidence):return EvidenceTrustDecision(False,"来源不能回溯到本地真实规范条文记录",[])
        expected_authority=self.standard_policy.normative_authority(evidence.standard_code)
        if evidence.normative_authority!=expected_authority:return EvidenceTrustDecision(False,"normative_authority 与权威目录不一致",[])
        allowed,warnings,statuses=self.standard_policy.validate([evidence.standard_code],evidence.standard_code,evidence.clause_no)
        exact=next((x for x in statuses if x.get("code")==evidence.standard_code),None)
        if not exact or not exact.get("allowed") or evidence.standard_code not in allowed:
            reason=(warnings or ["规范状态或条文 override 不允许作为当前依据"])[0]
            return EvidenceTrustDecision(False,reason,statuses)
        return EvidenceTrustDecision(True,"现行规范原文已验证且条文未被 override 阻断",statuses)

    def can_support_normative_claim(self,evidence:EvidenceLink)->EvidenceTrustDecision:
        return self.is_current_normative_evidence(evidence)

    def can_support_project_claim(self,evidence:EvidenceLink)->EvidenceTrustDecision:
        if evidence.evidence_role!="project_evidence":return EvidenceTrustDecision(False,"evidence_role 不是 project_evidence",[])
        if evidence.source_type not in PROJECT_SOURCE_TYPES:return EvidenceTrustDecision(False,"来源类型不是受支持的项目文件",[])
        lifecycle=str(evidence.status or "")
        if lifecycle in {"extracted","pending_confirmation","rejected","superseded","expired"}:
            return EvidenceTrustDecision(False,f"项目证据生命周期状态为 {lifecycle}",[])
        if not self._verified(evidence):return EvidenceTrustDecision(False,"项目文件尚未验证",[])
        if not self.is_traceable(evidence):return EvidenceTrustDecision(False,"项目文件无法回溯原始来源",[])
        if evidence.project_binding=="none":return EvidenceTrustDecision(False,"缺少项目约束类型",[])
        return EvidenceTrustDecision(True,"已验证项目文件可支持项目要求事实，但不能证明国家规范要求",[])
