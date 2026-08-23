from __future__ import annotations
import json,re
from pathlib import Path
from collections import defaultdict

BASE_DIR=Path(__file__).resolve().parent
DATA=BASE_DIR/'data'/'theme_router.json'
CODE_RE=re.compile(r'(?:GB(?:/T)?|JGJ(?:/T)?|DBJ/T|DB44/T)\s*\d+(?:\.\d+)?(?:-\d{4})?',re.I)
CLAUSE_RE=re.compile(r'(?<!\d)\d+(?:\.\d+){1,4}(?!\d)')

RISK_VALUE={'低':1,'中':2,'高':3,'极高':4}

def load_router():
    return json.loads(DATA.read_text(encoding='utf-8'))

def normalize(text:str, synonyms:dict|None=None):
    t=(text or '').strip().lower()
    t=t.replace('（','(').replace('）',')').replace('／','/')
    if synonyms:
        # 长词优先，保留原词同时附加标准词，避免替换损失语义
        extra=[]
        for a,b in sorted(synonyms.items(),key=lambda x:-len(x[0])):
            if a.lower() in t:
                extra.append(b.lower())
        if extra:
            t += ' ' + ' '.join(extra)
    return re.sub(r'\s+',' ',t)

def detect_intent(text:str):
    t=text or ''
    if CODE_RE.search(t) and CLAUSE_RE.search(t): return '条文核验'
    if any(k in t for k in ['现行吗','废止','替代','最新版','还能用吗','新规范','旧规范']): return '规范状态核验'
    if any(k in t for k in ['验收','允许偏差','合格','检查方法','检验批']): return '质量验收'
    if any(k in t for k in ['是否可以','能不能','可不可以','符合规范','违规','合规']): return '合规判断'
    if any(k in t for k in ['怎么做','如何施工','施工做法','工艺']): return '施工技术'
    if any(k in t for k in ['整改','怎么处理','修复','解决']): return '整改建议'
    return '规范问答'

def theme_score(theme:dict, q:str):
    score=0.0; hits=[]
    # 关键短语权重大于单触发词
    for p in theme.get('phrases',[]):
        if p.lower() in q:
            score += 8 + min(len(p),8)*0.15; hits.append(p)
    for k in theme.get('triggers',[]):
        if k.lower() in q:
            score += 3 + min(len(k),8)*0.08; hits.append(k)
    for x in theme.get('exclude',[]):
        if x.lower() in q:
            score -= 7
    # 高优先主题微调，但不能凭空命中
    if score>0:
        score += max(0,theme.get('priority',5)-5)*0.35
    return score, hits

def _legacy_route_question(question:str, top_themes:int=5, top_standards:int=7):
    pack=load_router(); q=normalize(question,pack.get('synonyms',{}))
    intent=detect_intent(q)
    explicit_codes=[re.sub(r'\s+',' ',x.upper()).strip() for x in CODE_RE.findall(question or '')]
    scored=[]
    for th in pack['themes']:
        s,h=theme_score(th,q)
        if s>=3:
            scored.append((s,th,h))
    scored.sort(key=lambda x:(-x[0],-x[1].get('priority',5),x[1]['id']))
    selected=scored[:top_themes]
    top_score=selected[0][0] if selected else 0.0
    second_score=selected[1][0] if len(selected)>1 else 0.0
    margin=top_score-second_score
    if explicit_codes and intent in ('条文核验','规范状态核验'):
        confidence='高'
    elif top_score>=13 or (top_score>=9 and margin>=3):
        confidence='高'
    elif top_score>=5:
        confidence='中'
    else:
        confidence='低'

    std=defaultdict(lambda:{'score':0.0,'role':set(),'themes':[]})
    for rank,(s,th,hits) in enumerate(selected):
        decay=max(0.58,1-rank*0.1)
        for code in th.get('primary_codes',[]):
            std[code]['score'] += s*1.35*decay + 7
            std[code]['role'].add('主规范'); std[code]['themes'].append(th['theme'])
        for code in th.get('secondary_codes',[]):
            std[code]['score'] += s*0.75*decay + 2.5
            std[code]['role'].add('配套规范'); std[code]['themes'].append(th['theme'])
    for code in explicit_codes:
        std[code]['score'] += 50
        std[code]['role'].add('用户点名')
    std_list=[]
    for code,v in std.items():
        std_list.append({'code':code,'score':round(v['score'],2),'role':'/'.join(sorted(v['role'])),'themes':list(dict.fromkeys(v['themes']))[:4]})
    std_list.sort(key=lambda x:-x['score'])
    std_list=std_list[:top_standards]

    risk='低'
    ctx=[]; expansion=[]
    for _,th,_ in selected:
        if RISK_VALUE.get(th.get('risk','中'),2)>RISK_VALUE.get(risk,1): risk=th.get('risk','中')
        ctx += th.get('required_context',[])
        expansion += th.get('query_expansion',[])
    # 高风险专业统一提醒，不等于用户必须补充后才回答
    ctx=list(dict.fromkeys(ctx))[:8]
    expansion=list(dict.fromkeys(expansion))[:16]
    # ‘主规范候选’只取强命中主题，避免问题中的材料词把无关专业抬成主规范。
    strong_theme_names=set()
    if selected:
        threshold=max(5.0, top_score*0.55)
        strong_theme_names={th['theme'] for s,th,_ in selected if s>=threshold}
    primary=[]
    for x in std_list:
        if '用户点名' in x['role'] or ('主规范' in x['role'] and any(t in strong_theme_names for t in x.get('themes',[]))):
            if x['code'] not in primary: primary.append(x['code'])
    if not primary and selected:
        primary=list(dict.fromkeys(selected[0][1].get('primary_codes',[])))[:4]
    primary=primary[:4]
    secondary=[x['code'] for x in std_list if x['code'] not in primary][:4]
    return {
        'question':question,'normalized':q,'intent':intent,'risk':risk,'confidence':confidence,'top_score':round(top_score,2),
        'themes':[{'id':th['id'],'theme':th['theme'],'category':th['category'],'score':round(s,2),'hits':hits,'risk':th['risk']} for s,th,hits in selected],
        'standards':std_list,'primary_codes':primary,'secondary_codes':secondary,
        'required_context':ctx,'query_expansion':expansion,
        'explicit_codes':explicit_codes,
        'router_is_evidence':False,
    }

