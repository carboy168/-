from __future__ import annotations
import os, sqlite3
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
DB_PATH = Path(os.getenv("DATABASE_PATH", "data/norms.db"))

SCHEMA = r"""
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS standards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    title TEXT NOT NULL,
    category TEXT DEFAULT '',
    jurisdiction TEXT DEFAULT '国家',
    authority TEXT DEFAULT '',
    publish_date TEXT DEFAULT '',
    effective_date TEXT DEFAULT '',
    repeal_date TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT '待核验',
    mandatory_level TEXT DEFAULT '',
    supersedes TEXT DEFAULT '',
    superseded_by TEXT DEFAULT '',
    source_url TEXT DEFAULT '',
    source_priority INTEGER DEFAULT 0,
    notes TEXT DEFAULT '',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(code, title)
);

CREATE TABLE IF NOT EXISTS clauses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    standard_id INTEGER NOT NULL,
    clause_no TEXT DEFAULT '',
    page_no INTEGER,
    heading TEXT DEFAULT '',
    content TEXT NOT NULL,
    source_file TEXT DEFAULT '',
    FOREIGN KEY(standard_id) REFERENCES standards(id) ON DELETE CASCADE
);

CREATE VIRTUAL TABLE IF NOT EXISTS clauses_fts USING fts5(
    clause_no,
    heading,
    content,
    standard_code UNINDEXED,
    standard_title UNINDEXED,
    clause_id UNINDEXED,
    tokenize = 'unicode61'
);

CREATE TABLE IF NOT EXISTS update_candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_name TEXT,
    title TEXT,
    url TEXT,
    discovered_date TEXT DEFAULT CURRENT_DATE,
    raw_date TEXT DEFAULT '',
    fingerprint TEXT UNIQUE,
    status TEXT DEFAULT '待核验',
    notes TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS source_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_name TEXT,
    checked_at TEXT DEFAULT CURRENT_TIMESTAMP,
    url TEXT,
    ok INTEGER,
    message TEXT
);
"""

def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con

def init_db():
    with connect() as con:
        con.executescript(SCHEMA)
        # V2/V3 schema is also bootstrapped here so the first norm query cannot fail
        # simply because the user has not yet opened the full-text management page.
        con.executescript(r"""
        CREATE TABLE IF NOT EXISTS source_documents(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            standard_id INTEGER NOT NULL, source_page TEXT DEFAULT '', file_url TEXT DEFAULT '',
            local_path TEXT DEFAULT '', sha256 TEXT DEFAULT '', file_size INTEGER DEFAULT 0,
            page_count INTEGER DEFAULT 0, clause_count INTEGER DEFAULT 0,
            status TEXT DEFAULT '已导入', created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(standard_id) REFERENCES standards(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS standard_clause_overrides(
            id INTEGER PRIMARY KEY AUTOINCREMENT, standard_code TEXT NOT NULL, clause_no TEXT DEFAULT '',
            override_type TEXT NOT NULL, superseding_code TEXT DEFAULT '', note TEXT DEFAULT '',
            UNIQUE(standard_code, clause_no, superseding_code)
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS clauses_zh_fts USING fts5(
            search_tokens, clause_id UNINDEXED, standard_code UNINDEXED, standard_title UNINDEXED,
            tokenize='unicode61'
        );
        CREATE TABLE IF NOT EXISTS route_logs(
            id INTEGER PRIMARY KEY AUTOINCREMENT, question TEXT, intent TEXT, risk TEXT, confidence TEXT,
            top_theme TEXT, primary_codes TEXT, route_json TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """)

def upsert_standard(data: dict) -> int:
    fields = [
        "code","title","category","jurisdiction","authority","publish_date",
        "effective_date","repeal_date","status","mandatory_level","supersedes",
        "superseded_by","source_url","source_priority","notes"
    ]
    values = [data.get(f, "") for f in fields]
    with connect() as con:
        con.execute(
            f"""INSERT INTO standards ({",".join(fields)})
                VALUES ({",".join(["?"]*len(fields))})
                ON CONFLICT(code,title) DO UPDATE SET
                category=excluded.category,
                jurisdiction=excluded.jurisdiction,
                authority=excluded.authority,
                publish_date=excluded.publish_date,
                effective_date=excluded.effective_date,
                repeal_date=excluded.repeal_date,
                status=excluded.status,
                mandatory_level=excluded.mandatory_level,
                supersedes=excluded.supersedes,
                superseded_by=excluded.superseded_by,
                source_url=excluded.source_url,
                source_priority=excluded.source_priority,
                notes=excluded.notes,
                updated_at=CURRENT_TIMESTAMP
            """, values)
        row = con.execute(
            "SELECT id FROM standards WHERE code=? AND title=?",
            (data["code"], data["title"])
        ).fetchone()
        return row["id"]

