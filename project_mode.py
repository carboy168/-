from __future__ import annotations
import json, re
from datetime import date, datetime
from pathlib import Path
from typing import Any
from db import connect

BASE_DIR = Path(__file__).resolve().parent
OVERLAY_FILE = BASE_DIR / 'data' / 'guangdong_overlay.json'

PROJECT_SCOPES = [
    '装饰装修','消防','给排水','电气','暖通空调','防水','脚手架/高处作业',
    '临时用电','结构改造/加固','安防/智能化','无障碍','绿色施工','绿色建筑','燃气'
]

PROJECT_DOC_TYPES = [
    '设计图纸/设计说明','图纸会审/设计回复','施工图审查意见','消防设计审查/验收文件',
    '设计变更/技术核定','甲方技术要求','监理/主管部门要求','经审批专项施工方案','合同技术条款','其他'
]

BUILDING_TYPES = ['公共建筑','住宅','厂房/工业建筑','商业','办公','学校/教育','医疗','酒店','市政/室外','其他']
PROJECT_NATURES = ['新建','室内装修','既有建筑改造','改扩建','结构加固','修缮维护','其他']
PROJECT_PHASES = ['方案/设计','施工图/图审','招投标','施工准备','施工','专项验收','竣工验收','运维/维修']

FLAG_LABELS = {
    'existing_building':'既有建筑',
    'structural_change':'涉及结构拆改/加固',
    'fire_change':'涉及消防系统或防火改造',
    'suspended_scaffold':'使用悬挑式脚手架',
    'work_platform':'使用装修操作平台/脚手架',
    'post_anchor':'涉及植筋/后置锚固',
    'basket':'使用高处作业吊篮',
    'green_building_eval':'涉及绿色建筑评价',
    'green_construction_eval':'涉及绿色施工评价',
    'gas_system':'涉及燃气系统',
}


def ensure_project_schema():
    with connect() as con:
        con.executescript('''
        CREATE TABLE IF NOT EXISTS projects(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            province TEXT DEFAULT '广东省',
            city TEXT DEFAULT '',
            district TEXT DEFAULT '',
            building_type TEXT DEFAULT '',
            project_nature TEXT DEFAULT '',
            usage TEXT DEFAULT '',
            phase TEXT DEFAULT '施工',
            scopes TEXT DEFAULT '[]',
            reference_date TEXT DEFAULT '',
            existing_building INTEGER DEFAULT 0,
            structural_change INTEGER DEFAULT 0,
            fire_change INTEGER DEFAULT 0,
            suspended_scaffold INTEGER DEFAULT 0,
            work_platform INTEGER DEFAULT 0,
            post_anchor INTEGER DEFAULT 0,
            basket INTEGER DEFAULT 0,
            green_building_eval INTEGER DEFAULT 0,
            green_construction_eval INTEGER DEFAULT 0,
            gas_system INTEGER DEFAULT 0,
            design_notes TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS project_requirements(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            doc_type TEXT DEFAULT '',
            title TEXT NOT NULL,
            requirement_text TEXT DEFAULT '',
            source_ref TEXT DEFAULT '',
            status TEXT DEFAULT '有效',
            priority INTEGER DEFAULT 50,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS app_settings(
            key TEXT PRIMARY KEY,
            value TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS project_query_logs(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER,
            question TEXT,
            route_json TEXT,
            overlay_json TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE SET NULL
        );
        CREATE TABLE IF NOT EXISTS standard_aliases(
            alias_code TEXT PRIMARY KEY,
            canonical_code TEXT NOT NULL,
            note TEXT DEFAULT ''
        );
        ''')


def _json_load(s, fallback):
    try:
        return json.loads(s) if s else fallback
    except Exception:
        return fallback


def _project_dict(row):
    if not row:
        return None
    d = dict(row)
    d['scopes'] = _json_load(d.get('scopes'), [])
    for k in FLAG_LABELS:
        d[k] = bool(d.get(k, 0))
    return d


