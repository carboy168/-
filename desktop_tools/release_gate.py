from __future__ import annotations

import argparse,json,os,subprocess,sys,tempfile,time
from dataclasses import asdict,dataclass
from pathlib import Path
from typing import Callable


ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))

from desktop_tools.benchmark_runner import build_suite,load_benchmark_manifest
from engineering_knowledge.contracts import load_contract_registry
from engineering_knowledge.json_schema import validate_instance,validate_json_file
from versioning import APP_VERSION


POLICY=ROOT/"data"/"release_gate.json"
POLICY_SCHEMA=ROOT/"data"/"release_gate.schema.json"
REPORT_SCHEMA=ROOT/"data"/"release_gate_report.schema.json"
PROJECT_INDEX_FIXTURE=ROOT/"data"/"project_index_integrity_benchmark.json"
PROJECT_INDEX_FIXTURE_SCHEMA=ROOT/"data"/"project_index_integrity_benchmark.schema.json"


@dataclass(frozen=True)
class GateCheck:
    check_id:str
    status:str
    detail:str
    duration_seconds:float=0.0


@dataclass(frozen=True)
class ReleaseGateReport:
    schema_version:int
    gate_version:str
    app_version:str
    status:str
    checks:list[dict]

    def to_dict(self)->dict:return asdict(self)


def load_release_policy(path:Path|None=None)->dict:
    return validate_json_file(path or POLICY,POLICY_SCHEMA)


def _version(value:str)->tuple[int,...]:return tuple(int(x) for x in value.split("."))


def _protected_changes(base_ref:str,runner:Callable[...,subprocess.CompletedProcess])->list[str]:
    commands=[]
    if base_ref:commands.append(["git","diff","--name-only",f"{base_ref}..HEAD"])
    commands.extend((["git","diff","--name-only"],["git","diff","--cached","--name-only"]))
    changed=[]
    for command in commands:
        result=runner(command,cwd=ROOT,text=True,capture_output=True,timeout=30)
        if result.returncode!=0:raise RuntimeError(result.stderr.strip() or "无法读取 Git 变更范围。")
        changed.extend(x.strip().replace("\\","/") for x in result.stdout.splitlines() if x.strip())
    return list(dict.fromkeys(changed))


def static_checks(policy:dict,*,base_ref:str="",require_clean:bool=False,runner:Callable[...,subprocess.CompletedProcess]=subprocess.run)->list[GateCheck]:
    checks=[]
    checks.append(GateCheck("application-version","pass" if _version(APP_VERSION)>=_version(policy["minimum_app_version"]) else "fail",f"APP_VERSION={APP_VERSION}"))
    registry=load_contract_registry();checks.append(GateCheck("frozen-contracts","pass",f"write={registry['current_write_version']}，冻结 Schema 哈希有效"))
    manifest=load_benchmark_manifest();suite_ids={x["suite_id"] for x in manifest["suites"]};required=set(policy["benchmark"]["required_suites"])
    benchmark_ok=manifest["runner_version"]==policy["benchmark"]["runner_version"] and required.issubset(suite_ids) and build_suite(manifest).countTestCases()>=policy["benchmark"]["minimum_test_count"]
    checks.append(GateCheck("benchmark-contract","pass" if benchmark_ok else "fail",f"runner={manifest['runner_version']}，suite={len(suite_ids)}，tests={build_suite(manifest).countTestCases()}"))
    index_fixture=validate_json_file(PROJECT_INDEX_FIXTURE,PROJECT_INDEX_FIXTURE_SCHEMA)
    index_contract=("project-fts-integrity" in required and index_fixture["benchmark_version"]=="1.4-a0"
                    and "production_database_audit_only" in index_fixture["safety_boundaries"]
                    and "no_automatic_rebuild" in index_fixture["safety_boundaries"])
    checks.append(GateCheck("project-fts-integrity-contract","pass" if index_contract else "fail",f"fixture_cases={len(index_fixture['cases'])}，production=audit-only"))
    workflow=(ROOT/".github"/"workflows"/"build-windows-installer.yml").read_text(encoding="utf-8")
    gate_pos=workflow.find("desktop_tools/release_gate.py");build_pos=workflow.find("pyinstaller --noconfirm")
    workflow_ok=gate_pos>=0 and build_pos>gate_pos and all(token not in workflow for token in ("gh release create","gh release delete"))
    checks.append(GateCheck("windows-ci-gate-order","pass" if workflow_ok else "fail","Release Gate 必须先于安装包构建且不得创建 Release。"))
    changed=_protected_changes(base_ref,runner);protected=policy["protected_paths"]
    violations=[path for path in changed if any(path==rule.rstrip("/") or path.startswith(rule) for rule in protected)]
    checks.append(GateCheck("database-schema-boundary","fail" if violations else "pass","受保护路径变更："+("、".join(violations) if violations else "无")))
    if base_ref:
        result=runner(["git","diff","--check",f"{base_ref}..HEAD"],cwd=ROOT,text=True,capture_output=True,timeout=30)
        detail=(result.stdout+result.stderr).strip()
        checks.append(GateCheck("branch-diff-check","pass" if result.returncode==0 else "fail",detail or f"已检查 {base_ref}..HEAD"))
    if require_clean:
        result=runner(["git","status","--porcelain"],cwd=ROOT,text=True,capture_output=True,timeout=30)
        clean=result.returncode==0 and not result.stdout.strip();checks.append(GateCheck("clean-worktree","pass" if clean else "fail","工作区 clean" if clean else "工作区存在未提交变更"))
    return checks


