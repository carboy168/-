from __future__ import annotations
import json, re, hashlib
from pathlib import Path
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup
from db import init_db, connect

KEYWORDS = ("标准", "规范", "规程", "公告", "发布", "修订", "废止", "工程建设", "强制性")
UA = "Mozilla/5.0 NormAgentV1/1.0 (+local engineering standards monitor)"
BASE_DIR = Path(__file__).resolve().parent

def fingerprint(source_name, title, url):
    return hashlib.sha256(f"{source_name}|{title}|{url}".encode("utf-8")).hexdigest()

def scan_source(source):
    url = source["url"]
    r = requests.get(url, timeout=20, headers={"User-Agent": UA})
    r.raise_for_status()
    r.encoding = r.apparent_encoding or r.encoding
    soup = BeautifulSoup(r.text, "html.parser")
    base_host = urlparse(url).netloc
    candidates = []
    for a in soup.find_all("a", href=True):
        title = " ".join(a.get_text(" ", strip=True).split())
        if len(title) < 4:
            continue
        if not any(k in title for k in KEYWORDS):
            continue
        full = urljoin(url, a["href"])
        if urlparse(full).scheme not in ("http","https"):
            continue
        # 允许同站或明确官方子域跳转
        host = urlparse(full).netloc
        if not (host == base_host or host.endswith("." + base_host) or base_host.endswith("." + host)):
            continue
        candidates.append((title[:240], full))
    # 去重
    dedup = {}
    for t,u in candidates:
        dedup[u] = t
    return [(t,u) for u,t in dedup.items()]

def save_candidate(source_name, title, url):
    fp = fingerprint(source_name, title, url)
    with connect() as con:
        con.execute(
            """INSERT OR IGNORE INTO update_candidates
               (source_name,title,url,fingerprint,status)
               VALUES (?,?,?,?, '待核验')""",
            (source_name,title,url,fp)
        )

def log_check(source_name, url, ok, message):
    with connect() as con:
        con.execute(
            "INSERT INTO source_checks(source_name,url,ok,message) VALUES (?,?,?,?)",
            (source_name,url,1 if ok else 0,message[:500])
        )

def main():
    init_db()
    sources = json.loads((BASE_DIR/"config"/"sources.json").read_text(encoding="utf-8"))
    total = 0
    for s in sorted(sources, key=lambda x: -x.get("priority",0)):
        try:
            items = scan_source(s)
            for title,url in items:
                save_candidate(s["name"], title, url)
            log_check(s["name"], s["url"], True, f"发现候选 {len(items)} 条")
            print(f"[OK] {s['name']}: {len(items)} 条候选")
            total += len(items)
        except Exception as e:
            log_check(s["name"], s["url"], False, repr(e))
            print(f"[ERR] {s['name']}: {e}")
    print(f"扫描结束，共处理 {total} 条候选链接。新链接会自动去重。")

if __name__ == "__main__":
    main()
