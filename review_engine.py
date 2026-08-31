from __future__ import annotations
import base64, json, logging, mimetypes, re
from pathlib import Path
from config_env import load_dotenv
from db import search_clauses_v3
from router import route_question, build_route_query
from project_mode import build_project_overlay, project_context_text, list_project_requirements, resolve_standard_alias
from project_kb import search_project_chunks, build_project_evidence, save_review, DIRECT_REVIEW_EXTS, extract_file_text
from provider import ProviderCapabilityError
from provider_config import resolve_provider

load_dotenv()
MAX_REQUEST_BYTES = 48 * 1024 * 1024
REVIEW_TYPES = ['施工图审查','施工方案审查','专项施工方案审查','项目文件综合技术审查']

DRAWING_CHECKS = [
    ('建筑/装饰','空间尺寸、净高、通道、门、楼梯、栏杆和构造是否存在明显冲突'),
    ('消防','防火分隔、疏散路径、防火门、装修材料防火要求是否需核对'),
    ('消防设施','喷淋、报警、应急照明、消火栓等与吊顶/装饰是否冲突'),
    ('给排水','卫生间地漏、排水坡度、管线、检修条件、防水节点是否完整'),
    ('电气','配电、线管、电缆、接地、检修和吊顶内安装是否有明显风险'),
    ('防水','卫生间/屋面/外墙/管根等防水构造和排水条件是否完整'),
    ('无障碍','无障碍通道、卫生间、门、坡道、扶手等是否需核对'),
    ('结构/改造','拆墙、开洞、植筋、荷载变化等是否涉及结构安全或需设计确认'),
    ('专业协调','装饰、消防、机电、暖通标高和检修口是否存在碰撞/遗漏'),
    ('施工可实施性','节点、材料、尺寸、标高、做法是否存在无法施工或表达不清')
]
PLAN_CHECKS = [
    ('编制依据','引用规范是否现行，是否存在旧规范或已废止条文'),
    ('工程概况','范围、施工条件、难点、界面和责任是否交代清楚'),
    ('施工工艺','工序、基层处理、材料、节点、允许偏差、成品保护是否可执行'),
    ('质量控制','材料进场、隐蔽验收、检验批、试验检测和验收标准是否完整'),
    ('安全措施','高处作业、脚手架、临时用电、机械、动火、临边洞口是否覆盖'),
    ('消防/防火','施工过程消防、防火材料、动火和消防设施保护是否覆盖'),
    ('进度组织','工序穿插、劳动力、材料、机械和关键线路是否合理'),
    ('项目接口','总包/分包/设计/监理/甲方界面、移交条件和前置工作是否明确'),
    ('应急措施','渗漏、停电、火灾、人员伤害、材料质量等应急处置是否具备'),
    ('项目适配','是否结合当前项目地区、改造属性、现场约束和广东地方要求')
]


def _mime(path:Path):return mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
def _data_url(path:Path):return f"data:{_mime(path)};base64,"+base64.b64encode(path.read_bytes()).decode('ascii')


def _supports(provider,capability:str)->bool:
    checker=getattr(provider,"supports",None)
    return bool(checker(capability)) if callable(checker) else True


def _review_content(provider,prompt:str,paths:list[Path],review_type:str):
    content=[{'type':'input_text','text':prompt}];modes=[];warnings=[]
    for p in paths:
        ext=p.suffix.lower()
        if ext in ('.png','.jpg','.jpeg','.webp'):
            if not _supports(provider,"image_input"):
                raise ProviderCapabilityError("当前兼容接口不支持图片审查，请切换已确认支持图片输入的 Provider。",provider=getattr(provider,"provider_id",""))
            content.append({'type':'input_image','image_url':_data_url(p),'detail':'high'});modes.append('image')
            continue
        if _supports(provider,"direct_file_input"):
            item={'type':'input_file','filename':p.name,'file_data':_data_url(p)}
            content.append(item);modes.append('direct_file');continue
        if ext=='.pdf':
            raise ProviderCapabilityError("当前兼容接口不支持直接 PDF/文件审查，请切换官方 OpenAI 或使用文本/图片审查模式。",provider=getattr(provider,"provider_id",""))
        chunks,_=extract_file_text(str(p))
        extracted='\n\n'.join(str(x.get('content','')) for x in chunks if x.get('content')).strip()
        if not extracted and ext in ('.txt','.md','.json','.csv','.xml','.html'):
            extracted=p.read_text(encoding='utf-8',errors='ignore').strip()
        if not extracted:
            raise ProviderCapabilityError("当前兼容接口不支持直接文件审查，且该文件未能抽取出可审查文本。",provider=getattr(provider,"provider_id",""))
        marker=f"【兼容降级：{p.name} 仅按抽取文本审查，不包含原始版式、图片或图表】\n"
        content.append({'type':'input_text','text':marker+extracted[:160000]});modes.append('extracted_text')
        warnings.append(f"{p.name} 已使用文本抽取兼容模式，未审查原始版式/图片。")
    mode='mixed' if len(set(modes))>1 else (modes[0] if modes else 'text')
    if warnings:logging.warning("Review input downgrade | provider=%s | mode=%s | files=%s",getattr(provider,"provider_id",""),mode,",".join(p.name for p in paths))
    return content,mode,warnings


