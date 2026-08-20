from __future__ import annotations
import json
from pathlib import Path
from db import init_db, connect
from ingest_v2 import ensure_v2_schema
from search_zh import build_index_text

def main():
    init_db(); ensure_v2_schema()
    pack=json.loads(Path("data/core_standards.json").read_text(encoding="utf-8"))
    with connect() as con:
        for x in pack.get("partial_repeals",[]):
            affected=x.get("affected","")
            # 尽量拆出明确条文号；含“部分强制性条文”等模糊描述时保留通配提醒记录
            nums=[]
            import re
            nums=re.findall(r"\d+(?:\.\d+){1,4}",affected)
            if not nums: nums=["*"]
            for no in nums:
                con.execute("""INSERT INTO standard_clause_overrides
                    (standard_code,clause_no,override_type,superseding_code,note)
                    VALUES(?,?,?,?,?)
                    ON CONFLICT(standard_code,clause_no,superseding_code)
                    DO UPDATE SET override_type=excluded.override_type,note=excluded.note""",
                    (x["code"],no,"repealed_or_superseded",x["superseding_code"],x.get("note","")))
        extra_path=Path("data/phase2_clause_overrides.json")
        if extra_path.exists():
            extra=json.loads(extra_path.read_text(encoding="utf-8"))
            for item in extra.get("items",[]):
                for no in item.get("clauses",[]):
                    con.execute("""INSERT INTO standard_clause_overrides
                        (standard_code,clause_no,override_type,superseding_code,note)
                        VALUES(?,?,?,?,?)
                        ON CONFLICT(standard_code,clause_no,superseding_code)
                        DO UPDATE SET override_type=excluded.override_type,note=excluded.note""",
                        (item["standard_code"],no,"repealed",item["superseding_code"],item.get("note","")))
        # 为既有条文补中文索引
        rows=con.execute("""SELECT c.id,c.clause_no,c.heading,c.content,s.code,s.title
                            FROM clauses c JOIN standards s ON s.id=c.standard_id""").fetchall()
        existing=set(r["clause_id"] for r in con.execute("SELECT clause_id FROM clauses_zh_fts"))
        for r in rows:
            if str(r["id"]) in existing: continue
            con.execute("""INSERT INTO clauses_zh_fts(search_tokens,clause_id,standard_code,standard_title)
                           VALUES(?,?,?,?)""",
                        (build_index_text(r["code"],r["clause_no"],r["heading"],r["content"]),
                         str(r["id"]),r["code"],r["title"]))
    print("第二阶段数据库迁移完成：中文N-gram索引、全文来源表、条文失效覆盖表已就绪。")

if __name__=="__main__":
    main()
