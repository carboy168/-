from __future__ import annotations
import re
from pathlib import Path
from pypdf import PdfReader
from db import replace_clauses

CLAUSE_RE = re.compile(r"^\s*((?:\d+\.)+\d+)\s+(.+)$")
HEADING_RE = re.compile(r"^\s*(\d+)\s+([^\d].{1,80})$")

def extract_pdf(path: str) -> list[tuple[int,str]]:
    reader = PdfReader(path)
    pages = []
    for i, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        pages.append((i, text))
    return pages

def extract_txt(path: str) -> list[tuple[int,str]]:
    text = Path(path).read_text(encoding="utf-8", errors="ignore")
    return [(1, text)]

def parse_pages(pages: list[tuple[int,str]]) -> list[dict]:
    """
    V1.0 规则解析：
    - 优先识别 1.2.3 这类条文号
    - 无法识别时按 900~1600 字切块并保留页码
    """
    clauses = []
    current = None
    current_heading = ""

    def flush():
        nonlocal current
        if current and current["content"].strip():
            current["content"] = current["content"].strip()
            clauses.append(current)
        current = None

    for page_no, text in pages:
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        # 如果整页基本没有换行，后面走普通切块
        found_clause = False
        for line in lines:
            m = CLAUSE_RE.match(line)
            if m:
                found_clause = True
                flush()
                current = {
                    "clause_no": m.group(1),
                    "page_no": page_no,
                    "heading": current_heading,
                    "content": line
                }
            else:
                hm = HEADING_RE.match(line)
                if hm and len(line) < 80:
                    current_heading = line
                if current:
                    current["content"] += "\n" + line
        if not found_clause and not current and text.strip():
            clean = re.sub(r"\s+", " ", text).strip()
            step = 1200
            overlap = 150
            start = 0
            while start < len(clean):
                chunk = clean[start:start+step]
                clauses.append({
                    "clause_no": "",
                    "page_no": page_no,
                    "heading": current_heading,
                    "content": chunk
                })
                start += step - overlap
    flush()

    # 防止极长条文
    normalized = []
    for c in clauses:
        txt = c["content"]
        if len(txt) <= 2200:
            normalized.append(c)
            continue
        for idx in range(0, len(txt), 1800):
            cc = dict(c)
            cc["content"] = txt[idx:idx+2000]
            if idx > 0 and cc["clause_no"]:
                cc["clause_no"] += "（续）"
            normalized.append(cc)
    return normalized

def ingest_file(standard_id: int, path: str):
    ext = Path(path).suffix.lower()
    if ext == ".pdf":
        pages = extract_pdf(path)
    elif ext in (".txt", ".md"):
        pages = extract_txt(path)
    else:
        raise ValueError("V1.0 仅支持 PDF/TXT/MD")
    total_text = sum(len(t.strip()) for _, t in pages)
    if total_text < 300:
        raise ValueError("提取到的文字过少，可能是扫描版PDF。V1.0暂不做OCR。")
    clauses = parse_pages(pages)
    if not clauses:
        raise ValueError("未能形成可检索条文块。")
    replace_clauses(standard_id, clauses, Path(path).name)
    return len(clauses)