def _norm_evidence(project:dict, review_type:str, scope:str, max_rows:int=28):
    checks=DRAWING_CHECKS if review_type=='施工图审查' else PLAN_CHECKS
    rows=[];seen=set()
    for _,q in checks:
        question=f'{scope} {q}'.strip();route=route_question(question,project_context=project);overlay=build_project_overlay(question,route,project)
        codes=list(dict.fromkeys([resolve_standard_alias(x) for x in route.get('primary_codes',[])+overlay.get('candidate_codes',[])+route.get('secondary_codes',[])]))
        got=search_clauses_v3(build_route_query(question,route),standard_codes=codes or None,limit=4)
        for r in got:
            if r['clause_id'] not in seen:
                seen.add(r['clause_id']);rows.append(r)
                if len(rows)>=max_rows:return rows
    return rows


def _format_norm(rows):
    out=[]
    for i,r in enumerate(rows,start=1):
        loc=r.get('clause_no') or (f"第{r.get('page_no')}页" if r.get('page_no') else '位置未识别')
        out.append(f"【N{i}】{r.get('code')}《{r.get('title')}》｜{loc}｜{r.get('status')}\n{r.get('content')}")
    return '\n\n'.join(out)


def _extract_json(text:str):
    text=(text or '').strip()
    if text.startswith('```'):
        text=re.sub(r'^```(?:json)?\s*','',text);text=re.sub(r'\s*```$','',text)
    try:return json.loads(text)
    except Exception:pass
    a=text.find('{');b=text.rfind('}')
    if a>=0 and b>a:return json.loads(text[a:b+1])
    raise ValueError('模型返回内容不是有效JSON。')


def validate_review_result(result:dict,n_count:int,p_count:int,r_count:int):
    if not isinstance(result,dict):result={}
    result.setdefault('summary','');result.setdefault('findings',[])
    allowed_n={f'N{i}' for i in range(1,n_count+1)};allowed_p={f'P{i}' for i in range(1,p_count+1)};allowed_r={f'R{i}' for i in range(1,r_count+1)}
    for f in result['findings']:
        f['norm_refs']=[x for x in f.get('norm_refs',[]) if x in allowed_n]
        f['project_refs']=[x for x in f.get('project_refs',[]) if x in allowed_p|allowed_r]
        f['severity']=f.get('severity','提示') if f.get('severity') in ('高','中','低','提示') else '提示'
        f['evidence_grade']=f.get('evidence_grade','D') if f.get('evidence_grade') in ('A','B','C','D') else 'D'
        f['confidence']=f.get('confidence','中') if f.get('confidence') in ('高','中','低') else '中'
        f['finding_type']=f.get('finding_type','需核对') if f.get('finding_type') in ('确定问题','需核对','建议优化','符合项') else '需核对'
        f['status']=f.get('status','待确认') if f.get('status') in ('待确认','待整改','已整改','已关闭','提示') else '待确认'
        if not f['norm_refs'] and f['evidence_grade'] in ('A','B'):
            f['evidence_grade']='C' if f['project_refs'] else 'D'
            f['notes']=(f.get('notes','')+'；未绑定有效规范证据，证据等级已自动降级').strip('；')
    return result


