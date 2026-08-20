from __future__ import annotations
import re, hashlib
from pathlib import Path
from pypdf import PdfReader
from db import connect
from search_zh import build_index_text

CHAPTER_RE = re.compile(r"^\s*(\d+)\s+([^\d].{1,80})$")
SECTION_RE = re.compile(r"^\s*(\d+\.\d+)\s+(.{1,100})$")
CLAUSE_RE = re.compile(r"^\s*(\d+(?:\.\d+){2,4})\s*(.*)$")

def extract_pdf(path: str):
    reader = PdfReader(path)
    pages=[]
    for i,p in enumerate(reader.pages, start=1):
        text=p.extract_text() or ""
        pages.append((i,text))
    return pages, len(reader.pages)

def _clean(line):
    return re.sub(r"\s+"," ", line or "").strip()

def parse_standard(pages):
    clauses=[]
    chapter=""
    section=""
    current=None

    def flush():
        nonlocal current
        if current and current["content"].strip():
            current["content"]=current["content"].strip()
            current["content_hash"]=hashlib.sha256(current["content"].encode("utf-8")).hexdigest()
            clauses.append(current)
        current=None

    for page_no,text in pages:
        lines=[_clean(x) for x in text.splitlines() if _clean(x)]
        for line in lines:
            cm=CHAPTER_RE.match(line)
            sm=SECTION_RE.match(line)
            am=CLAUSE_RE.match(line)
            if am:
                flush()
                current={
                    "clause_no":am.group(1),
                    "page_no":page_no,
                    "chapter":chapter,
                    "section":section,
                    "heading":"",
                    "content":line
                }
            elif sm and not am:
                flush()
                section=line
            elif cm and not sm and not am:
                flush()
                chapter=line
                section=""
            elif current:
                current["content"] += "\n" + line
        # 页面没有识别到条文且文字很长时，保留页级兜底块
        if not lines and text.strip():
            pass
    flush()

    # 如果整本几乎没识别到条文，则页级切块，确保可检索但只引用页码
    if len(clauses) < 5:
        clauses=[]
        for page_no,text in pages:
            clean=_clean(text)
            if not clean: continue
            step, overlap = 1300, 180
            for start in range(0,len(clean),step-overlap):
                chunk=clean[start:start+step]
                if len(chunk)<80: continue
                clauses.append({
                    "clause_no":"","page_no":page_no,"chapter":"","section":"",
                    "heading":"","content":chunk,
                    "content_hash":hashlib.sha256(chunk.encode("utf-8")).hexdigest()
                })
    return clauses

def ensure_v2_schema():
    with connect() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS source_documents(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            standard_id INTEGER NOT NULL,
            source_page TEXT DEFAULT '',
            file_url TEXT DEFAULT '',
            local_path TEXT DEFAULT '',
            sha256 TEXT DEFAULT '',
            file_size INTEGER DEFAULT 0,
            page_count INTEGER DEFAULT 0,
            clause_count INTEGER DEFAULT 0,
            status TEXT DEFAULT '已导入',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(standard_id) REFERENCES standards(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS standard_clause_overrides(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            standard_code TEXT NOT NULL,
            clause_no TEXT DEFAULT '',
            override_type TEXT NOT NULL,
            superseding_code TEXT DEFAULT '',
            note TEXT DEFAULT '',
            UNIQUE(standard_code, clause_no, superseding_code)
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS clauses_zh_fts USING fts5(
            search_tokens,
            clause_id UNINDEXED,
            standard_code UNINDEXED,
            standard_title UNINDEXED,
            tokenize='unicode61'
        );
        """)

def ingest_pdf_v2(standard_id:int, path:str, source_page:str="", file_url:str=""):
    ensure_v2_schema()
    pages,page_count=extract_pdf(path)
    total=sum(len(t.strip()) for _,t in pages)
    if total < 500:
        raise ValueError("PDF文字层过少，可能是扫描版。已拒绝静默OCR，请进入OCR待处理队列。")
    clauses=parse_standard(pages)
    raw=Path(path).read_bytes()
    sha=hashlib.sha256(raw).hexdigest()
    size=len(raw)

    with connect() as con:
        std=con.execute("SELECT code,title FROM standards WHERE id=?",(standard_id,)).fetchone()
        if not std: raise ValueError("standard_id不存在")
        old=[r["id"] for r in con.execute("SELECT id FROM clauses WHERE standard_id=?",(standard_id,))]
        if old:
            marks=",".join(["?"]*len(old))
            con.execute(f"DELETE FROM clauses_fts WHERE clause_id IN ({marks})",old)
            con.execute(f"DELETE FROM clauses_zh_fts WHERE clause_id IN ({marks})",old)
        con.execute("DELETE FROM clauses WHERE standard_id=?",(standard_id,))
        seen=set()
        for c in clauses:
            if c["content_hash"] in seen: continue
            seen.add(c["content_hash"])
            heading=" | ".join(x for x in [c.get("chapter",""),c.get("section",""),c.get("heading","")] if x)
            cur=con.execute("""INSERT INTO clauses(standard_id,clause_no,page_no,heading,content,source_file)
                               VALUES(?,?,?,?,?,?)""",
                            (standard_id,c.get("clause_no",""),c.get("page_no"),heading,c["content"],Path(path).name))
            cid=cur.lastrowid
            con.execute("""INSERT INTO clauses_fts
                           (clause_no,heading,content,standard_code,standard_title,clause_id)
                           VALUES(?,?,?,?,?,?)""",
                        (c.get("clause_no",""),heading,c["content"],std["code"],std["title"],str(cid)))
            con.execute("""INSERT INTO clauses_zh_fts
                           (search_tokens,clause_id,standard_code,standard_title)
                           VALUES(?,?,?,?)""",
                        (build_index_text(std["code"],c.get("clause_no",""),heading,c["content"]),
                         str(cid),std["code"],std["title"]))
        con.execute("""INSERT INTO source_documents
                       (standard_id,source_page,file_url,local_path,sha256,file_size,page_count,clause_count,status)
                       VALUES(?,?,?,?,?,?,?,?,?)""",
                    (standard_id,source_page,file_url,str(path),sha,size,page_count,len(seen),"已导入"))
    return {"pages":page_count,"clauses":len(seen),"sha256":sha,"bytes":size}
