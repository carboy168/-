from __future__ import annotations
import ast, os, sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
files=[
    ROOT/"desktop_main.py",
    *sorted((ROOT/"desktop").rglob("*.py")),
    ROOT/"db.py",ROOT/"migrations.py",ROOT/"provider.py",ROOT/"provider_config.py",ROOT/"log_security.py",ROOT/"rag.py",ROOT/"router.py",ROOT/"project_mode.py",ROOT/"project_kb.py",ROOT/"review_engine.py",
]
bad=[]
for f in files:
    try:
        ast.parse(f.read_text(encoding="utf-8"),filename=str(f))
    except Exception as e:
        bad.append((f,e))
if bad:
    for f,e in bad:print("FAIL",f,e)
    raise SystemExit(1)
for required in [
    ROOT/"data"/"core_standards.json",ROOT/"data"/"theme_router.json",ROOT/"data"/"guangdong_overlay.json",
    ROOT/"data"/"provider_catalog.json",
    ROOT/"prompts"/"system_prompt.txt",ROOT/"assets"/"app.ico",ROOT/"installer"/"EngineeringNormAgent.spec",
    ROOT/"installer"/"EngineeringNormAgent.iss",
]:
    if not required.exists():
        print("MISSING",required);raise SystemExit(2)
print("桌面版预检通过：",len(files),"个Python文件语法有效，核心资源齐全。")


# Inno/PyInstaller build path checks
iss=(ROOT/"installer"/"EngineeringNormAgent.iss").read_text(encoding="utf-8")
spec=(ROOT/"installer"/"EngineeringNormAgent.spec").read_text(encoding="utf-8")
bat=(ROOT/"一键生成Windows安装包.bat").read_text(encoding="utf-8")
checks=[
    ("SourceDir={#SourcePath}\\..", iss),
    ('Source: "dist\\EngineeringNormAgent\\*"', iss),
    ("OutputDir=release", iss),
    ("EngineeringNormAgent.exe", iss),
    ("ROOT = Path(os.getcwd()).resolve()", spec),
    ("console=False", spec),
    ("installer\\EngineeringNormAgent.spec", bat),
]
for needle,hay in checks:
    if needle not in hay:
        print("BUILD CHECK FAIL:",needle)
        raise SystemExit(3)
print("Windows installer/build paths PASS")

workflows=list((ROOT/".github"/"workflows").glob("*.yml"))
if [p.name for p in workflows] != ["build-windows-installer.yml"]:
    print("WORKFLOW CHECK FAIL:", [p.name for p in workflows]);raise SystemExit(4)
workflow=workflows[0].read_text(encoding="utf-8")
for forbidden in ["Expand-Archive", "EngineeringNormAgent_V1.0_source.zip\" -DestinationPath", "gh release delete", "gh release create"]:
    if forbidden in workflow:
        print("WORKFLOW CHECK FAIL: forbidden legacy build/release command", forbidden);raise SystemExit(4)
print("Official source/workflow paths PASS")
