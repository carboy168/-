from __future__ import annotations
import base64, json, mimetypes, os, re
from pathlib import Path
from config_env import load_dotenv
from db import search_clauses_v3
from router import route_question, build_route_query
from project_mode import build_project_overlay, project_context_text, list_project_requirements, resolve_standard_alias
from project_kb import search_project_chunks, build_project_evidence, save_review, DIRECT_REVIEW_EXTS
from provider import get_provider

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


def _norm_evidence(project:dict, review_type:str, scope:str, max_rows:int=28):
    checks=DRAWING_CHECKS if review_type=='施工图审查' else PLAN_CHECKS
    rows=[];seen=set()
    for _,q in checks:
        question=f'{scope} {q}'.strip();route=route_question(question);overlay=build_project_overlay(question,route,project)
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


def run_review(project:dict,file_paths:list[str],review_type:str,scope:str='',title:str=''):
    if not project:raise ValueError('必须先启用项目模式。')
    if review_type not in REVIEW_TYPES:raise ValueError('未知审查类型。')
    paths=[Path(x) for x in file_paths if Path(x).exists()]
    if not paths:raise ValueError('没有可审查文件。')
    total=sum(p.stat().st_size for p in paths)
    if total>MAX_REQUEST_BYTES:raise ValueError('本次文件合计超过48MB。OpenAI文件输入单请求总上限为50MB，V1.0预留安全余量，请拆分审查。')
    for p in paths:
        if p.suffix.lower() not in DIRECT_REVIEW_EXTS:raise ValueError(f'暂不支持直接审查：{p.suffix}。CAD请导出PDF。')
    key=os.getenv('OPENAI_API_KEY','').strip();model=os.getenv('OPENAI_REVIEW_MODEL','').strip() or os.getenv('OPENAI_MODEL','').strip()
    if not key or not model:raise ValueError('请先在桌面版“设置”中配置OpenAI API Key和模型。')

    norms=_norm_evidence(project,review_type,scope)
    p_rows=search_project_chunks(project['id'],scope or ('图纸 设计 变更 审图 消防 要求' if review_type=='施工图审查' else '施工方案 技术要求 工艺 验收 安全 责任'),limit=16)
    reqs=list_project_requirements(project['id'])[:15]
    norm_text=_format_norm(norms) or '（当前规范全文库没有检索到可引用条文）'
    project_text=build_project_evidence(p_rows) or '（当前未检索到相关项目文件文本证据）'
    req_text='\n'.join(f"【R{i}】{r['doc_type']}｜{r['title']}｜{r['source_ref']}\n{r['requirement_text']}" for i,r in enumerate(reqs,start=1)) or '（当前没有已确认项目控制条件）'
    checks=DRAWING_CHECKS if review_type=='施工图审查' else PLAN_CHECKS
    checklist='\n'.join(f'- {cat}：{q}' for cat,q in checks)
    prompt=f'''你是工程图纸与施工方案审查助手。请审查用户提交的文件，但必须严格区分“规范证据、项目文件证据、工程判断”。\n\n当前项目：\n{project_context_text(project)}\n\n审查类型：{review_type}\n审查范围/重点：{scope or '按V1.0默认清单全面审查'}\n\n默认审查清单：\n{checklist}\n\n【唯一允许作为规范依据的本地规范证据】\n{norm_text}\n\n【项目文件检索证据】\n{project_text}\n\n【项目已确认控制条件】\n{req_text}\n\n严格规则：\n1. 可以从提交文件本身发现问题，但不得凭模型记忆编造规范编号或条文号。\n2. norm_refs只能填写上面存在的N编号；没有对应N证据时规范依据必须留空。\n3. project_refs只能填写上面存在的P或R编号。\n4. 图纸PDF请结合页面图像和文字审查；看不清尺寸/图号不得猜。\n5. DOCX/PPTX等非PDF文件的嵌入图片/图表可能未完整呈现，依赖图形的信息标“需核对”。\n6. 区分确定问题、需核对、建议优化、符合项，不要为了数量制造问题。\n7. 高风险优先：结构拆改、消防疏散、防火分隔、脚手架/高处作业、临时用电、防水渗漏、主要机电安全。\n8. 项目文件不得降低强制性要求；疑似冲突时提出确认。\n9. 只返回有效JSON，不要Markdown。\n\nJSON格式：{{"summary":"总评","findings":[{{"severity":"高|中|低|提示","category":"专业","location":"页码/图号/章节","issue":"问题","norm_refs":["N1"],"project_refs":["P1","R1"],"recommendation":"建议","evidence_grade":"A|B|C|D","confidence":"高|中|低","finding_type":"确定问题|需核对|建议优化|符合项","status":"待确认|待整改|提示","notes":"说明"}}]}}'''
    content=[{'type':'input_text','text':prompt}]
    for p in paths:
        ext=p.suffix.lower();url=_data_url(p)
        if ext in ('.png','.jpg','.jpeg','.webp'):
            content.append({'type':'input_image','image_url':url,'detail':'high'})
        else:
            item={'type':'input_file','filename':p.name,'file_data':url}
            if ext=='.pdf':item['detail']='high' if review_type=='施工图审查' else 'auto'
            content.append(item)
    text=get_provider(api_key=key).generate(model=model,input=[{'role':'user','content':content}])
    result=validate_review_result(_extract_json(text),len(norms),len(p_rows),len(reqs))
    result['meta']={'review_type':review_type,'model':model,'norm_evidence_count':len(norms),'project_evidence_count':len(p_rows),'project_requirement_count':len(reqs),'files':[p.name for p in paths]}
    result['review_id']=save_review(project['id'],review_type,title or f'{review_type}-{paths[0].stem}',scope,model,[p.name for p in paths],result)
    return result


def extract_control_candidates(project:dict,file_path:str,doc_type:str,title:str):
    p=Path(file_path)
    if p.stat().st_size>MAX_REQUEST_BYTES:raise ValueError('文件超过V1.0单次48MB安全上限。')
    key=os.getenv('OPENAI_API_KEY','').strip();model=os.getenv('OPENAI_REVIEW_MODEL','').strip() or os.getenv('OPENAI_MODEL','').strip()
    if not key or not model:raise ValueError('请先在桌面版“设置”中配置OpenAI API。')
    prompt=f'''从该项目文件中提取会影响施工做法、材料选型、验收、责任界面、移交条件、工期或报审的明确控制条件。项目：{project_context_text(project)}。文件类型：{doc_type}，文件名：{title}。只返回JSON：{{"items":[{{"title":"短标题","requirement_text":"明确控制要求","source_ref":"页码/章节/图号，无法识别则待定位","priority":50}}]}}。不要把建议当成文件明确要求。'''
    content=[{'type':'input_text','text':prompt}];url=_data_url(p);ext=p.suffix.lower()
    if ext in ('.png','.jpg','.jpeg','.webp'):content.append({'type':'input_image','image_url':url,'detail':'high'})
    else:
        item={'type':'input_file','filename':p.name,'file_data':url}
        if ext=='.pdf':item['detail']='high'
        content.append(item)
    text=get_provider(api_key=key).generate(model=model,input=[{'role':'user','content':content}])
    return _extract_json(text)
