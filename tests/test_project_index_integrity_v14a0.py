from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import project_index_integrity as integrity
from engineering_knowledge.json_schema import validate_instance, validate_json_file


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "data" / "project_index_integrity_benchmark.json"
FIXTURE_SCHEMA = ROOT / "data" / "project_index_integrity_benchmark.schema.json"
AUDIT_SCHEMA = ROOT / "data" / "project_index_audit_report.schema.json"
CLEANUP_SCHEMA = ROOT / "data" / "project_index_cleanup_report.schema.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _create_database(path: Path, case: dict) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE project_files(id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL);
            CREATE TABLE project_file_chunks(
                id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL, file_id INTEGER NOT NULL
            );
            CREATE VIRTUAL TABLE project_file_chunks_fts USING fts5(
                search_tokens, chunk_id UNINDEXED, project_id UNINDEXED,
                file_id UNINDEXED, tokenize='unicode61'
            );
            """
        )
        for row in case["files"]:
            connection.execute(
                "INSERT INTO project_files(id,project_id) VALUES(?,?)",
                (row["id"], row["project_id"]),
            )
        for row in case["chunks"]:
            connection.execute(
                "INSERT INTO project_file_chunks(id,project_id,file_id) VALUES(?,?,?)",
                (row["id"], row["project_id"], row["file_id"]),
            )
        for row in case["fts_rows"]:
            connection.execute(
                "INSERT INTO project_file_chunks_fts(rowid,search_tokens,chunk_id,project_id,file_id) "
                "VALUES(?,?,?,?,?)",
                (
                    row["rowid"],
                    "测试 索引 内容",
                    row["chunk_id"],
                    row["project_id"],
                    row["file_id"],
                ),
            )
        connection.commit()
    finally:
        connection.close()


def _fixture() -> dict:
    return validate_json_file(FIXTURE_PATH, FIXTURE_SCHEMA)


def _case(case_id: str) -> dict:
    return next(case for case in _fixture()["cases"] if case["id"] == case_id)


def _assert_expected(test: unittest.TestCase, report: integrity.ProjectIndexAuditReport, expected: dict) -> None:
    test.assertEqual(report.is_clean, expected["is_clean"])
    test.assertEqual(
        [row["fts_rowid"] for row in report.orphan_fts_rows],
        expected["orphan_fts_rowids"],
    )
    test.assertEqual(report.missing_chunk_ids, expected["missing_chunk_ids"])
    test.assertEqual(
        [row["chunk_id"] for row in report.duplicate_chunks],
        expected["duplicate_chunk_ids"],
    )
    test.assertEqual(
        [row["fts_rowid"] for row in report.malformed_fts_rows],
        expected["malformed_fts_rowids"],
    )
    test.assertEqual(
        [row["fts_rowid"] for row in report.dangling_fts_rows],
        expected["dangling_fts_rowids"],
    )


class ProjectIndexIntegrityV14A0Tests(unittest.TestCase):
    def setUp(self) -> None:
        (ROOT / ".test-tmp").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / ".test-tmp")
        self.addCleanup(self.temp.cleanup)
        self.temp_dir = Path(self.temp.name)

    def make_database(self, case_id: str) -> Path:
        path = self.temp_dir / f"{case_id}.db"
        _create_database(path, _case(case_id))
        return path

    def test_json_schema_and_fixture_load(self):
        fixture = _fixture()
        self.assertEqual(fixture["benchmark_version"], "1.4-a0")
        self.assertGreaterEqual(len(fixture["cases"]), 6)
        self.assertIn("production_database_audit_only", fixture["safety_boundaries"])

    def test_fixture_cases_report_exact_machine_readable_ids(self):
        audit_schema = json.loads(AUDIT_SCHEMA.read_text(encoding="utf-8"))
        for case in _fixture()["cases"]:
            with self.subTest(case=case["id"]):
                path = self.temp_dir / f"fixture-{case['id']}.db"
                _create_database(path, case)
                report = integrity.audit_project_index(path)
                _assert_expected(self, report, case["expected"])
                validate_instance(report.to_dict(), audit_schema)

    def test_clean_index(self):
        report = integrity.audit_project_index(self.make_database("clean-index"))
        self.assertTrue(report.is_clean)
        self.assertEqual((report.chunk_count, report.fts_row_count), (2, 2))

    def test_orphan_fts_row(self):
        report = integrity.audit_project_index(self.make_database("orphan-fts-row"))
        self.assertEqual(report.orphan_fts_rows[0]["chunk_id"], 161)
        self.assertEqual(report.orphan_fts_rows[0]["fts_rowid"], 161)

    def test_missing_fts_row(self):
        report = integrity.audit_project_index(self.make_database("missing-fts-row"))
        self.assertEqual(report.missing_chunk_ids, [2])

    def test_duplicate_malformed_and_dangling_are_distinct(self):
        report = integrity.audit_project_index(
            self.make_database("duplicate-malformed-dangling")
        )
        self.assertEqual(report.duplicate_chunks, [{"chunk_id": 1, "fts_rowids": [101, 102]}])
        self.assertEqual(report.malformed_fts_rows[0]["fts_rowid"], 103)
        self.assertEqual(report.dangling_fts_rows[0]["fts_rowid"], 104)
        self.assertEqual(report.missing_chunk_ids, [2])

    def test_historical_161_162_stale_rows_are_reproduced(self):
        report = integrity.audit_project_index(
            self.make_database("multiple-orphans-161-162")
        )
        self.assertEqual(
            [(row["fts_rowid"], row["chunk_id"]) for row in report.orphan_fts_rows],
            [(161, 161), (162, 162)],
        )
        self.assertEqual((report.chunk_count, report.fts_row_count), (2, 4))

    def test_targeted_cleanup_restores_one_to_one_mapping(self):
        path = self.make_database("multiple-orphans-161-162")
        report = integrity.cleanup_orphan_fts_rows(
            path, scope=integrity.ISOLATED_COPY_SCOPE
        )
        self.assertEqual(report.removed_fts_rowids, [161, 162])
        self.assertEqual(report.status, "clean")
        self.assertTrue(report.after["is_clean"])
        self.assertEqual(report.after["chunk_count"], report.after["fts_row_count"])
        validate_instance(
            report.to_dict(), json.loads(CLEANUP_SCHEMA.read_text(encoding="utf-8"))
        )

    def test_cleanup_is_idempotent(self):
        path = self.make_database("multiple-orphans-161-162")
        first = integrity.cleanup_orphan_fts_rows(path, scope=integrity.ISOLATED_COPY_SCOPE)
        second = integrity.cleanup_orphan_fts_rows(path, scope=integrity.ISOLATED_COPY_SCOPE)
        self.assertEqual(first.removed_fts_rowids, [161, 162])
        self.assertEqual(second.removed_fts_rowids, [])
        self.assertEqual(second.before["database_sha256"], second.after["database_sha256"])
        self.assertTrue(second.after["is_clean"])

    def test_cleanup_deletes_only_orphans(self):
        case = _case("duplicate-malformed-dangling")
        case = json.loads(json.dumps(case))
        case["fts_rows"].append(
            {"rowid": 161, "chunk_id": "161", "project_id": "1", "file_id": "10"}
        )
        path = self.temp_dir / "mixed-issues.db"
        _create_database(path, case)
        result = integrity.cleanup_orphan_fts_rows(
            path, scope=integrity.ISOLATED_COPY_SCOPE
        )
        self.assertEqual(result.removed_fts_rowids, [161])
        self.assertEqual(result.status, "remaining_issues")
        self.assertEqual(result.after["duplicate_chunks"][0]["fts_rowids"], [101, 102])
        self.assertEqual(result.after["malformed_fts_rows"][0]["fts_rowid"], 103)
        self.assertEqual(result.after["dangling_fts_rows"][0]["fts_rowid"], 104)

    def test_cleanup_failure_rolls_back(self):
        path = self.make_database("multiple-orphans-161-162")
        original = integrity.audit_project_index_connection
        calls = 0

        def fail_verification(connection, *, database_label="connection"):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("injected verification failure")
            return original(connection, database_label=database_label)

        with patch(
            "project_index_integrity.audit_project_index_connection",
            side_effect=fail_verification,
        ):
            with self.assertRaisesRegex(RuntimeError, "injected"):
                integrity.cleanup_orphan_fts_rows(
                    path, scope=integrity.ISOLATED_COPY_SCOPE
                )
        report = integrity.audit_project_index(path)
        self.assertEqual(
            [row["fts_rowid"] for row in report.orphan_fts_rows], [161, 162]
        )

    def test_audit_is_read_only(self):
        path = self.make_database("multiple-orphans-161-162")
        before = (_sha256(path), path.stat().st_size, path.stat().st_mtime_ns)
        report = integrity.audit_project_index(path)
        after = (_sha256(path), path.stat().st_size, path.stat().st_mtime_ns)
        self.assertEqual(before, after)
        self.assertEqual(report.operation, "audit")

    def test_production_mode_blocks_cleanup_even_for_isolated_database(self):
        path = self.make_database("orphan-fts-row")
        with self.assertRaises(integrity.ProjectIndexCleanupBlocked):
            integrity.cleanup_orphan_fts_rows(path)
        with patch.object(integrity, "DB_PATH", path):
            with self.assertRaises(integrity.ProjectIndexCleanupBlocked):
                integrity.cleanup_orphan_fts_rows(
                    path, scope=integrity.ISOLATED_COPY_SCOPE
                )
        report = integrity.audit_project_index(path)
        self.assertEqual([row["fts_rowid"] for row in report.orphan_fts_rows], [161])


if __name__ == "__main__":
    unittest.main()
