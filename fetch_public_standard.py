from __future__ import annotations
import json, re, hashlib, sys, os
from pathlib import Path
from urllib.parse import urljoin, urlparse, unquote
import requests
from bs4 import BeautifulSoup
from db import connect, init_db
from ingest_v2 import ingest_pdf_v2, ensure_v2_schema

BASE_DIR=Path(__file__).resolve().parent
MANIFEST=BASE_DIR/'data'/'fulltext_sources.json'
OUTDIR=Path(os.getenv('STANDARD_DOCS_DIR', str(BASE_DIR/'data'/'source_docs')))
UA="Mozilla/5.0 EngineeringNormAgent/1.0"
MAX_BYTES=100*1024*1024

def is_government_host(host):
    host=(host or "").lower().split(":")[0]
    return host.endswith(".gov.cn")

def norm(s):
    return re.sub(r"\s+","",(s or "").lower())

def score_link(text,url,item):
    blob=norm(unquote(text+" "+url))
    score=0
    for kw in item.get("expected_keywords",[]):
        k=norm(kw)
        if k and k in blob: score += 5
    code=norm(item["code"]).replace("/","")
    if code and code in blob.replace("/",""): score += 8
    if urlparse(url).path.lower().endswith(".pdf"): score += 2
    return score

def download(url,dest):
    host=urlparse(url).hostname
    if not is_government_host(host):
        raise RuntimeError(f"拒绝下载非政府域名：{host}")
    with requests.get(url,headers={"User-Agent":UA},timeout=30,stream=True,allow_redirects=True) as r:
        r.raise_for_status()
        final_host=urlparse(r.url).hostname
        if not is_government_host(final_host):
            raise RuntimeError(f"重定向到非政府域名，已拒绝：{final_host}")
        ctype=(r.headers.get("content-type") or "").lower()
        total=0
        with open(dest,"wb") as f:
            for chunk in r.iter_content(1024*128):
                if not chunk: continue
                total += len(chunk)
                if total>MAX_BYTES:
                    raise RuntimeError("文件超过100MB安全上限")
                f.write(chunk)
    head=Path(dest).read_bytes()[:5]
    if b"%PDF" not in head:
        Path(dest).unlink(missing_ok=True)
        raise RuntimeError(f"下载结果不是PDF（Content-Type={ctype}）")
    return str(r.url)

def resolve_pdf(item):
    url=item["source_page"]
    if url.lower().split("?")[0].endswith(".pdf"):
        return url
    host=urlparse(url).hostname
    if not is_government_host(host):
        raise RuntimeError("来源页面不是gov.cn，拒绝自动抓取")
    r=requests.get(url,headers={"User-Agent":UA},timeout=25)
    r.raise_for_status()
    soup=BeautifulSoup(r.text,"html.parser")
    candidates=[]
    for a in soup.find_all("a",href=True):
        full=urljoin(url,a["href"])
        h=urlparse(full).hostname
        if not is_government_host(h): continue
        txt=" ".join(a.get_text(" ",strip=True).split())
        sc=score_link(txt,full,item)
        if sc>0:
            candidates.append((sc,full,txt))
    candidates.sort(reverse=True,key=lambda x:x[0])
    if not candidates:
        raise RuntimeError("官方页面未发现可可靠匹配的PDF附件；请手工导入合法全文。")
    best=candidates[0]
    if len(candidates)>1 and candidates[1][0] >= best[0]-1 and candidates[1][1] != best[1]:
        raise RuntimeError("发现多个接近的附件候选，系统拒绝自动猜测。")
    return best[1]

def find_standard_id(code,title):
    with connect() as con:
        r=con.execute("SELECT id,status FROM standards WHERE code=? AND title=?",(code,title)).fetchone()
        return (r["id"],r["status"]) if r else (None,None)

def fetch_one(code):
    init_db(); ensure_v2_schema(); OUTDIR.mkdir(parents=True,exist_ok=True)
    pack=json.loads(MANIFEST.read_text(encoding="utf-8"))
    item=next((x for x in pack["standards"] if x["code"].replace(" ","")==code.replace(" ","")),None)
    if not item: raise RuntimeError("不在第二阶段20本白名单中")
    if item["import_mode"]=="manual_upload":
        raise RuntimeError("该规范当前策略为人工导入合法全文，不允许自动抓取。")
    sid,status=find_standard_id(item["code"],item["title"])
    if not sid: raise RuntimeError("规范元数据尚未入库，请先运行 seed_core_standards.py")
    if status not in ("现行","即将实施"):
        raise RuntimeError(f"规范数据库状态为{status}，禁止进入正式全文库")
    pdf_url=resolve_pdf(item)
    safe=re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._-]+","_",item["code"])
    dest=OUTDIR/f"{safe}.pdf"
    final_url=download(pdf_url,dest)
    info=ingest_pdf_v2(sid,str(dest),item["source_page"],final_url)
    return {"code":item["code"],"title":item["title"],"pdf":str(dest),"file_url":final_url,**info}

if __name__=="__main__":
    if len(sys.argv)<2:
        print("用法：python fetch_public_standard.py \"GB 55032-2022\"")
        raise SystemExit(2)
    try:
        print(json.dumps(fetch_one(sys.argv[1]),ensure_ascii=False,indent=2))
    except Exception as e:
        print("获取失败：",e)
        raise SystemExit(1)
