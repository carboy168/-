from __future__ import annotations
import ast, os, sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
files=[
    ROOT/"desktop_main.py",
    *sorted((ROOT/"desktop").rglob("*.py")),
    *sorted((ROOT/"routing").rglob("*.py")),
    *sorted((ROOT/"engineering_knowledge").rglob("*.py")),
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
    ROOT/"data"/"topic_router.json",ROOT/"data"/"topic_router.schema.json",ROOT/"data"/"topic_router_benchmark.json",
    ROOT/"data"/"engineering_objects.json",ROOT/"data"/"engineering_objects.schema.json",
    ROOT/"data"/"engineering_relations.json",ROOT/"data"/"engineering_relations.schema.json",
    ROOT/"data"/"engineering_knowledge.schema.json",ROOT/"data"/"engineering_knowledge_benchmark.json",
    ROOT/"data"/"topic_crosswalk.json",ROOT/"data"/"topic_crosswalk.schema.json",
    ROOT/"data"/"engineering_knowledge_c1_benchmark.json",
    ROOT/"data"/"requirement_claim_rules.json",ROOT/"data"/"requirement_claim_rules.schema.json",ROOT/"data"/"engineering_knowledge_c2_benchmark.json",
    ROOT/"data"/"conflict_detection_rules.json",ROOT/"data"/"conflict_detection_rules.schema.json",ROOT/"data"/"engineering_knowledge_c3_benchmark.json",
    ROOT/"data"/"engineering_knowledge_c4_benchmark.json",
    ROOT/"data"/"engineering_contracts.json",ROOT/"data"/"engineering_contracts.schema.json",
    ROOT/"data"/"benchmark_manifest.json",ROOT/"data"/"benchmark_manifest.schema.json",
    ROOT/"version.ini",ROOT/"versioning.py",ROOT/"desktop_tools"/"benchmark_runner.py",
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

from engineering_knowledge.schema_validator import validate_catalogs
validate_catalogs()
print("Engineering Knowledge JSON Schema validation PASS")

from engineering_knowledge.json_schema import validate_json_file
validate_json_file(ROOT/"data"/"topic_router.json",ROOT/"data"/"topic_router.schema.json")
print("Topic Router JSON Schema validation PASS")

from desktop_tools.benchmark_runner import load_benchmark_manifest
from versioning import APP_VERSION
load_benchmark_manifest()
if f'OutputBaseFilename=工程规范智能体_V{{#MyAppVersion}}_Setup' not in iss:
    print("VERSION CHECK FAIL: installer does not derive its filename from version.ini");raise SystemExit(5)
if 'ReadIni(SourcePath + "\\..\\version.ini"' not in iss or "version.ini" not in spec:
    print("VERSION CHECK FAIL: build configuration does not consume version.ini");raise SystemExit(5)
if APP_VERSION not in bat and "%APP_VERSION%" not in bat:
    print("VERSION CHECK FAIL: local build script does not consume the version variable");raise SystemExit(5)
print("Version and benchmark governance PASS:",APP_VERSION)