def normalize_model_findings_for_confirmation(result:dict)->dict:
    """Fail closed: model output can describe a finding, but cannot assign workflow authority."""
    for finding in result.get('findings',[]):
        finding['status']='待确认'
        finding['discovery_mode']='model_assisted'
    return result


def _engineering_conflict_overlay(project:dict,review_type:str,scope:str,norms:list[dict],project_rows:list[dict],requirements:list[dict]):
    """Additive C4 path; failure must not break the established review workflow."""
    from engineering_knowledge.conflict_detection import detect_claim_conflicts
    from engineering_knowledge.layer import build_knowledge_package
    from engineering_knowledge.presentation import review_conflict_findings
    from engineering_knowledge.requirement_claims import bind_project_chunks,bind_project_requirements,bind_retrieved_clauses
    question=" ".join([review_type,scope]+[str(item.get("requirement_text","") or "") for item in requirements]).strip()
    package=build_knowledge_package(question or review_type,project_context=project)
    bind_retrieved_clauses(package,norms);bind_project_chunks(package,project_rows);bind_project_requirements(package,requirements);detect_claim_conflicts(package)
    findings,summary=review_conflict_findings(package,norms,project_rows,requirements)
    return package,findings,summary


def run_review(project:dict,file_paths:list[str],review_type:str,scope:str='',title:str='',workflow_store=None):
    if not project:raise ValueError('必须先启用项目模式。')
    if review_type not in REVIEW_TYPES:raise ValueError('未知审查类型。')
    paths=[Path(x) for x in file_paths if Path(x).exists()]
    if not paths:raise ValueError('没有可审查文件。')
    total=sum(p.stat().st_size for p in paths)
    if total>MAX_REQUEST_BYTES:raise ValueError('本次文件合计超过48MB安全上限，请拆分审查。')
    for p in paths:
        if p.suffix.lower() not in DIRECT_REVIEW_EXTS:raise ValueError(f'暂不支持直接审查：{p.suffix}。CAD请导出PDF。')
    provider=resolve_provider(purpose="review");model=provider.config.model

    norms=_norm_evidence(project,review_type,scope)
    p_rows=search_project_chunks(project['id'],scope or ('图纸 设计 变更 审图 消防 要求' if review_type=='施工图审查' else '施工方案 技术要求 工艺 验收 安全 责任'),limit=16)
    reqs=list_project_requirements(project['id'])[:15]
    norm_text=_format_norm(norms) or '（当前规范全文库没有检索到可引用条文）'
    project_text=build_project_evidence(p_rows) or '（当前未检索到相关项目文件文本证据）'
    req_text='\n'.join(f"【R{i}】{r['doc_type']}｜{r['title']}｜{r['source_ref']}\n{r['requirement_text']}" for i,r in enumerate(reqs,start=1)) or '（当前没有已确认项目控制条件）'
    checks=DRAWING_CHECKS if review_type=='施工图审查' else PLAN_CHECKS
    checklist='\n'.join(f'- {cat}：{q}' for cat,q in checks)
    prompt=f'''你是工程图纸与施工方案审查助手。请审查用户提交的文件，但必须严格区分“规范证据、项目文件证据、工程判断”。\n\n当前项目：\n{project_context_text(project)}\n\n审查类型：{review_type}\n审查范围/重点：{scope or '按V1.0默认清单全面审查'}\n\n默认审查清单：\n{checklist}\n\n【唯一允许作为规范依据的本地规范证据】\n{norm_text}\n\n【项目文件检索证据】\n{project_text}\n\n【项目已确认控制条件】\n{req_text}\n\n严格规则：\n1. 可以从提交文件本身发现问题，但不得凭模型记忆编造规范编号或条文号。\n2. norm_refs只能填写上面存在的N编号；没有对应N证据时规范依据必须留空。\n3. project_refs只能填写上面存在的P或R编号。\n4. 图纸PDF请结合页面图像和文字审查；看不清尺寸/图号不得猜。\n5. DOCX/PPTX等非PDF文件的嵌入图片/图表可能未完整呈现，依赖图形的信息标“需核对”。\n6. 区分确定问题、需核对、建议优化、符合项，不要为了数量制造问题。\n7. 高风险优先：结构拆改、消防疏散、防火分隔、脚手架/高处作业、临时用电、防水渗漏、主要机电安全。\n8. 项目文件不得降低强制性要求；疑似冲突时提出确认。\n9. 所有模型发现的初始状态只能填写“待确认”，模型无权直接进入整改或确认状态。\n10. 只返回有效JSON，不要Markdown。\n\nJSON格式：{{"summary":"总评","findings":[{{"severity":"高|中|低|提示","category":"专业","location":"页码/图号/章节","issue":"问题","norm_refs":["N1"],"project_refs":["P1","R1"],"recommendation":"建议","evidence_grade":"A|B|C|D","confidence":"高|中|低","finding_type":"确定问题|需核对|建议优化|符合项","status":"待确认","notes":"说明"}}]}}'''
    content,input_mode,input_warnings=_review_content(provider,prompt,paths,review_type)
    text=provider.generate(model=model,input=[{'role':'user','content':content}])
    result=normalize_model_findings_for_confirmation(
        validate_review_result(_extract_json(text),len(norms),len(p_rows),len(reqs))
    )
    if input_warnings:result['summary']=("输入模式提示："+"；".join(input_warnings)+"\n"+result.get('summary','')).strip()
    model_findings=list(result['findings'])
    conflict_meta=[];conflict_summary="未执行"
    package=None
    try:
        package,deterministic_findings,conflict_summary=_engineering_conflict_overlay(project,review_type,scope,norms,p_rows,reqs)
        result['findings'].extend(deterministic_findings);result=validate_review_result(result,len(norms),len(p_rows),len(reqs))
        conflict_meta=package.to_dict()['conflicts']
        if conflict_meta:result['summary']=(result.get('summary','')+f"\n确定性要求比对：{conflict_summary}。").strip()
    except Exception as exc:
        logging.exception("Engineering conflict overlay failed");conflict_summary=f"已回退原审查流程（{type(exc).__name__}）"
    result['meta']={'review_type':review_type,'model':model,'norm_evidence_count':len(norms),'project_evidence_count':len(p_rows),'project_requirement_count':len(reqs),'files':[p.name for p in paths],
                    'input_mode':input_mode,'input_warnings':input_warnings,
                    'engineering_conflict_summary':conflict_summary,'engineering_conflicts':conflict_meta}
    # V1.3-C workflow state is intentionally attached only after the legacy save.
    # It is an explicit runtime compatibility interface and is never persisted by this path.
    workflow_payload=[]
    try:
        from engineering_knowledge.review_workflow import ReviewFindingStore
        workflow_store=workflow_store or ReviewFindingStore()
        workflow_store.ingest_model_findings(model_findings)
        if package is not None:workflow_store.ingest_conflicts(package)
        workflow_payload=[item.to_dict() for item in workflow_store.all()]
        result['meta']['review_workflow_summary']=f"运行时审查问题 {len(workflow_payload)} 项"
    except Exception as exc:
        logging.exception("Runtime review workflow failed");result['meta']['review_workflow_summary']=f"已回退旧审查闭环（{type(exc).__name__}）"
    result['review_id']=save_review(project['id'],review_type,title or f'{review_type}-{paths[0].stem}',scope,model,[p.name for p in paths],result)
    result['review_workflow']=workflow_payload
    return result


def extract_control_candidates(project:dict,file_path:str,doc_type:str,title:str):
    p=Path(file_path)
    if p.stat().st_size>MAX_REQUEST_BYTES:raise ValueError('文件超过V1.0单次48MB安全上限。')
    provider=resolve_provider(purpose="review");model=provider.config.model
    prompt=f'''从该项目文件中提取会影响施工做法、材料选型、验收、责任界面、移交条件、工期或报审的明确控制条件。项目：{project_context_text(project)}。文件类型：{doc_type}，文件名：{title}。只返回JSON：{{"items":[{{"title":"短标题","requirement_text":"明确控制要求","source_ref":"页码/章节/图号，无法识别则待定位","priority":50}}]}}。不要把建议当成文件明确要求。'''
    content,_,_=_review_content(provider,prompt,[p],'施工图审查')
    text=provider.generate(model=model,input=[{'role':'user','content':content}])
    return _extract_json(text)
