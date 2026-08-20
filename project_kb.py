from __future__ import annotations
import hashlib, json, mimetypes, re, shutil, zipfile, os
import xml.etree.ElementTree as ET
from pathlib import Path
from pypdf import PdfReader
from db import connect
from search_zh import build_index_text, build_query

BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = Path(os.getenv('PROJECT_DATA_DIR', str(BASE_DIR / 'data' / 'projects')))
PROJECT_DIR.mkdir(parents=True, exist_ok=True)

SUPPORTED_INDEX_EXTS = {'.pdf','.docx','.pptx','.txt','.md','.json','.csv','.xlsx','.xml','.html'}
VISUAL_REVIEW_EXTS = {'.pdf','.png','.jpg','.jpeg','.webp'}
DIRECT_REVIEW_EXTS = SUPPORTED_INDEX_EXTS | VISUAL_REVIEW_EXTS | {'.doc','.ppt','.xls','.rtf','.odt'}
UNSUPPORTED_CAD_EXTS = {'.dwg','.dxf','.dgn'}

DOC_TYPES = [
    '施工图/设计图纸','设计说明','图纸会审/设计回复','施工图审查意见','消防设计审查/验收文件',
    '施工组织设计','施工方案','专项施工方案','技术交底','设计变更/技术核定','材料报审/样板确认',
    '合同技术条款','甲方技术要求','监理/主管部门要求','工程联系单/会议纪要','验收资料','其他'
]