def route_question(question:str, top_themes:int=5, top_standards:int=7, project_context=None):
    """Compatibility facade for the V1.2 data-driven Topic Router."""
    from routing.topic_router import TopicRouter
    modern_router=TopicRouter();modern=modern_router.route(question,project_context)
    legacy=_legacy_route_question(question,top_themes,top_standards)
    eligible_modern=[item for item in modern.topics if item.confidence!='low']
    modern_themes=[]
    for item in eligible_modern:
        modern_themes.append({
            'id':item.topic_id,'theme':item.topic,'category':item.profession,'subtopics':item.subtopics,
            'score':item.numeric_score,'confidence':item.confidence,'hits':item.matched_terms,'risk':'中',
            'normalized_terms':[{'original':x.original,'normalized':x.normalized} for x in item.normalized_terms],
        })
    seen={x['theme'] for x in modern_themes}
    supplementary=[x for x in legacy.get('themes',[]) if x.get('theme') not in seen and (x.get('score',0)>=5 or not modern_themes)]
    themes=(modern_themes+supplementary)[:top_themes]
    modern_primary=list(dict.fromkeys(x for item in eligible_modern for x in (item.governing_standards+item.primary_standards)))
    modern_companion=list(dict.fromkeys(x for item in eligible_modern for x in item.companion_standards))
    use_legacy_codes=not eligible_modern and not modern.explicit.explicit_standard
    legacy_primary=legacy.get('primary_codes',[]) if use_legacy_codes else []
    legacy_secondary=legacy.get('secondary_codes',[]) if use_legacy_codes else []
    combined_codes=modern.preferred_standard_codes+legacy_primary+legacy_secondary
    allowed,warnings,statuses=modern_router.policy.validate(combined_codes,modern.explicit.standard_code,modern.explicit.clause_no)
    status_keys={(x.get('code'),x.get('status')) for x in modern.standard_statuses}
    statuses=modern.standard_statuses+[x for x in statuses if (x.get('code'),x.get('status')) not in status_keys]
    explicit_codes=[modern.explicit.standard_code] if modern.explicit.explicit_standard else []
    preferred_primary,_w,_s=modern_router.policy.validate(explicit_codes+modern_primary+legacy_primary,modern.explicit.standard_code,modern.explicit.clause_no)
    primary=[x for x in preferred_primary if x in allowed][:8]
    preferred_companion,_w,_s=modern_router.policy.validate(modern_companion+legacy_secondary,modern.explicit.standard_code,modern.explicit.clause_no)
    secondary=[x for x in preferred_companion if x in allowed and x not in primary][:8]
    if not primary:primary=allowed[:8]
    standards=[]
    for code in primary+secondary:
        role='用户点名' if code in explicit_codes else ('主规范' if code in primary else '配套规范')
        standards.append({'code':code,'score':100 if role=='用户点名' else 50,'role':role,'themes':[x['theme'] for x in themes[:3]]})
    result=dict(legacy)
    result.update({
        'themes':themes,'standards':standards,'primary_codes':primary,'secondary_codes':secondary,
        'query_expansion':list(dict.fromkeys(modern.expanded_query_terms+legacy.get('query_expansion',[])))[:16],
        'explicit_codes':explicit_codes,'explicit_standard':modern.explicit.explicit_standard,
        'explicit_clause':modern.explicit.explicit_clause,'explicit_clause_no':modern.explicit.clause_no,
        'explicit_clause_blocked':any(x.get('status')=='条文失效' for x in statuses),
        'warnings':list(dict.fromkeys(modern.warnings+warnings)),'standard_statuses':statuses,
        'project_stage':modern.project_stage,'project_stage_confidence':modern.project_stage_confidence,
        'project_stage_candidates':[{'value':x.value,'confidence':x.confidence,'score':x.numeric_score,'matched_terms':x.matched_terms} for x in modern.project_stage_candidates],
        'user_role':modern.user_role,'user_role_confidence':modern.user_role_confidence,
        'user_role_candidates':[{'value':x.value,'confidence':x.confidence,'score':x.numeric_score,'matched_terms':x.matched_terms} for x in modern.user_role_candidates],
        'claims':[{'claim_type':x.claim_type,'claim_text':x.claim_text,'claim_value':x.claim_value,'claim_unit':x.claim_unit,'verification_status':x.verification_status} for x in modern.claims],
        'normative_authority':[{'code':x,'authority':modern_router.policy.normative_authority(x)} for x in allowed],'project_binding':modern.project_binding,
        'filtered_standards':[x for x in statuses if not x.get('allowed')],
        'deprecated_standards':[x for x in statuses if not x.get('allowed') and any(k in x.get('status','') for k in ('废止','被替代','条文失效'))],
        'conflicts':modern.conflicts,'evidence_gate':modern.evidence_gate,
        'topic_router_version':modern_router.pack['router_version'],'router_is_evidence':False,
    })
    if eligible_modern:
        result['top_score']=eligible_modern[0].numeric_score
        result['confidence']={'high':'高','medium':'中','low':'低'}.get(eligible_modern[0].confidence,'低')
    return result

