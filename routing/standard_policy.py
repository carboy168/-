from __future__ import annotations
import json,re
from pathlib import Path
from typing import Iterable

from routing.explicit_parser import STANDARD_RE,normalize_standard_code

ROOT=Path(__file__).resolve().parents[1]
CORE_FILE=ROOT/"data"/"core_standards.json"
CLAUSE_OVERRIDE_FILE=ROOT/"data"/"phase2_clause_overrides.json"


def _norm(code:str)->str:return re.sub(r"\s+","",(code or "").upper())


class StandardPolicyService:
    """Validate router candidates against the existing catalogue and clause overrides."""
    def __init__(self,core_file:Path|None=None):
        self.pack=json.loads((core_file or CORE_FILE).read_text(encoding="utf-8"))
        self.catalog={_norm(x["code"]):x for x in self.pack.get("standards",[])}
        self.deprecated={_norm(x["old_code"]):x for x in self.pack.get("deprecated",[])}
        self.partial={_norm(x["code"]):x for x in self.pack.get("partial_repeals",[])}
        extra=json.loads(CLAUSE_OVERRIDE_FILE.read_text(encoding="utf-8")) if CLAUSE_OVERRIDE_FILE.exists() else {"items":[]}
        self.clause_overrides={( _norm(x["standard_code"]),clause):x for x in extra.get("items",[]) for clause in x.get("clauses",[])}

    def _db_status(self,code:str)->str:
        try:
            from db import connect
            with connect() as con:
                row=con.execute("SELECT status FROM standards WHERE replace(upper(code),' ','')=? ORDER BY source_priority DESC LIMIT 1",(_norm(code),)).fetchone()
            return row["status"] if row else ""
        except Exception:return ""

    def _clause_override(self,code:str,clause_no:str)->dict|None:
        if not clause_no:return None
        try:
            from db import connect
            with connect() as con:
                row=con.execute("""SELECT * FROM standard_clause_overrides
                    WHERE replace(upper(standard_code),' ','')=? AND clause_no=?
                      AND override_type IN ('repealed','repealed_or_superseded') LIMIT 1""",(_norm(code),clause_no)).fetchone()
            if row:return dict(row)
        except Exception:pass
        exact=self.clause_overrides.get((_norm(code),clause_no))
        if exact:return {"standard_code":code,"clause_no":clause_no,"override_type":"repealed","superseding_code":exact.get("superseding_code","")}
        item=self.partial.get(_norm(code))
        if item and clause_no in re.findall(r"\d+(?:\.\d+){1,4}",item.get("affected","")):
            return {"standard_code":code,"clause_no":clause_no,"override_type":"repealed_or_superseded","superseding_code":item.get("superseding_code","")}
        return None

    @staticmethod
    def _replacement_codes(text:str)->list[str]:
        return list(dict.fromkeys(normalize_standard_code(m) for m in STANDARD_RE.finditer(text or "")))

    def validate(self,codes:Iterable[str],explicit_code:str="",clause_no:str="")->tuple[list[str],list[str],list[dict]]:
        allowed=[];warnings=[];statuses=[]
        for code in dict.fromkeys(x for x in codes if x):
            key=_norm(code);deprecated=self.deprecated.get(key)
            if deprecated:
                replacements=self._replacement_codes(deprecated.get("replacement",""))
                warnings.append(f"{code} 已废止或被替代，不作为当前首选依据。")
                statuses.append({"code":code,"status":"废止/被替代","allowed":False,"replacements":replacements})
                for replacement in replacements:
                    if replacement not in allowed:allowed.append(replacement)
                continue
            override=self._clause_override(code,clause_no if _norm(explicit_code)==key else "")
            if override:
                replacement=override.get("superseding_code","")
                warnings.append(f"{code} 第{clause_no}条已被条文级 override 拦截。")
                statuses.append({"code":code,"status":"条文失效","allowed":False,"replacements":[replacement] if replacement else []})
                if replacement and replacement not in allowed:allowed.append(replacement)
                continue
            item=self.catalog.get(key,{});status=self._db_status(code) or item.get("status","")
            if not item and not status:
                statuses.append({"code":code,"status":"待核验","allowed":False,"replacements":[]})
                warnings.append(f"{code} 未在当前已核验规范目录中，不作为首选依据。")
                continue
            if "即将实施" in status:
                statuses.append({"code":code,"status":"即将实施","allowed":False,"replacements":[]})
                warnings.append(f"{code} 当前为即将实施，不作为现行首选依据。")
                continue
            if status in ("废止","被替代","待核验") or ("废止" in status and not status.startswith("现行")):
                statuses.append({"code":code,"status":status or "待核验","allowed":False,"replacements":[]})
                warnings.append(f"{code} 当前状态为{status or '待核验'}，不作为首选依据。")
                continue
            partial=key in self.partial or "部分" in status
            statuses.append({"code":code,"status":"现行（部分条文调整）" if partial else "现行","allowed":True,"replacements":[]})
            if partial:warnings.append(f"{code} 存在部分条文调整，引用前必须执行条文级校验。")
            if code not in allowed:allowed.append(code)
        return allowed,warnings,statuses
