from __future__ import annotations
import re

CN_RE = re.compile(r"[\u4e00-\u9fff]+")
CODE_RE = re.compile(r"(?:GB|GB/T|JGJ|JGJ/T|DBJ/T|DB44/T)\s*\d+(?:\.\d+)?(?:-\d{4})?", re.I)
CLAUSE_RE = re.compile(r"(?<!\d)\d+(?:\.\d+){1,4}(?!\d)")

def normalize_code(s: str) -> str:
    return re.sub(r"\s+","", (s or "").upper())

def zh_ngrams(text: str, min_n: int = 2, max_n: int = 4, cap: int = 240):
    out, seen = [], set()
    for seg in CN_RE.findall(text or ""):
        if len(seg) <= 1:
            continue
        for n in range(min_n, min(max_n, len(seg)) + 1):
            for i in range(0, len(seg)-n+1):
                token = seg[i:i+n]
                if token not in seen:
                    seen.add(token); out.append(token)
                    if len(out) >= cap:
                        return out
    return out

def technical_tokens(text: str):
    tokens = []
    tokens += [normalize_code(x) for x in CODE_RE.findall(text or "")]
    tokens += CLAUSE_RE.findall(text or "")
    tokens += zh_ngrams(text or "")
    # 英数字词
    tokens += re.findall(r"[A-Za-z][A-Za-z0-9/_-]{1,30}|\d{2,}", text or "")
    # 去重
    seen, final = set(), []
    for t in tokens:
        t = t.strip()
        if t and t not in seen:
            seen.add(t); final.append(t)
    return final

def build_index_text(code: str, clause_no: str, heading: str, content: str):
    raw = f"{code} {clause_no} {heading} {content}"
    return " ".join(technical_tokens(raw))

def build_query(text: str, cap: int = 80):
    toks = technical_tokens(text)[:cap]
    if not toks:
        return ""
    return " OR ".join('"' + t.replace('"','') + '"' for t in toks)
