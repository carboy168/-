from __future__ import annotations
import os, sys, tempfile, shutil
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
tmp=Path(tempfile.mkdtemp(prefix="ena_desktop_smoke_"))
try:
    os.environ["DATABASE_PATH"]=str(tmp/"norms.db")
    os.environ["PROJECT_DATA_DIR"]=str(tmp/"projects")
    os.environ["STANDARD_DOCS_DIR"]=str(tmp/"standards")
    os.chdir(ROOT)
    from db import init_db, connect
    init_db()
    import seed_core_standards
    seed_core_standards.main()
    from project_mode import ensure_project_schema, save_project, set_active_project, get_active_project, build_project_overlay
    ensure_project_schema()
    pid=save_project({"name":"桌面版烟雾测试","province":"广东省","city":"茂名市","building_type":"公共建筑","project_nature":"室内装修","phase":"施工","reference_date":"2026-08-20","scopes":["装饰装修","消防","防水"],"fire_change":True})
    set_active_project(pid)
    p=get_active_project()
    assert p and p["name"]=="桌面版烟雾测试"
    from router import route_question
    route=route_question("卫生间闭水试验怎么做")
    overlay=build_project_overlay("卫生间闭水试验怎么做",route,p)
    assert overlay["enabled"]
    from project_kb import ensure_project_kb_schema, ingest_project_file, search_project_chunks
    ensure_project_kb_schema()
    sample=tmp/"会审回复.txt";sample.write_text("卫生间二次排水由总包负责施工，渗漏整改完成后移交。",encoding="utf-8")
    ingest_project_file(pid,str(sample),"图纸会审/设计回复")
    hits=search_project_chunks(pid,"二次排水 总包",limit=5)
    assert hits
    print("Desktop backend smoke test PASS")
finally:
    shutil.rmtree(tmp,ignore_errors=True)
