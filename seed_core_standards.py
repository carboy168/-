from __future__ import annotations
import json
from pathlib import Path
from db import init_db, upsert_standard, connect

DATA = Path("data/core_standards.json")

def normalize_status(item):
    # V1.0数据库只让“已核验A级”进入正式可引用状态。
    if item.get("grade") != "A":
        return "待核验"
    raw = item.get("status","")
    if "即将实施" in raw:
        return "即将实施"
    if raw.startswith("现行") or "现行至" in raw:
        return "现行"
    if "废止" in raw and not raw.startswith("现行"):
        return "废止"
    return "待核验"

def main():
    init_db()
    pack = json.loads(DATA.read_text(encoding="utf-8"))
    count = 0
    for x in pack["standards"]:
        status = normalize_status(x)
        notes = (
            f"V1.0首批核心规范库；核验等级={x.get('grade','')}; "
            f"目录原状态={x.get('status','')}; "
            f"典型场景={x.get('scene','')}; "
            f"检索关键词={x.get('kw','')}; "
            f"关系说明={x.get('relation','')}"
        )
        upsert_standard({
            "code": x["code"],
            "title": x["title"],
            "category": x.get("cat",""),
            "jurisdiction": x.get("level",""),
            "authority": "住房城乡建设主管部门/标准主管部门",
            "publish_date": x.get("pub",""),
            "effective_date": x.get("eff",""),
            "repeal_date": "",
            "status": status,
            "mandatory_level": x.get("mandatory",""),
            "supersedes": x.get("relation",""),
            "superseded_by": "",
            "source_url": x.get("source",""),
            "source_priority": 100 if x.get("grade")=="A" else 50,
            "notes": notes
        })
        count += 1

    # 单独保存废止黑名单和“部分强条废止”关系，供问答前置拦截。
    with connect() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS deprecated_standards(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            old_code TEXT UNIQUE,
            old_title TEXT,
            replacement TEXT,
            invalid_from TEXT,
            note TEXT
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS partial_repeals(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT,
            title TEXT,
            affected TEXT,
            superseding_code TEXT,
            note TEXT,
            UNIQUE(code, superseding_code)
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS repeal_catalog(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE,
            title TEXT,
            basis TEXT,
            repeal_date TEXT
        )""")
        for x in pack["deprecated"]:
            con.execute("""INSERT INTO deprecated_standards(old_code,old_title,replacement,invalid_from,note)
                           VALUES(?,?,?,?,?)
                           ON CONFLICT(old_code) DO UPDATE SET
                           old_title=excluded.old_title,replacement=excluded.replacement,
                           invalid_from=excluded.invalid_from,note=excluded.note""",
                        (x["old_code"],x["old_title"],x["replacement"],x["invalid_from"],x["note"]))
        for x in pack["partial_repeals"]:
            con.execute("""INSERT INTO partial_repeals(code,title,affected,superseding_code,note)
                           VALUES(?,?,?,?,?)
                           ON CONFLICT(code,superseding_code) DO UPDATE SET
                           title=excluded.title,affected=excluded.affected,note=excluded.note""",
                        (x["code"],x["title"],x["affected"],x["superseding_code"],x["note"]))
        for x in pack["mohurd_repealed_2026"]:
            con.execute("""INSERT INTO repeal_catalog(code,title,basis,repeal_date)
                           VALUES(?,?,?,?)
                           ON CONFLICT(code) DO UPDATE SET
                           title=excluded.title,basis=excluded.basis,repeal_date=excluded.repeal_date""",
                        (x["code"],x["title"],x["basis"],"2026-01-23"))

    print(f"首批核心规范目录已导入：{count} 部。")
    print("A级记录进入可引用状态；B级记录统一进入“待核验”，不会被正式引用。")
    print(f"废止黑名单：{len(pack['deprecated'])} 项；部分强条替代关系：{len(pack['partial_repeals'])} 项；2026废止目录：{len(pack['mohurd_repealed_2026'])} 项。")

if __name__ == "__main__":
    main()
