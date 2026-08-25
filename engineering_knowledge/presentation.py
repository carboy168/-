from __future__ import annotations

from collections import Counter

from engineering_knowledge.models import KnowledgePackage

STATUS_LABELS={
    "compatible":"一致","project_stricter":"项目要求更严格","norm_stricter":"规范要求更严格",
    "conflict":"明确冲突","potential_conflict":"潜在冲突","not_comparable":"不可直接比较","insufficient_evidence":"证据不足",
}


def knowledge_summary(package:KnowledgePackage)->str:
    """Stable, user-facing summary. Internal scores and token matches stay out of the UI."""
    topics="、".join(item.get("id","") for item in package.topics if item.get("id")) or "未识别"
    evidence=Counter(item.verification_status for item in package.evidence_links if item.evidence_role!="user_statement")
    claims=Counter(item.verification_status for item in package.requirement_claims)
    conflicts=Counter(item.status for item in package.conflicts)
    lines=[
        "工程知识摘要",
        f"识别主题：{topics}",
        f"工程阶段：{package.project_stage or 'unknown'}",
        f"证据状态：已验证 {evidence.get('verified',0)}；未验证 {evidence.get('unverified',0)}；已阻断/失效 {evidence.get('blocked',0)+evidence.get('superseded',0)}",
        f"要求 Claim：已支持 {claims.get('supported',0)}；待核验 {claims.get('unverified',0)}；已阻断 {claims.get('blocked',0)}",
    ]
    if conflicts:
        lines.append("冲突检查："+"；".join(f"{STATUS_LABELS.get(key,key)} {value}" for key,value in conflicts.items()))
        for item in package.conflicts[:5]:lines.append(f"- {STATUS_LABELS.get(item.status,item.status)}：{item.reason}")
    else:lines.append("冲突检查：当前没有形成可比较的项目 Claim 与规范 Claim。")
    if package.human_confirmation_required:lines.append("结论边界：存在待确认信息；不得把路由候选、未验证项目文件或模型判断当作规范证据。")
    return "\n".join(lines)


def review_conflict_findings(package:KnowledgePackage,norm_rows:list[dict],project_rows:list[dict],requirements:list[dict])->tuple[list[dict],str]:
    norm_refs={f"ev-norm-{row.get('clause_id')}":f"N{i}" for i,row in enumerate(norm_rows,start=1)}
    project_refs={f"ev-project-{row.get('chunk_id')}":f"P{i}" for i,row in enumerate(project_rows,start=1)}
    project_refs.update({f"ev-project-req-{row.get('id')}":f"R{i}" for i,row in enumerate(requirements,start=1)})
    actionable={"conflict","norm_stricter","potential_conflict","insufficient_evidence"};findings=[]
    for item in package.conflicts:
        if item.status not in actionable:continue
        n_refs=[norm_refs[x] for x in item.evidence_links if x in norm_refs];p_refs=[project_refs[x] for x in item.evidence_links if x in project_refs]
        if item.status=="insufficient_evidence" and not p_refs:continue
        severity="高" if item.status in {"conflict","norm_stricter"} else ("中" if item.status=="potential_conflict" else "提示")
        evidence_trusted=item.comparison_basis.get("project_evidence_trusted",True) and item.comparison_basis.get("normative_evidence_trusted",True)
        findings.append({
            "severity":severity,"category":"确定性要求比对","location":"项目控制条件/规范条文","issue":f"{STATUS_LABELS[item.status]}：{item.reason}",
            "norm_refs":n_refs,"project_refs":p_refs,"recommendation":"核对所引用原文、适用条件及项目文件确认状态后，由责任人员确认处理。",
            "evidence_grade":"B" if item.status!="insufficient_evidence" and evidence_trusted else "D","confidence":{"high":"高","medium":"中","low":"低"}.get(item.confidence,"低"),
            "finding_type":"需核对","status":"待确认","notes":"由确定性 Claim 比较生成；不自动宣布项目文件不合规。",
        })
        if len(findings)>=20:break
    counts=Counter(item.status for item in package.conflicts)
    summary="；".join(f"{STATUS_LABELS.get(key,key)} {value}" for key,value in counts.items()) or "没有形成可比较的项目 Claim 与规范 Claim"
    return findings,summary