def _run_command(command:list[str],timeout:int)->GateCheck:
    started=time.monotonic();env=os.environ.copy();env["ENGINEERING_AGENT_TEST_MODE"]="1";env["NO_REAL_API"]="1"
    temp_root=ROOT/".test-tmp";temp_root.mkdir(exist_ok=True);env["TEMP"]=str(temp_root);env["TMP"]=str(temp_root)
    try:result=subprocess.run(command,cwd=ROOT,text=True,capture_output=True,timeout=timeout,env=env)
    except subprocess.TimeoutExpired:return GateCheck("","fail",f"命令超过 {timeout} 秒门禁上限。",round(time.monotonic()-started,3))
    except OSError as exc:return GateCheck("","fail",f"命令无法执行：{type(exc).__name__}",round(time.monotonic()-started,3))
    detail=(result.stdout+"\n"+result.stderr).strip()
    if len(detail)>1200:detail=detail[-1200:]
    return GateCheck(command[0] if len(command)==1 else "", "pass" if result.returncode==0 else "fail",detail,round(time.monotonic()-started,3))


def run_release_gate(*,policy:dict|None=None,base_ref:str="",require_clean:bool=False,run_commands:bool=True)->ReleaseGateReport:
    policy=policy or load_release_policy()
    try:checks=static_checks(policy,base_ref=base_ref,require_clean=require_clean)
    except Exception as exc:checks=[GateCheck("static-contracts","fail",f"静态门禁异常：{type(exc).__name__}")]
    temp_root=ROOT/".test-tmp";temp_root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=temp_root) as tmp:
        benchmark_report=Path(tmp)/"benchmark-report.json"
        if run_commands and all(x.status=="pass" for x in checks):
            for command in policy["commands"]:
                args=[x.format(python=sys.executable,benchmark_report=str(benchmark_report)) for x in command["args"]]
                item=_run_command(args,command["timeout_seconds"]);item=GateCheck(command["id"],item.status,item.detail,item.duration_seconds);checks.append(item)
                if item.status=="fail":break
            benchmark_check=next((x for x in checks if x.check_id=="unified-benchmark" and x.status=="pass"),None)
            if benchmark_check:
                try:
                    report=json.loads(benchmark_report.read_text(encoding="utf-8"));schema=json.loads((ROOT/"data"/"benchmark_report.schema.json").read_text(encoding="utf-8"));validate_instance(report,schema)
                    ready=(report["status"]=="pass" and report["passed"]>=policy["benchmark"]["minimum_test_count"]
                        and report["failures"]==0 and report["errors"]==0 and report["skipped"]==0)
                    checks.append(GateCheck("benchmark-threshold","pass" if ready else "fail",f"passed={report['passed']}，tests={report['test_count']}，skipped={report['skipped']}"))
                except Exception as exc:checks.append(GateCheck("benchmark-threshold","fail",f"Benchmark 报告缺失或无效：{type(exc).__name__}"))
    status="pass" if checks and all(x.status=="pass" for x in checks) else "fail"
    report=ReleaseGateReport(1,policy["gate_version"],APP_VERSION,status,[asdict(x) for x in checks]);validate_instance(report.to_dict(),json.loads(REPORT_SCHEMA.read_text(encoding="utf-8")))
    return report


def main(argv:list[str]|None=None)->int:
    parser=argparse.ArgumentParser(description="V1.3 发布前确定性门禁。失败时禁止进入 Windows 构建。")
    parser.add_argument("--base-ref",default="",help="检查 DB/migration 边界的对比基线。")
    parser.add_argument("--ci",action="store_true",help="CI 模式：要求 clean 工作区。")
    parser.add_argument("--static-only",action="store_true",help="只验证门禁契约，不执行测试命令。")
    parser.add_argument("--json-output",type=Path)
    args=parser.parse_args(argv);report=run_release_gate(base_ref=args.base_ref,require_clean=args.ci,run_commands=not args.static_only)
    text=json.dumps(report.to_dict(),ensure_ascii=False,indent=2)
    if args.json_output:args.json_output.parent.mkdir(parents=True,exist_ok=True);args.json_output.write_text(text+"\n",encoding="utf-8")
    print(text);return 0 if report.status=="pass" else 1


if __name__=="__main__":raise SystemExit(main())