def list_standards():
    with connect() as con:
        return [dict(r) for r in con.execute(
            """SELECT * FROM standards
               ORDER BY
               CASE status WHEN '现行' THEN 1 WHEN '即将实施' THEN 2 WHEN '待核验' THEN 3 ELSE 4 END,
               source_priority DESC, code"""
        )]

def get_standard(standard_id: int):
    with connect() as con:
        row = con.execute("SELECT * FROM standards WHERE id=?", (standard_id,)).fetchone()
        return dict(row) if row else None

def replace_clauses(standard_id: int, clauses: list[dict], source_file: str):
    with connect() as con:
        old_ids = [r["id"] for r in con.execute("SELECT id FROM clauses WHERE standard_id=?", (standard_id,))]
        if old_ids:
            placeholders = ",".join(["?"] * len(old_ids))
            con.execute(f"DELETE FROM clauses_fts WHERE clause_id IN ({placeholders})", old_ids)
        con.execute("DELETE FROM clauses WHERE standard_id=?", (standard_id,))
        std = con.execute("SELECT code,title FROM standards WHERE id=?", (standard_id,)).fetchone()
        for c in clauses:
            cur = con.execute(
                """INSERT INTO clauses (standard_id,clause_no,page_no,heading,content,source_file)
                   VALUES (?,?,?,?,?,?)""",
                (standard_id, c.get("clause_no",""), c.get("page_no"), c.get("heading",""),
                 c["content"], source_file)
            )
            clause_id = cur.lastrowid
            con.execute(
                """INSERT INTO clauses_fts
                   (clause_no,heading,content,standard_code,standard_title,clause_id)
                   VALUES (?,?,?,?,?,?)""",
                (c.get("clause_no",""), c.get("heading",""), c["content"],
                 std["code"], std["title"], str(clause_id))
            )

def search_clauses(query: str, limit: int = 10, active_only: bool = True):
    query = (query or "").strip()
    if not query:
        return []
    # FTS5 simple sanitization: AND terms, quoted unsafe punctuation removed.
    import re
    terms = [t for t in re.split(r"\s+", re.sub(r'[^\w\u4e00-\u9fff\.\-/]+', ' ', query)) if t]
    fts_query = " OR ".join(f'"{t}"' for t in terms[:12])
    with connect() as con:
        sql = """
        SELECT c.id AS clause_id, c.clause_no, c.page_no, c.heading, c.content,
               s.id AS standard_id, s.code, s.title, s.status, s.mandatory_level,
               s.source_url, s.source_priority, s.effective_date,
               bm25(clauses_fts) AS rank
        FROM clauses_fts
        JOIN clauses c ON c.id = CAST(clauses_fts.clause_id AS INTEGER)
        JOIN standards s ON s.id = c.standard_id
        WHERE clauses_fts MATCH ?
        """
        params = [fts_query]
        if active_only:
            sql += " AND s.status IN ('现行','即将实施') "
        sql += """
        ORDER BY
          CASE s.status WHEN '现行' THEN 0 WHEN '即将实施' THEN 1 ELSE 2 END,
          s.source_priority DESC,
          rank
        LIMIT ?
        """
        params.append(limit)
        return [dict(r) for r in con.execute(sql, params)]

def recent_update_candidates(limit=100):
    with connect() as con:
        return [dict(r) for r in con.execute(
            "SELECT * FROM update_candidates ORDER BY discovered_date DESC, id DESC LIMIT ?", (limit,)
        )]


