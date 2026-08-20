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

def route_question(question:str, top_themes:int=5, top_standards:int=7):
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

def build_route_query(question:str, route:dict):
    terms=[question]
    terms += route.get('query_expansion',[])[:8]
    return ' '.join(x for x in terms if x)

def route_summary(route:dict):
    themes='、'.join(x['theme'] for x in route.get('themes',[])[:4]) or '未明确识别'
    prim='、'.join(route.get('primary_codes',[])) or '暂无'
    sec='、'.join(route.get('secondary_codes',[])) or '暂无'
    return f"意图：{route['intent']}；风险：{route['risk']}；路由置信度：{route.get('confidence','-')}；主题：{themes}；主规范候选：{prim}；配套规范候选：{sec}。"


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
