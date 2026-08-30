from __future__ import annotations

import io,json,subprocess,unittest
from pathlib import Path
from unittest.mock import patch

from desktop_tools.benchmark_runner import BenchmarkReport,run_benchmarks
from desktop_tools.release_gate import GateCheck,_run_command,load_release_policy,run_release_gate,static_checks
from engineering_knowledge.json_schema import validate_instance,validate_json_file


ROOT=Path(__file__).resolve().parents[1]


def _git_runner(changes="",dirty="",missing_base=False,whitespace_error=False):
    def run(command,**kwargs):
        if command[:3]==["git","status","--porcelain"]:return subprocess.CompletedProcess(command,0,dirty,"")
        if command[:3]==["git","diff","--name-only"]:
            return subprocess.CompletedProcess(command,128 if missing_base else 0,"" if missing_base else changes,"fatal: bad object" if missing_base else "")
        if command[:3]==["git","diff","--check"]:
            detail="version.ini:3: new blank line at EOF.\n" if whitespace_error else ""
            return subprocess.CompletedProcess(command,2 if whitespace_error else 0,detail,"")
        return subprocess.CompletedProcess(command,0,"","")
    return run


class ReleaseGateV13DTests(unittest.TestCase):
    def test_policy_schema_fixture_and_required_suites(self):
        policy=load_release_policy();fixture=validate_json_file(ROOT/"data"/"release_gate_benchmark.json",ROOT/"data"/"release_gate_benchmark.schema.json")
        self.assertEqual(policy["gate_version"],"1.3-d");self.assertEqual(len(policy["benchmark"]["required_suites"]),9)
        self.assertGreaterEqual(len(fixture["cases"]),9);self.assertIn("no_real_paid_api",fixture["safety_boundaries"])

    def test_benchmark_runner_produces_valid_machine_readable_report(self):
        class Passing(unittest.TestCase):
            def runTest(self):self.assertTrue(True)
        suite=unittest.TestSuite([Passing()]);manifest={"runner_version":"test","suites":[{}]}
        with patch("desktop_tools.benchmark_runner.build_suite",return_value=suite):result,report=run_benchmarks(manifest,verbosity=0,stream=io.StringIO())
        self.assertTrue(result.wasSuccessful());self.assertEqual((report.test_count,report.passed,report.status),(1,1,"pass"))
        validate_instance(report.to_dict(),json.loads((ROOT/"data"/"benchmark_report.schema.json").read_text(encoding="utf-8")))

    def test_static_release_contract_passes_for_current_source(self):
        checks=static_checks(load_release_policy(),runner=_git_runner())
        self.assertTrue(checks);self.assertTrue(all(x.status=="pass" for x in checks),checks)

    def test_database_or_migration_change_is_blocked(self):
        checks=static_checks(load_release_policy(),base_ref="baseline",runner=_git_runner("db.py\nmigrations.py\n"))
        gate=next(x for x in checks if x.check_id=="database-schema-boundary")
        self.assertEqual(gate.status,"fail");self.assertIn("db.py",gate.detail)

    def test_available_base_runs_protected_and_whitespace_diff_checks(self):
        checks=static_checks(load_release_policy(),base_ref="baseline",runner=_git_runner())
        self.assertEqual(next(x for x in checks if x.check_id=="database-schema-boundary").status,"pass")
        self.assertEqual(next(x for x in checks if x.check_id=="branch-diff-check").status,"pass")

    def test_missing_real_base_fails_closed(self):
        with self.assertRaises(RuntimeError):
            static_checks(load_release_policy(),base_ref="missing",runner=_git_runner(missing_base=True))

    def test_branch_whitespace_error_fails_release_gate(self):
        checks=static_checks(load_release_policy(),base_ref="baseline",runner=_git_runner(whitespace_error=True))
        gate=next(x for x in checks if x.check_id=="branch-diff-check")
        self.assertEqual(gate.status,"fail");self.assertIn("new blank line at EOF",gate.detail)

    def test_clean_branch_diff_passes(self):
        checks=static_checks(load_release_policy(),base_ref="baseline",runner=_git_runner())
        self.assertEqual(next(x for x in checks if x.check_id=="branch-diff-check").status,"pass")

    def test_dirty_ci_worktree_is_blocked(self):
        checks=static_checks(load_release_policy(),require_clean=True,runner=_git_runner(dirty=" M review_engine.py\n"))
        self.assertEqual(next(x for x in checks if x.check_id=="clean-worktree").status,"fail")

    def test_any_required_command_failure_fails_closed(self):
        policy=dict(load_release_policy());policy["commands"]=[{"id":"full-regression","args":["{python}","broken.py"],"timeout_seconds":1}]
        with patch("desktop_tools.release_gate.static_checks",return_value=[GateCheck("static","pass","ok")]),patch("desktop_tools.release_gate._run_command",return_value=GateCheck("","fail","failed",0.1)):
            report=run_release_gate(policy=policy)
        self.assertEqual(report.status,"fail");self.assertEqual(report.checks[-1]["check_id"],"full-regression")

    def test_timeout_and_static_exception_fail_closed(self):
        with patch("desktop_tools.release_gate.subprocess.run",side_effect=subprocess.TimeoutExpired(["slow"],1)):
            self.assertEqual(_run_command(["slow"],1).status,"fail")
        with patch("desktop_tools.release_gate.static_checks",side_effect=RuntimeError("broken contract")):
            report=run_release_gate(policy=load_release_policy(),run_commands=False)
        self.assertEqual(report.status,"fail");self.assertEqual(report.checks[0]["check_id"],"static-contracts")

    def test_benchmark_below_threshold_fails_release(self):
        policy=dict(load_release_policy());policy["commands"]=[{"id":"unified-benchmark","args":["{python}","runner.py","--json-output","{benchmark_report}"],"timeout_seconds":1}]
        def fake(args,timeout):
            path=Path(args[-1]);report=BenchmarkReport(1,"1.3-d",9,1,1,0,0,0,0.01,"pass");path.write_text(json.dumps(report.to_dict()),encoding="utf-8")
            return GateCheck("","pass","ok",0.01)
        with patch("desktop_tools.release_gate.static_checks",return_value=[GateCheck("static","pass","ok")]),patch("desktop_tools.release_gate._run_command",side_effect=fake):report=run_release_gate(policy=policy)
        self.assertEqual(report.status,"fail");self.assertEqual(report.checks[-1]["check_id"],"benchmark-threshold")

    def test_missing_benchmark_report_fails_closed(self):
        policy=dict(load_release_policy());policy["commands"]=[{"id":"unified-benchmark","args":["{python}","runner.py","--json-output","{benchmark_report}"],"timeout_seconds":1}]
        with patch("desktop_tools.release_gate.static_checks",return_value=[GateCheck("static","pass","ok")]),patch("desktop_tools.release_gate._run_command",return_value=GateCheck("","pass","ok",0.01)):
            report=run_release_gate(policy=policy)
        self.assertEqual(report.status,"fail");self.assertIn("无效",report.checks[-1]["detail"])

    def test_skipped_benchmark_cannot_satisfy_release_threshold(self):
        policy=dict(load_release_policy());policy["commands"]=[{"id":"unified-benchmark","args":["{python}","runner.py","--json-output","{benchmark_report}"],"timeout_seconds":1}]
        def fake(args,timeout):
            path=Path(args[-1]);report=BenchmarkReport(1,"1.3-d",9,101,100,0,0,1,0.01,"pass");path.write_text(json.dumps(report.to_dict()),encoding="utf-8")
            return GateCheck("","pass","ok",0.01)
        with patch("desktop_tools.release_gate.static_checks",return_value=[GateCheck("static","pass","ok")]),patch("desktop_tools.release_gate._run_command",side_effect=fake):report=run_release_gate(policy=policy)
        self.assertEqual(report.status,"fail");self.assertIn("skipped=1",report.checks[-1]["detail"])

    def test_windows_build_is_strictly_after_gate_and_never_releases(self):
        workflow=(ROOT/".github"/"workflows"/"build-windows-installer.yml").read_text(encoding="utf-8")
        self.assertLess(workflow.index("desktop_tools/release_gate.py"),workflow.index("pyinstaller --noconfirm"))
        self.assertNotIn("gh release create",workflow);self.assertNotIn("actions/create-release",workflow)

    def test_workflow_fetches_history_and_resolves_a_real_dispatch_base(self):
        workflow=(ROOT/".github"/"workflows"/"build-windows-installer.yml").read_text(encoding="utf-8")
        self.assertIn("fetch-depth: 0",workflow)
        self.assertIn("pull_request:",workflow);self.assertIn("branches: [main]",workflow)
        self.assertNotIn("pull_request_target",workflow)
        self.assertIn('$eventName = "${{ github.event_name }}"',workflow)
        self.assertIn('$baseRef = "${{ github.event.pull_request.base.sha }}"',workflow)
        self.assertIn('$baseRef = "${{ github.event.before }}"',workflow)
        self.assertIn('$resolvedBase = git rev-parse HEAD^ 2>$null',workflow)
        self.assertIn('$baseRef = $resolvedBase.Trim()',workflow)
        self.assertIn('git cat-file -e "$baseRef^{commit}"',workflow)
        self.assertNotIn("continue-on-error",workflow)

    def test_command_environment_explicitly_disables_real_api(self):
        captured={}
        def fake(command,**kwargs):captured.update(kwargs);return subprocess.CompletedProcess(command,0,"ok","")
        with patch("desktop_tools.release_gate.subprocess.run",side_effect=fake):result=_run_command(["safe-check"],1)
        self.assertEqual(result.status,"pass");self.assertEqual(captured["env"]["NO_REAL_API"],"1");self.assertEqual(captured["env"]["ENGINEERING_AGENT_TEST_MODE"],"1")
        self.assertEqual(Path(captured["env"]["TEMP"]),ROOT/".test-tmp");self.assertEqual(captured["env"]["TEMP"],captured["env"]["TMP"])


if __name__=="__main__":unittest.main()