def ensure_project_kb_schema():
    with connect() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS project_files(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            doc_type TEXT DEFAULT '',
            title TEXT NOT NULL,
            source_ref TEXT DEFAULT '',
            original_name TEXT NOT NULL,
            stored_path TEXT NOT NULL,
            ext TEXT DEFAULT '',
            mime_type TEXT DEFAULT '',
            sha256 TEXT NOT NULL,
            file_size INTEGER DEFAULT 0,
            page_count INTEGER DEFAULT 0,
            chunk_count INTEGER DEFAULT 0,
            index_status TEXT DEFAULT '未索引',
            visual_review_supported INTEGER DEFAULT 0,
            notes TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
            UNIQUE(project_id, sha256)
        );
        CREATE TABLE IF NOT EXISTS project_file_chunks(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            file_id INTEGER NOT NULL,
            page_no INTEGER,
            section TEXT DEFAULT '',
            content TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
            FOREIGN KEY(file_id) REFERENCES project_files(id) ON DELETE CASCADE,
            UNIQUE(file_id, content_hash)
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS project_file_chunks_fts USING fts5(
            search_tokens, chunk_id UNINDEXED, project_id UNINDEXED, file_id UNINDEXED,
            tokenize='unicode61'
        );
        CREATE TABLE IF NOT EXISTS project_reviews(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            review_type TEXT NOT NULL,
            title TEXT DEFAULT '',
            review_scope TEXT DEFAULT '',
            model TEXT DEFAULT '',
            file_names TEXT DEFAULT '[]',
            summary TEXT DEFAULT '',
            status TEXT DEFAULT '已完成',
            raw_json TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS review_findings(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            review_id INTEGER NOT NULL,
            project_id INTEGER NOT NULL,
            severity TEXT DEFAULT '提示',
            category TEXT DEFAULT '',
            location TEXT DEFAULT '',
            issue TEXT NOT NULL,
            norm_refs TEXT DEFAULT '[]',
            project_refs TEXT DEFAULT '[]',
            recommendation TEXT DEFAULT '',
            evidence_grade TEXT DEFAULT 'D',
            confidence TEXT DEFAULT '中',
            finding_type TEXT DEFAULT '需核对',
            status TEXT DEFAULT '待确认',
            notes TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(review_id) REFERENCES project_reviews(id) ON DELETE CASCADE,
            FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
        );
        """)


def _safe_name(name:str):
    return re.sub(r'[\\/:*?"<>|]+','_',name or 'file')[:120]


def _mime(path:Path):
    return mimetypes.guess_type(path.name)[0] or 'application/octet-stream'


def _chunk_text(text:str, page_no:int|None=None, section:str='', step:int=1200, overlap:int=160):
    clean=re.sub(r'[ \t]+',' ',text or '').strip()
    if not clean: return []
    out=[]; start=0
    while start < len(clean):
        chunk=clean[start:start+step].strip()
        if len(chunk)>=8:
            out.append({'page_no':page_no,'section':section,'content':chunk})
        if start+step >= len(clean): break
        start += max(1, step-overlap)
    return out


def _extract_pdf(path:Path):
    reader=PdfReader(str(path)); chunks=[]
    for i,p in enumerate(reader.pages,start=1):
        chunks.extend(_chunk_text(p.extract_text() or '',page_no=i,section=f'PDF第{i}页'))
    return chunks,len(reader.pages)


def _xml_text(xml_bytes:bytes):
    root=ET.fromstring(xml_bytes); texts=[]
    for e in root.iter():
        tag=e.tag.split('}')[-1]
        if tag in ('t','instrText') and e.text: texts.append(e.text)
        elif tag=='tab': texts.append('\t')
        elif tag in ('br','cr'): texts.append('\n')
    return ' '.join(x for x in texts if x).strip()


def _extract_docx(path:Path):
    with zipfile.ZipFile(path) as z: text=_xml_text(z.read('word/document.xml'))
    return _chunk_text(text,section='Word正文'),1


def _extract_pptx(path:Path):
    chunks=[]
    with zipfile.ZipFile(path) as z:
        names=[n for n in z.namelist() if re.fullmatch(r'ppt/slides/slide\d+\.xml',n)]
        names.sort(key=lambda s:int(re.search(r'(\d+)',Path(s).stem).group(1)))
        for i,n in enumerate(names,start=1):
            chunks.extend(_chunk_text(_xml_text(z.read(n)),page_no=i,section=f'幻灯片{i}'))
    return chunks,len(names)


def _xlsx_shared_strings(z):
    if 'xl/sharedStrings.xml' not in z.namelist(): return []
    root=ET.fromstring(z.read('xl/sharedStrings.xml')); out=[]
    for si in root:
        vals=[e.text for e in si.iter() if e.tag.split('}')[-1]=='t' and e.text]
        out.append(''.join(vals))
    return out


def _extract_xlsx(path:Path):
    chunks=[]
    with zipfile.ZipFile(path) as z:
        shared=_xlsx_shared_strings(z)
        sheets=[n for n in z.namelist() if re.fullmatch(r'xl/worksheets/sheet\d+\.xml',n)]
        sheets.sort(key=lambda s:int(re.search(r'(\d+)',Path(s).stem).group(1)))
        for si,n in enumerate(sheets,start=1):
            root=ET.fromstring(z.read(n)); rows=[]
            for row in root.iter():
                if row.tag.split('}')[-1] != 'row': continue
                vals=[]
                for c in row:
                    if c.tag.split('}')[-1] != 'c': continue
                    typ=c.attrib.get('t',''); v=None
                    for e in c:
                        et=e.tag.split('}')[-1]
                        if et in ('v','t') and e.text is not None: v=e.text; break
                        if et=='is': v=''.join(x.text or '' for x in e.iter() if x.tag.split('}')[-1]=='t')
                    if v is None: continue
                    if typ=='s':
                        try:v=shared[int(v)]
                        except Exception:pass
                    vals.append(str(v))
                if vals: rows.append(' | '.join(vals))
                if len(rows)>=1000: break
            chunks.extend(_chunk_text('\n'.join(rows),section=f'工作表{si}'))
    return chunks,len(sheets)


def extract_file_text(path:str):
    p=Path(path); ext=p.suffix.lower()
    if ext=='.pdf': return _extract_pdf(p)
    if ext=='.docx': return _extract_docx(p)
    if ext=='.pptx': return _extract_pptx(p)
    if ext=='.xlsx': return _extract_xlsx(p)
    if ext in ('.txt','.md','.json','.csv','.xml','.html'):
        return _chunk_text(p.read_text(encoding='utf-8',errors='ignore'),section='文本正文'),1
    return [],0


def ingest_project_file(project_id:int, source_path:str, doc_type:str, title:str='', source_ref:str='', notes:str=''):
    ensure_project_kb_schema(); src=Path(source_path)
    if not src.exists(): raise FileNotFoundError(source_path)
    ext=src.suffix.lower()
    if ext in UNSUPPORTED_CAD_EXTS:
        raise ValueError('V1.0暂不直接解析DWG/DXF/DGN，请从CAD导出为带文字层的PDF后导入；PDF可进行视觉审查。')
    raw=src.read_bytes(); sha=hashlib.sha256(raw).hexdigest(); size=len(raw)
    proj_dir=PROJECT_DIR/str(project_id)/'files'; proj_dir.mkdir(parents=True,exist_ok=True)
    dest=proj_dir/f'{sha[:10]}_{_safe_name(src.name)}'
    if not dest.exists(): shutil.copy2(src,dest)
    chunks,page_count=extract_file_text(str(dest)) if ext in SUPPORTED_INDEX_EXTS else ([],0)
    index_status='已索引' if chunks else ('仅视觉/AI直读' if ext in VISUAL_REVIEW_EXTS else '未建立本地文本索引')
    with connect() as con:
        old=con.execute('SELECT id FROM project_files WHERE project_id=? AND sha256=?',(project_id,sha)).fetchone()
        if old:
            file_id=old['id']
            con.execute('UPDATE project_files SET doc_type=?,title=?,source_ref=?,notes=?,stored_path=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',
                        (doc_type,title or src.stem,source_ref,notes,str(dest),file_id))
            ids=[r['id'] for r in con.execute('SELECT id FROM project_file_chunks WHERE file_id=?',(file_id,))]
            if ids:
                marks=','.join(['?']*len(ids)); con.execute(f'DELETE FROM project_file_chunks_fts WHERE chunk_id IN ({marks})',ids)
            con.execute('DELETE FROM project_file_chunks WHERE file_id=?',(file_id,))
        else:
            cur=con.execute('''INSERT INTO project_files(project_id,doc_type,title,source_ref,original_name,stored_path,ext,mime_type,sha256,file_size,page_count,chunk_count,index_status,visual_review_supported,notes)
                               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (project_id,doc_type,title or src.stem,source_ref,src.name,str(dest),ext,_mime(dest),sha,size,page_count,len(chunks),index_status,1 if ext in VISUAL_REVIEW_EXTS else 0,notes))
            file_id=cur.lastrowid
        seen=set(); count=0
        for c in chunks:
            h=hashlib.sha256(c['content'].encode('utf-8')).hexdigest()
            if h in seen: continue
            seen.add(h)
            cur=con.execute('INSERT OR IGNORE INTO project_file_chunks(project_id,file_id,page_no,section,content,content_hash) VALUES(?,?,?,?,?,?)',
                            (project_id,file_id,c.get('page_no'),c.get('section',''),c['content'],h))
            if cur.rowcount:
                cid=cur.lastrowid; count+=1
                con.execute('INSERT INTO project_file_chunks_fts(search_tokens,chunk_id,project_id,file_id) VALUES(?,?,?,?)',
                            (build_index_text('', '', c.get('section',''), c['content']),str(cid),str(project_id),str(file_id)))
        con.execute('UPDATE project_files SET page_count=?,chunk_count=?,index_status=?,visual_review_supported=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',
                    (page_count,count,index_status,1 if ext in VISUAL_REVIEW_EXTS else 0,file_id))
    return get_project_file(file_id)


def get_project_file(file_id:int):
    ensure_project_kb_schema()
    with connect() as con:
        r=con.execute('SELECT * FROM project_files WHERE id=?',(file_id,)).fetchone()
        return dict(r) if r else None


def list_project_files(project_id:int):
    ensure_project_kb_schema()
    with connect() as con:
        return [dict(r) for r in con.execute('SELECT * FROM project_files WHERE project_id=? ORDER BY updated_at DESC,id DESC',(project_id,))]


def delete_project_file(file_id:int, delete_physical=True):
    f=get_project_file(file_id)
    if not f:return
    with connect() as con:
        ids=[r['id'] for r in con.execute('SELECT id FROM project_file_chunks WHERE file_id=?',(file_id,))]
        if ids:
            marks=','.join(['?']*len(ids)); con.execute(f'DELETE FROM project_file_chunks_fts WHERE chunk_id IN ({marks})',ids)
        con.execute('DELETE FROM project_files WHERE id=?',(file_id,))
    if delete_physical:
        try:Path(f['stored_path']).unlink(missing_ok=True)
        except Exception:pass


def search_project_chunks(project_id:int, query:str, limit:int=10, file_ids:list[int]|None=None):
    ensure_project_kb_schema(); fts=build_query(query or '')
    if not fts:return []
    sql='''SELECT pc.id AS chunk_id,pc.page_no,pc.section,pc.content,pf.id AS file_id,pf.title,pf.doc_type,pf.source_ref,pf.original_name,
                  bm25(project_file_chunks_fts) AS rank
           FROM project_file_chunks_fts
           JOIN project_file_chunks pc ON pc.id=CAST(project_file_chunks_fts.chunk_id AS INTEGER)
           JOIN project_files pf ON pf.id=pc.file_id
           WHERE project_file_chunks_fts MATCH ? AND pc.project_id=?'''
    params=[fts,project_id]
    if file_ids:
        marks=','.join(['?']*len(file_ids)); sql+=f' AND pf.id IN ({marks})'; params+=list(file_ids)
    sql+=' ORDER BY rank LIMIT ?'; params.append(limit)
    with connect() as con:return [dict(r) for r in con.execute(sql,params)]


def build_project_evidence(rows:list[dict]):
    out=[]
    for i,r in enumerate(rows,start=1):
        loc=f"第{r['page_no']}页" if r.get('page_no') else (r.get('section') or '位置未识别')
        out.append(f"【P{i}】{r.get('doc_type','')}｜{r.get('title','')}｜{r.get('source_ref','')}｜{loc}\n{r.get('content','')}")
    return '\n\n'.join(out)


def project_kb_stats(project_id:int):
    ensure_project_kb_schema()
    with connect() as con:
        files=con.execute('SELECT COUNT(*) n FROM project_files WHERE project_id=?',(project_id,)).fetchone()['n']
        chunks=con.execute('SELECT COUNT(*) n FROM project_file_chunks WHERE project_id=?',(project_id,)).fetchone()['n']
        visual=con.execute('SELECT COUNT(*) n FROM project_files WHERE project_id=? AND visual_review_supported=1',(project_id,)).fetchone()['n']
        reviews=con.execute('SELECT COUNT(*) n FROM project_reviews WHERE project_id=?',(project_id,)).fetchone()['n']
        findings=con.execute('SELECT COUNT(*) n FROM review_findings WHERE project_id=?',(project_id,)).fetchone()['n']
    return {'files':files,'chunks':chunks,'visual_files':visual,'reviews':reviews,'findings':findings}


def save_review(project_id:int, review_type:str, title:str, scope:str, model:str, file_names:list[str], result:dict):
    ensure_project_kb_schema()
    with connect() as con:
        cur=con.execute('''INSERT INTO project_reviews(project_id,review_type,title,review_scope,model,file_names,summary,status,raw_json)
                           VALUES(?,?,?,?,?,?,?,?,?)''',
                        (project_id,review_type,title,scope,model,json.dumps(file_names,ensure_ascii=False),result.get('summary',''),'已完成',json.dumps(result,ensure_ascii=False)))
        rid=cur.lastrowid
        for f in result.get('findings',[]):
            con.execute('''INSERT INTO review_findings(review_id,project_id,severity,category,location,issue,norm_refs,project_refs,recommendation,evidence_grade,confidence,finding_type,status,notes)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                        (rid,project_id,f.get('severity','提示'),f.get('category',''),f.get('location',''),f.get('issue',''),
                         json.dumps(f.get('norm_refs',[]),ensure_ascii=False),json.dumps(f.get('project_refs',[]),ensure_ascii=False),f.get('recommendation',''),
                         f.get('evidence_grade','D'),f.get('confidence','中'),f.get('finding_type','需核对'),f.get('status','待确认'),f.get('notes','')))
        return rid


def list_reviews(project_id:int):
    ensure_project_kb_schema()
    with connect() as con:return [dict(r) for r in con.execute('SELECT * FROM project_reviews WHERE project_id=? ORDER BY id DESC',(project_id,))]


def list_findings(review_id:int|None=None, project_id:int|None=None):
    ensure_project_kb_schema(); sql='SELECT * FROM review_findings WHERE 1=1'; params=[]
    if review_id is not None:sql+=' AND review_id=?';params.append(review_id)
    if project_id is not None:sql+=' AND project_id=?';params.append(project_id)
    sql+=" ORDER BY CASE severity WHEN '高' THEN 1 WHEN '中' THEN 2 WHEN '低' THEN 3 ELSE 4 END,id"
    with connect() as con:
        out=[]
        for r in con.execute(sql,params):
            d=dict(r)
            for k in ('norm_refs','project_refs'):
                try:d[k]=json.loads(d[k] or '[]')
                except Exception:d[k]=[]
            out.append(d)
        return out


def update_finding_status(finding_id:int,status:str,notes:str=''):
    with connect() as con:con.execute('UPDATE review_findings SET status=?,notes=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',(status,notes,finding_id))