def build_route_query(question:str, route:dict):
    terms=[question]
    terms += route.get('query_expansion',[])[:8]
    return ' '.join(x for x in terms if x)


def build_engineering_knowledge(question:str, project_context=None):
    """Optional V1.2-C0 facade; existing route_question API remains unchanged."""
    from engineering_knowledge.layer import build_knowledge_package
    return build_knowledge_package(question, project_context=project_context)

def route_summary(route:dict):
    themes='、'.join(x['theme'] for x in route.get('themes',[])[:4]) or '未明确识别'
    prim='、'.join(route.get('primary_codes',[])) or '暂无'
    sec='、'.join(route.get('secondary_codes',[])) or '暂无'
    sub=list(dict.fromkeys(s for x in route.get('themes',[]) for s in x.get('subtopics',[])))[:4]
    subtext=('；子主题：'+'、'.join(sub)) if sub else ''
    stage_labels={'pre_construction':'施工准备','construction':'施工过程','acceptance':'验收','maintenance':'维修','renovation':'改造','unknown':'未识别'}
    role_labels={'construction':'施工','designer':'设计','supervision':'监理','owner':'甲方/业主','cost':'造价','general':'普通用户','unknown':'未识别'}
    stage=stage_labels.get(route.get('project_stage','unknown'),route.get('project_stage','未识别'))
    role=role_labels.get(route.get('user_role','unknown'),route.get('user_role','未识别'))
    evidence='待检索条文证据' if route.get('evidence_gate')=='requires_clause_evidence' else route.get('evidence_gate','待核验')
    warning=('；警告：'+'；'.join(route.get('warnings',[])[:2])) if route.get('warnings') else ''
    return f"意图：{route['intent']}；风险：{route['risk']}；路由置信度：{route.get('confidence','-')}；工程阶段：{stage}；用户角色：{role}；主题：{themes}{subtext}；主规范候选：{prim}；配套规范候选：{sec}；证据状态：{evidence}{warning}。"


def log_route(route:dict):
    """本地记录路由结果，便于发现低置信度现场说法。不会自动改变规范映射。"""
    try:
        from db import connect
        with connect() as con:
            con.execute("""CREATE TABLE IF NOT EXISTS route_logs(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                question TEXT,
                intent TEXT,
                risk TEXT,
                confidence TEXT,
                top_theme TEXT,
                primary_codes TEXT,
                route_json TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )""")
            top=(route.get('themes') or [{}])[0].get('theme','') if route.get('themes') else ''
            con.execute("""INSERT INTO route_logs(question,intent,risk,confidence,top_theme,primary_codes,route_json)
                           VALUES(?,?,?,?,?,?,?)""",
                        (route.get('question',''),route.get('intent',''),route.get('risk',''),route.get('confidence',''),
                         top,'；'.join(route.get('primary_codes',[])),json.dumps(route,ensure_ascii=False)))
    except Exception:
        pass