def save_project(data: dict, project_id: int|None=None) -> int:
    ensure_project_schema()
    scopes = data.get('scopes', [])
    if isinstance(scopes, str):
        scopes = [x.strip() for x in re.split(r'[；;,，]+', scopes) if x.strip()]
    fields = [
        'name','province','city','district','building_type','project_nature','usage','phase','scopes','reference_date',
        'existing_building','structural_change','fire_change','suspended_scaffold','work_platform','post_anchor','basket',
        'green_building_eval','green_construction_eval','gas_system','design_notes','notes'
    ]
    values = []
    for f in fields:
        if f == 'scopes':
            values.append(json.dumps(scopes, ensure_ascii=False))
        elif f in FLAG_LABELS:
            values.append(1 if data.get(f) else 0)
        else:
            values.append(data.get(f, ''))
    with connect() as con:
        if project_id:
            assigns = ','.join(f'{f}=?' for f in fields)
            con.execute(f'UPDATE projects SET {assigns}, updated_at=CURRENT_TIMESTAMP WHERE id=?', values+[project_id])
            return project_id
        cur = con.execute(
            f"INSERT INTO projects ({','.join(fields)}) VALUES ({','.join(['?']*len(fields))})", values
        )
        return cur.lastrowid


def list_projects():
    ensure_project_schema()
    with connect() as con:
        return [_project_dict(r) for r in con.execute('SELECT * FROM projects ORDER BY updated_at DESC,id DESC')]


def get_project(project_id: int|None):
    if not project_id:
        return None
    ensure_project_schema()
    with connect() as con:
        return _project_dict(con.execute('SELECT * FROM projects WHERE id=?',(project_id,)).fetchone())