def search_clauses_v2(query: str, limit: int = 12):
    """中文工程规范检索：规范号/条文号精确优先 + 2~4字N-gram召回。仅正式现行/即将实施库。"""
    import re
    from search_zh import build_query, normalize_code
    q=(query or "").strip()
    if not q: return []
    with connect() as con:
        # 1) 规范编号 + 条文号精确定位
        code_match=re.search(r"(GB(?:/T)?|JGJ(?:/T)?|DBJ/T|DB44/T)\s*\d+(?:\.\d+)?(?:-\d{4})?",q,re.I)
        clause_match=re.search(r"(?<!\d)\d+(?:\.\d+){1,4}(?!\d)",q)
        exact=[]
        if code_match:
            code_norm=normalize_code(code_match.group(0))
            sql="""SELECT c.id AS clause_id,c.clause_no,c.page_no,c.heading,c.content,
                          s.id AS standard_id,s.code,s.title,s.status,s.mandatory_level,
                          s.source_url,s.source_priority,s.effective_date,0 AS rank
                   FROM clauses c JOIN standards s ON s.id=c.standard_id
                   WHERE replace(upper(s.code),' ','')=? AND s.status IN ('现行','即将实施')
                     AND NOT EXISTS (
                         SELECT 1 FROM standard_clause_overrides o
                         WHERE replace(upper(o.standard_code),' ','')=replace(upper(s.code),' ','')
                           AND o.clause_no=c.clause_no
                           AND o.clause_no<>'*'
                           AND o.override_type IN ('repealed','repealed_or_superseded')
                     )"""
            params=[code_norm]
            if clause_match:
                sql+=" AND c.clause_no=?"; params.append(clause_match.group(0))
            sql+=" LIMIT ?"; params.append(limit)
            exact=[dict(r) for r in con.execute(sql,params)]
            if exact: return exact

        # 2) 中文N-gram全文召回
        fts=build_query(q)
        if not fts: return []
        sql="""SELECT c.id AS clause_id,c.clause_no,c.page_no,c.heading,c.content,
                      s.id AS standard_id,s.code,s.title,s.status,s.mandatory_level,
                      s.source_url,s.source_priority,s.effective_date,
                      bm25(clauses_zh_fts) AS rank
               FROM clauses_zh_fts
               JOIN clauses c ON c.id=CAST(clauses_zh_fts.clause_id AS INTEGER)
               JOIN standards s ON s.id=c.standard_id
               WHERE clauses_zh_fts MATCH ?
                 AND s.status IN ('现行','即将实施')
                 AND NOT EXISTS (
                     SELECT 1 FROM standard_clause_overrides o
                     WHERE replace(upper(o.standard_code),' ','')=replace(upper(s.code),' ','')
                       AND o.clause_no=c.clause_no
                       AND o.clause_no<>'*'
                       AND o.override_type IN ('repealed','repealed_or_superseded')
                 )
               ORDER BY CASE s.status WHEN '现行' THEN 0 ELSE 1 END,
                        s.source_priority DESC, rank
               LIMIT ?"""
        return [dict(r) for r in con.execute(sql,(fts,limit))]


def search_clauses_v3(query: str, standard_codes=None, limit: int = 12):
    """V3路由检索：在候选规范集合内优先做中文N-gram召回；无命中时由上层决定是否放宽。"""
    import re
    from search_zh import build_query, normalize_code
    q=(query or '').strip()
    if not q: return []
    codes=[normalize_code(x) for x in (standard_codes or []) if x]
    with connect() as con:
        code_match=re.search(r'(GB(?:/T)?|JGJ(?:/T)?|DBJ/T|DB44/T)\s*\d+(?:\.\d+)?(?:-\d{4})?',q,re.I)
        clause_match=re.search(r'(?<!\d)\d+(?:\.\d+){1,4}(?!\d)',q)
        if code_match:
            cn=normalize_code(code_match.group(0))
            sql="""SELECT c.id AS clause_id,c.clause_no,c.page_no,c.heading,c.content,
                          s.id AS standard_id,s.code,s.title,s.status,s.mandatory_level,
                          s.source_url,s.source_priority,s.effective_date,0 AS rank
                   FROM clauses c JOIN standards s ON s.id=c.standard_id
                   WHERE replace(upper(s.code),' ','')=? AND s.status IN ('现行','即将实施')
                     AND NOT EXISTS (SELECT 1 FROM standard_clause_overrides o
                         WHERE replace(upper(o.standard_code),' ','')=replace(upper(s.code),' ','')
                           AND o.clause_no=c.clause_no AND o.clause_no<>'*'
                           AND o.override_type IN ('repealed','repealed_or_superseded'))"""
            params=[cn]
            if clause_match:
                sql+=' AND c.clause_no=?'; params.append(clause_match.group(0))
            sql+=' LIMIT ?'; params.append(limit)
            exact=[dict(r) for r in con.execute(sql,params)]
            if exact: return exact
        fts=build_query(q)
        if not fts: return []
        sql="""SELECT c.id AS clause_id,c.clause_no,c.page_no,c.heading,c.content,
                      s.id AS standard_id,s.code,s.title,s.status,s.mandatory_level,
                      s.source_url,s.source_priority,s.effective_date,
                      bm25(clauses_zh_fts) AS rank
               FROM clauses_zh_fts
               JOIN clauses c ON c.id=CAST(clauses_zh_fts.clause_id AS INTEGER)
               JOIN standards s ON s.id=c.standard_id
               WHERE clauses_zh_fts MATCH ?
                 AND s.status IN ('现行','即将实施')
                 AND NOT EXISTS (SELECT 1 FROM standard_clause_overrides o
                     WHERE replace(upper(o.standard_code),' ','')=replace(upper(s.code),' ','')
                       AND o.clause_no=c.clause_no AND o.clause_no<>'*'
                       AND o.override_type IN ('repealed','repealed_or_superseded'))"""
        params=[fts]
        if codes:
            sql += ' AND replace(upper(s.code),\' \',\'\') IN (' + ','.join(['?']*len(codes)) + ')'
            params += codes
        sql += """ ORDER BY CASE s.status WHEN '现行' THEN 0 ELSE 1 END,
                         s.source_priority DESC, rank LIMIT ?"""
        params.append(limit)
        return [dict(r) for r in con.execute(sql,params)]
