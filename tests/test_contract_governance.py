from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from desktop_tools.benchmark_runner import build_suite,load_benchmark_manifest
from engineering_knowledge.contracts import ContractCompatibilityError,assert_readable_version,assert_writable_version,load_contract_registry,schema_for_version
from router import log_route
from versioning import APP_VERSION,VERSION_PATTERN


ROOT = Path(__file__).resolve().parents[1]


class ContractGovernanceTests(unittest.TestCase):
    def test_single_version_source_is_consumed_by_all_build_paths(self):
        self.assertRegex(APP_VERSION,VERSION_PATTERN)
        runtime=(ROOT/"desktop"/"runtime.py").read_text(encoding="utf-8")
        spec=(ROOT/"installer"/"EngineeringNormAgent.spec").read_text(encoding="utf-8")
        iss=(ROOT/"installer"/"EngineeringNormAgent.iss").read_text(encoding="utf-8")
        workflow=(ROOT/".github"/"workflows"/"build-windows-installer.yml").read_text(encoding="utf-8")
        batch=(ROOT/"一键生成Windows安装包.bat").read_text(encoding="utf-8")
        self.assertIn("from versioning import APP_VERSION",runtime)
        for content in (spec,iss,workflow,batch):self.assertIn("version.ini",content)
        for content in (runtime,iss,workflow,batch):self.assertNotIn("工程规范智能体_V1.0_Setup",content)

    def test_frozen_schema_registry_and_fail_closed_compatibility(self):
        registry=load_contract_registry();self.assertEqual(registry["current_write_version"],"1.2-c3")
        self.assertEqual(schema_for_version("1.2-c3")["properties"]["schema_version"]["const"],"1.2-c3")
        assert_readable_version("1.2-c3");assert_writable_version("1.2-c3")
        with self.assertRaises(ContractCompatibilityError):schema_for_version("1.3")
        with self.assertRaises(ContractCompatibilityError):assert_readable_version("1.3")
        with self.assertRaises(ContractCompatibilityError):assert_writable_version("1.2-c2")

    def test_frozen_schema_hash_detects_in_place_changes(self):
        registry=json.loads((ROOT/"data"/"engineering_contracts.json").read_text(encoding="utf-8"))
        registry["schemas"]["1.2-c3"]["sha256"]="0"*64
        base=ROOT/".test-tmp";base.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as tmp:
            path=Path(tmp)/"registry.json";path.write_text(json.dumps(registry),encoding="utf-8")
            with self.assertRaises(ContractCompatibilityError):load_contract_registry(path)

    def test_benchmark_manifest_covers_topic_and_c0_through_c4(self):
        manifest=load_benchmark_manifest();ids={item["suite_id"] for item in manifest["suites"]}
        self.assertEqual(ids,{"topic-router","knowledge-c0","knowledge-c1","knowledge-c2","knowledge-c3","knowledge-c4"})
        self.assertGreater(build_suite(manifest).countTestCases(),0)

    def test_route_logging_performs_insert_without_runtime_ddl(self):
        statements=[]
        class Connection:
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def execute(self,sql,params=()):statements.append(sql);return self
        route={"question":"测试","intent":"qa","risk":"低","confidence":"高","themes":[],"primary_codes":[]}
        with patch("db.connect",return_value=Connection()):log_route(route)
        self.assertEqual(len(statements),1);self.assertIn("INSERT INTO route_logs",statements[0]);self.assertNotIn("CREATE TABLE",statements[0])


if __name__=="__main__":unittest.main()