def set_active_project(project_id: int|None):
    ensure_project_schema()
    with connect() as con:
        con.execute("INSERT INTO app_settings(key,value) VALUES('active_project_id',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (str(project_id or ''),))


def get_active_project():
    ensure_project_schema()
    with connect() as con:
        r = con.execute("SELECT value FROM app_settings WHERE key='active_project_id'").fetchone()
    if not r or not r['value']:
        return None
    try:
        return get_project(int(r['value']))
    except Exception:
        return None


def add_project_requirement(project_id:int, doc_type:str, title:str, requirement_text:str='', source_ref:str='', status:str='有效', priority:int=50):
    ensure_project_schema()
    with connect() as con:
        cur=con.execute('''INSERT INTO project_requirements(project_id,doc_type,title,requirement_text,source_ref,status,priority)
                           VALUES(?,?,?,?,?,?,?)''',
                        (project_id,doc_type,title,requirement_text,source_ref,status,priority))
        return cur.lastrowid


def delete_project_requirement(req_id:int):
    with connect() as con:
        con.execute('DELETE FROM project_requirements WHERE id=?',(req_id,))


def list_project_requirements(project_id:int, active_only=True):
    ensure_project_schema()
    sql='SELECT * FROM project_requirements WHERE project_id=?'
    params=[project_id]
    if active_only:
        sql += " AND status='有效'"
    sql += ' ORDER BY priority DESC,id DESC'
    with connect() as con:
        return [dict(r) for r in con.execute(sql,params)]


def is_guangdong(project:dict|None):
    if not project:
        return False
    blob=' '.join(str(project.get(k,'')) for k in ('province','city','district'))
    return any(k in blob for k in ('广东','广州','深圳','珠海','汕头','佛山','韶关','河源','梅州','惠州','汕尾','东莞','中山','江门','阳江','湛江','茂名','肇庆','清远','潮州','揭阳','云浮'))


def _date(s, default=None):
    if isinstance(s,date): return s
    if not s: return default
    try: return datetime.strptime(str(s)[:10],'%Y-%m-%d').date()
    except Exception: return default


def project_reference_date(project:dict|None):
    return _date((project or {}).get('reference_date'), date.today())


def project_context_text(project:dict|None):
    if not project:
        return '当前未启用项目模式。'
    flags=[label for key,label in FLAG_LABELS.items() if project.get(key)]
    reqs=list_project_requirements(project['id']) if project.get('id') else []
    req_text='；'.join(f"{x['doc_type']}：{x['title']}" for x in reqs[:8]) or '暂无登记'
    return (
        f"项目：{project.get('name','')}；地区：{project.get('province','')}{project.get('city','')}{project.get('district','')}；"
        f"建筑类型：{project.get('building_type','')}；项目性质：{project.get('project_nature','')}；用途：{project.get('usage','')}；"
        f"阶段：{project.get('phase','')}；专业范围：{'、'.join(project.get('scopes',[])) or '未登记'}；"
        f"规范适用基准日：{project_reference_date(project).isoformat()}；特殊标签：{'、'.join(flags) or '无'}；"
        f"项目特定文件：{req_text}。"
    )


def load_guangdong_overlay():
    if not OVERLAY_FILE.exists():
        return {'standards':[]}
    return json.loads(OVERLAY_FILE.read_text(encoding='utf-8'))


def _active_on(rule, ref_date:date):
    start=_date(rule.get('effective_from'))
    end=_date(rule.get('effective_to'))
    if start and ref_date < start: return False
    if end and ref_date > end: return False
    return True


def _upcoming_on(rule, ref_date:date):
    start=_date(rule.get('effective_from'))
    return bool(start and ref_date < start)


def _score_rule(rule:dict, question:str, route:dict, project:dict):
    q=(question or '').lower()
    score=0; hits=[]
    route_themes=[x.get('theme','') for x in route.get('themes',[])]
    route_cats=[x.get('category','') for x in route.get('themes',[])]
    scopes=project.get('scopes',[]) or []
    explicit_question_hits=[]
    for w in rule.get('question_terms',[]):
        if w.lower() in q:
            score+=8; hits.append(w); explicit_question_hits.append(w)
    for t in rule.get('theme_terms',[]):
        if any(t in x for x in route_themes): score+=6; hits.append('主题:'+t)
    for c in rule.get('categories',[]):
        if c in route_cats: score+=4; hits.append('专业:'+c)
    for s in rule.get('scopes',[]):
        if s in scopes: score+=2.5; hits.append('范围:'+s)
    for n in rule.get('project_natures',[]):
        if n == project.get('project_nature'): score+=2.5; hits.append('性质:'+n)
    for b in rule.get('building_types',[]):
        if b == project.get('building_type'): score+=1.5; hits.append('建筑:'+b)
    for f in rule.get('flags_any',[]):
        if project.get(f): score+=6; hits.append('项目:'+FLAG_LABELS.get(f,f))
    # 特定标准如果要求强触发，则仅有“项目范围”不足以叠加
    if rule.get('require_strong_trigger'):
        # 专门性地方标准必须由用户问题本身命中该标准的专门场景；
        # 项目标签、专业范围或宽泛主题不能单独触发，避免“广东项目就把所有省标都叠上去”。
        if not explicit_question_hits:
            score=0
    return score, hits


def _project_national_context(question:str, route:dict, project:dict):
    q=(question or '')
    cats=[x.get('category','') for x in route.get('themes',[])]
    out=[]
    def add(code,reason):
        if code not in [x['code'] for x in out]: out.append({'code':code,'reason':reason})
    structural = any(k in q for k in ['结构','承重','拆墙','拆梁','楼板','加固','植筋','锚固','裂缝']) or any('既有建筑' in c for c in cats)
    renovation_related = structural or any(k in q for k in ['既有','旧楼','改造','改扩建','拆除','拆一面','拆墙','功能改变'])
    fire = any(k in q for k in ['消防','防火','喷淋','报警','疏散','防火门','排烟','灭火器']) or any('消防' in c for c in cats)
    if project.get('existing_building') and renovation_related:
        add('GB 55022-2021','既有建筑维护与改造背景')
    if project.get('structural_change') and structural:
        add('GB 55021-2021','项目涉及结构拆改/加固')
        add('GB 55001-2021','结构安全上位控制')
    if project.get('fire_change') and fire:
        add('GB 55036-2022','项目涉及消防设施改造')
        add('GB 55037-2022','项目涉及建筑防火要求')
    return out


def build_project_overlay(question:str, route:dict, project:dict|None):
    result={
        'enabled':bool(project), 'is_guangdong':False, 'reference_date':'',
        'national_context':[], 'local_applicable':[], 'local_upcoming':[], 'local_pending':[],
        'candidate_codes':[], 'project_context':'当前未启用项目模式。', 'project_requirements':[]
    }
    if not project:
        return result
    ref=project_reference_date(project)
    result['reference_date']=ref.isoformat()
    result['project_context']=project_context_text(project)
    result['project_requirements']=list_project_requirements(project['id']) if project.get('id') else []
    result['national_context']=_project_national_context(question,route,project)
    result['is_guangdong']=is_guangdong(project)
    if not result['is_guangdong']:
        result['candidate_codes']=[x['code'] for x in result['national_context']]
        return result
    pack=load_guangdong_overlay()
    for rule in pack.get('standards',[]):
        score,hits=_score_rule(rule,question,route,project)
        if score < rule.get('min_score',6):
            continue
        item={
            'code':rule['code'],'title':rule['title'],'grade':rule.get('grade','B'),'score':round(score,1),
            'hits':hits,'effective_from':rule.get('effective_from',''),'effective_to':rule.get('effective_to',''),
            'status':rule.get('status',''),'source_url':rule.get('source_url',''),'note':rule.get('note','')
        }
        if rule.get('grade')!='A':
            result['local_pending'].append(item)
        elif _upcoming_on(rule,ref):
            result['local_upcoming'].append(item)
        elif _active_on(rule,ref):
            result['local_applicable'].append(item)
    for k in ('local_applicable','local_upcoming','local_pending'):
        result[k].sort(key=lambda x:(-x['score'],x['code']))
    result['candidate_codes']=list(dict.fromkeys(
        [x['code'] for x in result['local_applicable']] + [x['code'] for x in result['national_context']]
    ))
    return result


def overlay_summary(overlay:dict):
    if not overlay or not overlay.get('enabled'):
        return '未启用项目模式。'
    parts=[f"项目地区叠加：{'广东省' if overlay.get('is_guangdong') else '非广东/未识别'}；基准日：{overlay.get('reference_date','-')}"]
    if overlay.get('local_applicable'):
        parts.append('当前适用地方规范候选：'+'、'.join(x['code'] for x in overlay['local_applicable']))
    if overlay.get('local_upcoming'):
        parts.append('即将实施（当前不作为现行依据）：'+'、'.join(x['code'] for x in overlay['local_upcoming']))
    if overlay.get('local_pending'):
        parts.append('待核验地方标准：'+'、'.join(x['code'] for x in overlay['local_pending']))
    if overlay.get('national_context'):
        parts.append('项目背景补充候选：'+'、'.join(x['code'] for x in overlay['national_context']))
    return '；'.join(parts)+'。'


def normalize_standard_code(code:str):
    return re.sub(r'\s+','',(code or '').upper())

def resolve_standard_alias(code:str):
    ensure_project_schema()
    n=normalize_standard_code(code)
    with connect() as con:
        rows=con.execute('SELECT alias_code,canonical_code FROM standard_aliases').fetchall()
    for r in rows:
        if normalize_standard_code(r['alias_code'])==n:
            return r['canonical_code']
    return code

def seed_standard_aliases():
    ensure_project_schema()
    aliases=[
        ('DB44/T 2827-2026','DBJ/T 15-291-2026','同一广东地方标准的市场监管编号/工程建设标准编号'),
        ('DB44/T 2828-2026','DBJ/T 15-292-2026','同一广东地方标准的市场监管编号/工程建设标准编号'),
        ('DB44/T 2829-2026','DBJ/T 15-295-2026','同一广东地方标准的市场监管编号/工程建设标准编号'),
    ]
    with connect() as con:
        for a,c,note in aliases:
            con.execute('INSERT INTO standard_aliases(alias_code,canonical_code,note) VALUES(?,?,?) ON CONFLICT(alias_code) DO UPDATE SET canonical_code=excluded.canonical_code,note=excluded.note',(a,c,note))


def log_project_query(project_id:int|None, question:str, route:dict, overlay:dict):
    try:
        ensure_project_schema()
        with connect() as con:
            con.execute('INSERT INTO project_query_logs(project_id,question,route_json,overlay_json) VALUES(?,?,?,?)',
                        (project_id,question,json.dumps(route,ensure_ascii=False),json.dumps(overlay,ensure_ascii=False)))
    except Exception:
        pass


def update_project_requirement_status(req_id:int, status:str):
    ensure_project_schema()
    if status not in ('有效','待确认','作废'):
        raise ValueError('无效状态')
    with connect() as con:
        con.execute('UPDATE project_requirements SET status=? WHERE id=?',(status,req_id))
