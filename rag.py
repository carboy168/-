from __future__ import annotations
from pathlib import Path
from config_env import load_dotenv
from db import search_clauses, search_clauses_v2, search_clauses_v3
from core_library import build_status_warning
from router import route_question, build_route_query, route_summary
from project_mode import get_active_project, build_project_overlay, overlay_summary, project_context_text, log_project_query, resolve_standard_alias
from project_kb import search_project_chunks, build_project_evidence
from provider import ProviderConfigurationError
from provider_config import resolve_provider

load_dotenv()
BASE_DIR = Path(__file__).resolve().parent

def retrieve_with_route(question: str, limit: int = 10, project: dict|None = None):
    route = route_question(question)
    project = project if project is not None else get_active_project()
    overlay = build_project_overlay(question, route, project)
    routed_codes = list(dict.fromkeys(
        [resolve_standard_alias(x) for x in (route.get('primary_codes', []) + overlay.get('candidate_codes', []) + route.get('secondary_codes', []))]
    ))
    expanded = build_route_query(question, route)
    project_rows = []
    if project and project.get('id'):
        try:
            project_rows = search_project_chunks(project['id'], question, limit=8)
            if not project_rows:
                project_rows = search_project_chunks(project['id'], expanded, limit=8)
        except Exception:
            project_rows = []
    overlay['project_file_evidence'] = project_rows
    rows = search_clauses_v3(expanded, standard_codes=routed_codes, limit=limit) if routed_codes else []
    # 项目/路由候选内无条文时，退回全库检索。候选规范不是证据。
    if not rows:
        rows = search_clauses_v3(expanded, standard_codes=None, limit=limit)
    if not rows:
        rows = search_clauses_v2(question, limit=limit)
    if project:
        log_project_query(project.get('id'), question, route, overlay)
    return rows, route, overlay

def retrieve(question: str, limit: int = 8, project: dict|None = None):
    rows, _, _ = retrieve_with_route(question, limit=limit, project=project)
    return rows

def build_context(rows: list[dict]) -> str:
    parts = []
    for i, r in enumerate(rows, start=1):
        loc = r["clause_no"] or (f"第{r['page_no']}页" if r["page_no"] else "位置未识别")
        parts.append(
            f"""【证据{i}】
规范：{r['code']}《{r['title']}》
状态：{r['status']}
强制属性：{r['mandatory_level']}
实施日期：{r['effective_date']}
定位：{loc}
来源：{r['source_url']}
内容：
{r['content']}
"""
        )
    return "\n".join(parts)

def answer(question: str, rows: list[dict], route: dict|None = None, project: dict|None = None, overlay: dict|None = None) -> str:
    status_warning = build_status_warning(question)
    route = route or route_question(question)
    project = project if project is not None else get_active_project()
    overlay = overlay or build_project_overlay(question, route, project)
    project_file_rows = overlay.get('project_file_evidence', [])
    if not rows and not project_file_rows:
        prefix = (status_warning + "\n\n") if status_warning else ""
        project_note = ("\n\n项目模式：" + overlay_summary(overlay)) if project else ""
        local_note = ""
        if overlay.get('local_applicable'):
            local_note = "\n已识别广东地方规范候选，但当前未检索到其可引用全文条文，因此不能把候选当作正式依据。"
        return prefix + (
            "当前知识库未检索到足够的规范证据或项目文件证据，不能据此下确定结论。" + project_note + local_note + "\n\n"
            "建议先导入相关现行规范全文或项目文件，或用规范编号/图号/文件名称/更具体关键词重新检索。"
        )

    try:
        provider = resolve_provider(purpose="chat")
    except ProviderConfigurationError:
        evidence = build_context(rows) if rows else "（未检索到规范条文证据）"
        project_evidence = build_project_evidence(project_file_rows) if project_file_rows else "（未检索到项目文件证据）"
        return (
            "问题路由：" + route_summary(route) + "\n" +
            ("项目模式：" + overlay_summary(overlay) + "\n" if project else "") +
            "注意：规范条文证据与项目文件证据必须分开解释；项目文件不能冒充规范原文。\n\n" +
            "当前未完整配置 AI Provider，因此先返回证据，不生成AI综合结论。\n\n" +
            "【规范条文证据】\n" + evidence + "\n\n【项目文件证据】\n" + project_evidence
        )

    system_prompt = (BASE_DIR / "prompts" / "system_prompt.txt").read_text(encoding="utf-8")
    context = build_context(rows) if rows else "（当前未检索到可引用规范条文）"
    project_file_context = build_project_evidence(project_file_rows) if project_file_rows else "（当前未检索到相关项目文件文本证据）"
    user_input = f"""用户问题：
{question}

问题路由（只用于导航，不属于规范证据）：
{route_summary(route)}
建议补充条件：{("、".join(route.get("required_context", [])) or "无") }

项目模式背景（只用于适用性判断和检索导航，不属于规范原文证据）：
{project_context_text(project) if project else "未启用项目模式。"}
{overlay_summary(overlay)}
项目已确认控制条件：
{chr(10).join("【R" + str(i) + "】" + x.get("doc_type","") + "｜" + x.get("title","") + "｜" + x.get("source_ref","") + "｜" + x.get("requirement_text","") for i,x in enumerate(overlay.get("project_requirements",[])[:10], start=1)) or "- 暂无登记"}

项目文件检索证据（P编号，仅作为项目事实/责任/设计要求证据，不属于规范原文）：
{project_file_context}

规范状态前置检查：
{status_warning or "未命中本地旧规范/部分废止黑名单。"}

以下是知识库检索到的唯一可引用规范证据：
{context}

请严格按照系统规则回答。必须把三类内容分开：①规范条文证据N；②项目文件证据P/R；③工程判断。
不得把“问题路由”、项目档案、广东地方规范候选或项目文件证据当作规范原文依据；只有“规范条文证据”可以正式支撑规范编号和条文号。
项目文件证据可以回答设计要求、责任界面、移交条件、项目约定等项目事实，但必须引用P/R编号，并不得降低法律法规、强制性工程建设规范或其他必须执行的强制要求。
若只有项目文件证据而没有规范条文证据，可以回答“项目文件怎么要求/谁负责”，但必须明确“当前没有足够规范条文证据，不能据此宣称为规范要求”。
不得引用检索证据之外的规范编号或条文号。
如果“规范状态前置检查”提示某标准已废止或部分条文已废止，必须在结论前明确提示。
"""
    return provider.generate(
        model=provider.config.model,
        instructions=system_prompt,
        input=user_input,
    )
