from __future__ import annotations
import json, re
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA = BASE_DIR / "data" / "core_standards.json"

def _load():
    try:
        return json.loads(DATA.read_text(encoding="utf-8"))
    except Exception:
        return {"deprecated":[],"partial_repeals":[],"mohurd_repealed_2026":[]}

def normalize_code(text: str) -> str:
    return re.sub(r"\s+","", (text or "").upper()).replace("（","(").replace("）",")")

def find_deprecated_in_question(question: str):
    q = normalize_code(question)
    pack = _load()
    hits = []
    for x in pack.get("deprecated",[]):
        if normalize_code(x["old_code"]) in q:
            hits.append(x)
    for x in pack.get("mohurd_repealed_2026",[]):
        if normalize_code(x["code"]) in q:
            hits.append({
                "old_code":x["code"],"old_title":x["title"],
                "replacement":"未在V1.0目录中自动判定替代标准",
                "invalid_from":"2026-01-23",
                "note":x["basis"]
            })
    return hits

def find_partial_repeal_in_question(question: str):
    q = normalize_code(question)
    out = []
    for x in _load().get("partial_repeals",[]):
        if normalize_code(x["code"]) in q:
            out.append(x)
    return out

def build_status_warning(question: str) -> str:
    parts = []
    old = find_deprecated_in_question(question)
    if old:
        parts.append("【旧规范/废止拦截】")
        for x in old:
            parts.append(
                f"- {x['old_code']}《{x['old_title']}》已不应作为现行依据；"
                f"失效/废止起点：{x.get('invalid_from','')}；"
                f"现行替代/处理：{x.get('replacement','')}。"
            )
    partial = find_partial_repeal_in_question(question)
    if partial:
        parts.append("【部分强制性条文废止提醒】")
        for x in partial:
            parts.append(
                f"- {x['code']}《{x['title']}》并非简单“整本废止”，"
                f"但其中{x['affected']}已受{x['superseding_code']}调整/废止。"
                f"{x.get('note','')}"
            )
    ew = exact_clause_warning(question)
    if ew:
        parts.append(ew)
    return "\n".join(parts)


def find_exact_clause_override(question: str):
    import re
    try:
        from db import connect
    except Exception:
        return []
    code_m=re.search(r"(GB(?:/T)?|JGJ(?:/T)?|DBJ/T|DB44/T)\s*\d+(?:\.\d+)?(?:-\d{4})?",question or "",re.I)
    clause_m=re.search(r"(?<!\d)\d+(?:\.\d+){1,4}(?!\d)",question or "")
    if not code_m or not clause_m:
        return []
    code=normalize_code(code_m.group(0))
    clause=clause_m.group(0)
    try:
        with connect() as con:
            rows=con.execute("""SELECT * FROM standard_clause_overrides
                                WHERE replace(upper(standard_code),' ','')=?
                                  AND clause_no=?
                                  AND override_type IN ('repealed','repealed_or_superseded')""",
                             (code,clause)).fetchall()
            return [dict(r) for r in rows]
    except Exception:
        return []

def exact_clause_warning(question: str):
    hits=find_exact_clause_override(question)
    if not hits: return ""
    lines=["【条文级失效拦截】"]
    for x in hits:
        lines.append(
            f"- {x['standard_code']} 第{x['clause_no']}条已被标记为{x['override_type']}；"
            f"上位/替代规范：{x['superseding_code']}。{x.get('note','')}"
        )
    return "\n".join(lines)
