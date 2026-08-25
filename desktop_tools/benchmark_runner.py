from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engineering_knowledge.json_schema import validate_instance


MANIFEST = ROOT / "data" / "benchmark_manifest.json"
MANIFEST_SCHEMA = ROOT / "data" / "benchmark_manifest.schema.json"


def load_benchmark_manifest(path: Path | None = None) -> dict:
    manifest = json.loads((path or MANIFEST).read_text(encoding="utf-8"))
    schema = json.loads(MANIFEST_SCHEMA.read_text(encoding="utf-8"))
    validate_instance(manifest, schema)
    suite_ids = [item["suite_id"] for item in manifest["suites"]]
    if len(suite_ids) != len(set(suite_ids)):
        raise ValueError("Benchmark suite_id 重复。")
    for suite in manifest["suites"]:
        fixture_path = (ROOT / suite["fixture"]).resolve()
        try:
            fixture_path.relative_to(ROOT)
        except ValueError as exc:
            raise ValueError(f"Benchmark fixture 越出源码根目录：{suite['fixture']}") from exc
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        cases = fixture.get("cases")
        if not isinstance(cases, list) or not cases:
            raise ValueError(f"Benchmark fixture 没有 cases：{suite['fixture']}")
        case_ids = [case.get("id") for case in cases]
        if any(not value for value in case_ids) or len(case_ids) != len(set(case_ids)):
            raise ValueError(f"Benchmark case id 缺失或重复：{suite['fixture']}")
        if importlib.util.find_spec(suite["test_module"]) is None:
            raise ValueError(f"Benchmark 测试模块不存在：{suite['test_module']}")
    return manifest


def build_suite(manifest: dict | None = None) -> unittest.TestSuite:
    manifest = manifest or load_benchmark_manifest()
    loader = unittest.defaultTestLoader
    suite = unittest.TestSuite()
    for item in manifest["suites"]:
        loaded = loader.loadTestsFromName(item["test_module"])
        if loaded.countTestCases() == 0:
            raise ValueError(f"Benchmark 测试模块没有测试：{item['test_module']}")
        suite.addTests(loaded)
    return suite


def main() -> int:
    manifest = load_benchmark_manifest()
    print(f"Benchmark manifest {manifest['runner_version']}：{len(manifest['suites'])} 个 suite。")
    result = unittest.TextTestRunner(verbosity=2).run(build_suite(manifest))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())

