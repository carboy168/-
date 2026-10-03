from __future__ import annotations

import errno
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from pypdf import PdfWriter

import db
import project_kb
from engineering_knowledge.json_schema import validate_instance, validate_json_file
from pdf_support import PDF_PASSWORD_MESSAGE
from project_index_integrity import audit_project_index
from project_mode import ensure_project_schema, save_project


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "data" / "project_batch_import_benchmark.json"
FIXTURE_SCHEMA = ROOT / "data" / "project_batch_import_benchmark.schema.json"
RESULT_SCHEMA = ROOT / "data" / "project_batch_import.schema.json"


class ProjectBatchImportV14A1Tests(unittest.TestCase):
    def setUp(self):
        base = ROOT / ".test-tmp"
        base.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base)
        self.root = Path(self.temp.name)
        self.old_db = db.DB_PATH
        self.old_project_dir = project_kb.PROJECT_DIR
        db.DB_PATH = self.root / "batch.sqlite"
        os.environ["DATABASE_PATH"] = str(db.DB_PATH)
        project_kb.PROJECT_DIR = self.root / "projects"
        db.init_db()
        ensure_project_schema()
        project_kb.ensure_project_kb_schema()
        self.project_id = save_project({"name": "A1 批次导入测试项目"})
        self.fixture = validate_json_file(FIXTURE, FIXTURE_SCHEMA)
        self.result_schema = json.loads(RESULT_SCHEMA.read_text(encoding="utf-8"))

    def tearDown(self):
        if not getattr(self, 'preexisting_malformed', False):
            self.assertTrue(audit_project_index(db.DB_PATH).is_clean)
        db.DB_PATH = self.old_db
        os.environ["DATABASE_PATH"] = str(self.old_db)
        project_kb.PROJECT_DIR = self.old_project_dir
        self.temp.cleanup()

    def _case(self, case_id: str) -> dict:
        return next(item for item in self.fixture["cases"] if item["id"] == case_id)

    def _text(self, name: str, content: str | None = None) -> Path:
        path = self.root / name
        path.write_text(content or f"{name} 项目文件导入测试内容足够形成全文索引", encoding="utf-8")
        return path

    def _bad_docx(self, name: str = "损坏文件.docx") -> Path:
        path = self.root / name
        path.write_bytes(b"not-a-valid-office-zip")
        return path

    def _password_pdf(self) -> Path:
        path = self.root / "需要密码.pdf"
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        writer.encrypt("required-password", algorithm="AES-256")
        with path.open("wb") as stream:
            writer.write(stream)
        return path

    def _run(self, paths: list[Path]):
        result = project_kb.ingest_project_files_batch(
            self.project_id, [str(path) for path in paths], "施工图/设计图纸"
        )
        validate_instance(result.to_dict(), self.result_schema)
        return result

    def _assert_case(self, case_id: str, result):
        expected = self._case(case_id)
        self.assertEqual(result.status, expected["expected_status"])
        self.assertEqual(result.success_count, expected["success_count"])
        self.assertEqual(result.failure_count, expected["failure_count"])
        outcomes = {item.outcome for item in result.files}
        self.assertTrue(set(expected["required_outcomes"]).issubset(outcomes))
        report = audit_project_index(db.DB_PATH)
        self.assertEqual(report.is_clean, expected["fts_clean"], report.to_dict())
        refresh = Mock()
        project_kb.present_batch_import_result(
            result, refresh=refresh, set_result_text=Mock(),
            show_information=Mock(), show_warning=Mock())
        self.assertEqual(refresh.call_count, expected['ui_refresh_count'])
        return report

    def _database_counts(self) -> dict[str, int]:
        with db.connect() as con:
            return {
                "files": con.execute("SELECT COUNT(*) FROM project_files").fetchone()[0],
                "chunks": con.execute("SELECT COUNT(*) FROM project_file_chunks").fetchone()[0],
                "fts": con.execute("SELECT COUNT(*) FROM project_file_chunks_fts").fetchone()[0],
                "requirements": con.execute("SELECT COUNT(*) FROM project_requirements").fetchone()[0],
                "reviews": con.execute("SELECT COUNT(*) FROM project_reviews").fetchone()[0],
                "findings": con.execute("SELECT COUNT(*) FROM review_findings").fetchone()[0],
            }

    def _stored_files(self) -> list[Path]:
        return [path for path in project_kb.PROJECT_DIR.rglob("*") if path.is_file()]

    def test_json_schema_fixture_and_batch_result_contract(self):
        self.assertEqual(self.fixture["benchmark_version"], "1.4-a1")
        self.assertGreaterEqual(len(self.fixture["cases"]), 10)
        self.assertIn("per_file_atomic_commit", self.fixture["safety_boundaries"])
        result = self._run([])
        self.assertEqual(result.status, "empty")
        self.assertFalse(result.ui_refresh_required)

    def test_all_files_succeed_with_complete_ids(self):
        result = self._run([self._text("一.txt"), self._text("二.txt")])
        self._assert_case("all-success", result)
        self.assertEqual(result.total_files, 2)
        self.assertTrue(all(item.file_id for item in result.files))
        self.assertTrue(result.ui_refresh_required)

    def test_first_failure_does_not_block_later_success(self):
        missing = self.root / "不存在.pdf"
        good = self._text("后续成功.txt")
        result = self._run([missing, good])
        self._assert_case("first-failure-continues", result)
        self.assertEqual([item.status for item in result.files], ["failed", "success"])
        self.assertIn("不存在", result.files[0].failure_reason)

    def test_middle_failure_preserves_prior_and_later_success(self):
        result = self._run([
            self._text("先成功.txt"), self._bad_docx(), self._text("后成功.txt")
        ])
        self._assert_case("middle-failure-continues", result)
        self.assertEqual([item.status for item in result.files], ["success", "failed", "success"])
        self.assertEqual(self._database_counts()["files"], 2)
        self.assertEqual(len(self._stored_files()), 2)

    def test_multiple_file_failures_are_reported_together(self):
        result = self._run([
            self.root / "缺失.pdf", self._bad_docx("另一个损坏.docx"), self._text("仍然成功.txt")
        ])
        self._assert_case("multiple-failures", result)
        self.assertEqual(len([item for item in result.files if item.failure_reason]), 2)
        self.assertIn("损坏", result.files[1].failure_reason)

    def test_duplicate_content_updates_one_record_without_duplicate_index_or_file(self):
        first = self._text("原名称.txt", "相同文件内容用于验证重复导入不制造重复索引")
        second = self._text("另一个名称.txt", "相同文件内容用于验证重复导入不制造重复索引")
        result = self._run([first, second])
        report = self._assert_case("duplicate-safe-update", result)
        self.assertEqual([item.outcome for item in result.files], ["created", "updated_existing"])
        self.assertEqual(result.files[0].file_id, result.files[1].file_id)
        self.assertEqual(self._database_counts()["files"], 1)
        self.assertEqual(len(self._stored_files()), 1)
        self.assertEqual(report.chunk_count, report.fts_row_count)

    def test_duplicate_reindex_failure_restores_previous_record_and_index(self):
        source = self._text("重复回滚.txt", "重复文件重建索引失败后必须保留原有完整索引")
        first = self._run([source])
        original_id = first.files[0].file_id
        before_counts = self._database_counts()
        before_files = [(path.name, path.read_bytes()) for path in self._stored_files()]
        with patch("project_kb._insert_project_chunk_fts", side_effect=RuntimeError("reindex failed")):
            failed = self._run([source])
        self._assert_case("duplicate-reindex-failure-rollback", failed)
        self.assertEqual(failed.files[0].status, "failed")
        self.assertEqual(self._database_counts(), before_counts)
        self.assertEqual([(path.name, path.read_bytes()) for path in self._stored_files()], before_files)
        with db.connect() as con:
            self.assertEqual(con.execute("SELECT id FROM project_files").fetchone()[0], original_id)

    def test_duplicate_reindex_does_not_delete_preexisting_malformed_fts_row(self):
        self.preexisting_malformed = True
        source = self._text("精确匹配.txt", "重复导入只能删除所属文本块的精确索引行")
        first = self._run([source])
        with db.connect() as con:
            chunk_id = con.execute("SELECT id FROM project_file_chunks").fetchone()[0]
            con.execute(
                "INSERT INTO project_file_chunks_fts(search_tokens,chunk_id,project_id,file_id) "
                "VALUES(?,?,?,?)",
                ("历史异常", f"{chunk_id}malformed", str(self.project_id), str(first.files[0].file_id)),
            )
            malformed_rowid = con.execute(
                "SELECT rowid FROM project_file_chunks_fts WHERE search_tokens=?", ("历史异常",)
            ).fetchone()[0]
        second = self._run([source])
        self.assertEqual(second.files[0].outcome, "updated_existing")
        with db.connect() as con:
            self.assertIsNotNone(con.execute(
                "SELECT rowid FROM project_file_chunks_fts WHERE rowid=?", (malformed_rowid,)
            ).fetchone())
        report = audit_project_index(db.DB_PATH)
        self.assertEqual([item["fts_rowid"] for item in report.malformed_fts_rows], [malformed_rowid])
        self.assertEqual(report.issue_counts["orphan_fts_rows"], 0)
        self.assertEqual(report.issue_counts["duplicate_chunks"], 0)

    def test_password_pdf_rolls_back_every_side_effect(self):
        result = self._run([self._password_pdf()])
        self._assert_case("password-pdf-rollback", result)
        self.assertIn(PDF_PASSWORD_MESSAGE, result.files[0].failure_reason)
        self.assertEqual(self._stored_files(), [])
        self.assertEqual(self._database_counts(), {
            "files": 0, "chunks": 0, "fts": 0,
            "requirements": 0, "reviews": 0, "findings": 0,
        })

    def test_corrupt_office_file_is_friendly_and_leaves_no_residue(self):
        result = self._run([self._bad_docx()])
        self._assert_case("corrupt-office-rollback", result)
        self.assertIn("损坏", result.files[0].failure_reason)
        self.assertEqual(self._stored_files(), [])
        self.assertEqual(self._database_counts()["files"], 0)

    def test_parser_exception_rolls_back_only_that_file_and_continues(self):
        broken = self._text("解析异常.txt")
        good = self._text("解析后续成功.txt")
        original = project_kb.extract_file_text

        def extract(path):
            if Path(path).name == broken.name:
                raise RuntimeError("injected parser exception")
            return original(path)

        with patch("project_kb.extract_file_text", side_effect=extract):
            result = self._run([broken, good])
        self._assert_case("parser-exception-continues", result)
        self.assertEqual([item.status for item in result.files], ["failed", "success"])
        self.assertEqual(self._database_counts()["files"], 1)
        self.assertEqual(len(self._stored_files()), 1)

    def test_fts_write_failure_rolls_back_file_and_preserves_siblings(self):
        failed = self._text("FTS失败.txt")
        good = self._text("FTS后续成功.txt")
        original = project_kb._insert_project_chunk_fts
        calls = 0

        def insert(con, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("injected FTS write failure")
            return original(con, **kwargs)

        with patch("project_kb._insert_project_chunk_fts", side_effect=insert):
            result = self._run([failed, good])
        self._assert_case("fts-write-failure-rollback", result)
        self.assertEqual([item.status for item in result.files], ["failed", "success"])
        with db.connect() as con:
            names = [row[0] for row in con.execute("SELECT original_name FROM project_files")]
        self.assertEqual(names, [good.name])
        self.assertEqual(len(self._stored_files()), 1)

    def test_system_database_failure_stops_remaining_without_rolling_back_success(self):
        first = self._text("先提交.txt")
        fatal = self._text("数据库异常.txt")
        pending = self._text("不应处理.txt")
        original = project_kb.ingest_project_file
        seen = []

        def ingest(project_id, source_path, doc_type, **kwargs):
            seen.append(Path(source_path).name)
            if Path(source_path).name == fatal.name:
                raise sqlite3.DatabaseError("database disk image is malformed")
            return original(project_id, source_path, doc_type, **kwargs)

        with patch("project_kb.ingest_project_file", side_effect=ingest):
            result = self._run([first, fatal, pending])
        self._assert_case("system-failure-stops-remaining", result)
        self.assertEqual(seen, [first.name, fatal.name])
        self.assertEqual(result.files[2].outcome, "not_processed")
        self.assertIsNotNone(result.fatal_error)
        self.assertEqual(self._database_counts()["files"], 1)

    def test_ui_refreshes_once_after_batch_and_keeps_success_failure_list(self):
        result = self._run([self._text("显示成功.txt"), self.root / "显示失败.pdf"])
        self._assert_case("ui-final-refresh", result)
        refresh=Mock();set_text=Mock();information=Mock();warning=Mock()
        summary=project_kb.present_batch_import_result(
            result,refresh=refresh,set_result_text=set_text,
            show_information=information,show_warning=warning)
        refresh.assert_called_once_with()
        set_text.assert_called_once_with(summary)
        self.assertIn("显示成功.txt", summary)
        self.assertIn("显示失败.pdf", summary)
        self.assertIn("成功 1 个，失败 1 个", summary)
        warning.assert_called_once()
        information.assert_not_called()

    def test_corrupt_pdf_is_readable_and_next_file_succeeds(self):
        bad = self.root / 'broken.pdf'
        bad.write_bytes(b'not a PDF')
        result = self._run([bad, self._text('after-pdf.txt')])
        self._assert_case('corrupt-pdf-continues', result)
        self.assertEqual((result.success_count, result.failure_count), (1, 1))
        self.assertIn('PDF', result.files[0].failure_reason)
        self.assertIn('解析', result.files[0].failure_reason)
        self.assertEqual(len(self._stored_files()), 1)

    def test_partial_chunk_and_fts_writes_are_rolled_back(self):
        source = self._text('many-chunks.txt', ''.join(f'第{i}段工程内容。' for i in range(800)))
        original = project_kb._insert_project_chunk_fts
        calls = 0
        def insert(con, **kwargs):
            nonlocal calls
            calls += 1
            original(con, **kwargs)
            if calls == 2:
                raise sqlite3.OperationalError('injected FTS failure after write')
        with patch('project_kb._insert_project_chunk_fts', side_effect=insert):
            result = self._run([source])
        self.assertEqual(result.failure_count, 1)
        self.assertEqual(calls, 2)
        self._assert_case('partial-fts-rollback', result)
        self.assertEqual(self._database_counts()['chunks'], 0)
        self.assertEqual(self._database_counts()['fts'], 0)
        self.assertEqual(self._database_counts()['files'], 0)
        self.assertEqual(self._stored_files(), [])

    def test_partial_copy_is_removed_on_failure(self):
        source = self._text('copy-fails.txt')
        def copy(src, dest):
            Path(dest).write_bytes(b'partial')
            raise OSError('copy interrupted')
        with patch('project_kb.shutil.copy2', side_effect=copy):
            result = self._run([source])
        self._assert_case('partial-copy-rollback', result)
        self.assertEqual(result.failure_count, 1)
        self.assertEqual(self._database_counts()['files'], 0)
        self.assertEqual(self._stored_files(), [])

    def test_ui_new_batch_replaces_old_failure_list(self):
        first = self._run([self.root / 'old-failure.pdf'])
        second = self._run([self._text('new-success.txt')])
        self._assert_case('ui-replaces-failures', second)
        state = {}
        refresh = Mock()
        for result in (first, second):
            project_kb.present_batch_import_result(
                result, refresh=refresh,
                set_result_text=lambda text: state.update(text=text),
                show_information=Mock(), show_warning=Mock())
        self.assertEqual(refresh.call_count, 2)
        self.assertIn('new-success.txt', state['text'])
        self.assertNotIn('old-failure.pdf', state['text'])

    def test_cleanup_failures_preserve_primary_fault_and_attempt_both_paths(self):
        source = self._text('cleanup-primary.txt')
        primary = OSError(errno.ENOSPC, 'primary disk full')
        attempts = []

        def unlink(path, **kwargs):
            attempts.append(path)
            raise PermissionError(errno.EACCES, 'cleanup denied', str(path))

        with patch('project_kb._insert_project_chunk_fts', side_effect=primary), patch.object(Path, 'unlink', unlink):
            with self.assertRaises(OSError) as caught:
                project_kb.ingest_project_file(self.project_id, str(source), '其他')
        self.assertIs(caught.exception, primary)
        self.assertEqual(len(attempts), 2)
        self.assertIn('.importing-', attempts[0].name)
        self.assertNotIn('.importing-', attempts[1].name)
        self.assertTrue(attempts[1].exists())
        reason = project_kb._friendly_import_failure(primary)
        self.assertIn('primary disk full', reason)
        self.assertIn('清理未完成', reason)
        self.assertNotIn('已完整回滚', reason)
        for path in attempts:
            self.assertIn(str(path), reason)
        self.assertTrue(project_kb._is_system_fatal_import_error(primary))
        self.assertEqual(self._database_counts()['files'], 0)

    def test_cleanup_failure_stops_batch_and_preserves_preceding_success(self):
        first, failed, pending = [self._text(name) for name in ('kept.txt', 'residue.txt', 'pending.txt')]
        insert = project_kb._insert_project_chunk_fts
        unlink = Path.unlink
        attempts = []
        calls = 0

        def fail_insert(con, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError(errno.ENOSPC, 'primary disk full')
            return insert(con, **kwargs)

        def fail_unlink(path, **kwargs):
            if 'residue.txt' in path.name:
                attempts.append(path)
                raise PermissionError('cannot clean')
            return unlink(path, **kwargs)

        with patch('project_kb._insert_project_chunk_fts', side_effect=fail_insert), patch.object(Path, 'unlink', fail_unlink):
            result = self._run([first, failed, pending])
        self.assertEqual(result.status, 'system_failure')
        self.assertEqual([item.outcome for item in result.files], ['created', 'failed', 'not_processed'])
        self.assertEqual(result.files[1].error_type, 'OSError')
        self.assertEqual(len(attempts), 2)
        self.assertIn('primary disk full', result.fatal_error)
        self.assertIn(str(attempts[1]), result.fatal_error)
        self.assertEqual(self._database_counts()['files'], 1)
        self.assertEqual(len(self._stored_files()), 2)
        kept = project_kb.get_project_file(result.files[0].file_id)
        self.assertEqual(Path(kept['stored_path']).read_bytes(), first.read_bytes())

    def test_recoverable_primary_with_partial_copy_cleanup_failure_is_fatal(self):
        source = self._text('partial-residue.txt')
        pending = self._text('after-residue.txt')
        residual = []

        def copy(src, dest):
            Path(dest).write_bytes(b'partial')
            residual.append(Path(dest))
            raise RuntimeError('copy interrupted')

        with patch('project_kb.shutil.copy2', side_effect=copy), patch.object(Path, 'unlink', side_effect=PermissionError('denied')):
            result = self._run([source, pending])
        self.assertEqual(result.status, 'system_failure')
        self.assertEqual(result.files[1].outcome, 'not_processed')
        self.assertEqual(result.files[0].error_type, 'RuntimeError')
        self.assertEqual(residual[0].read_bytes(), b'partial')
        self.assertIn(str(residual[0]), result.fatal_error)
        self.assertEqual(self._database_counts()['files'], 0)

    def test_failed_temp_cleanup_does_not_prevent_destination_cleanup(self):
        source = self._text('second-cleanup.txt')
        original = Path.unlink
        attempts = []

        def unlink(path, **kwargs):
            attempts.append(path)
            if '.importing-' in path.name:
                raise PermissionError('temp cleanup denied')
            return original(path, **kwargs)

        with patch('project_kb._insert_project_chunk_fts', side_effect=RuntimeError('index failed')), patch.object(Path, 'unlink', unlink):
            result = self._run([source])
        self.assertEqual(len(attempts), 2)
        self.assertFalse(attempts[1].exists())
        self.assertEqual(self._stored_files(), [])
        self.assertEqual(result.status, 'system_failure')

    def test_target_storage_errno_failures_stop_remaining_inputs(self):
        for boundary in ('mkdir', 'copy', 'replace'):
            for code in (errno.EIO, errno.EROFS):
                with self.subTest(boundary=boundary, code=code):
                    token = f'{boundary}-{code}'
                    first, failed, pending = [self._text(f'{token}-{name}.txt') for name in ('kept', 'failed', 'pending')]
                    original_ingest = project_kb.ingest_project_file
                    target = {'mkdir': 'project_kb.Path.mkdir', 'copy': 'project_kb.shutil.copy2', 'replace': 'project_kb.os.replace'}[boundary]

                    def ingest(project_id, source_path, doc_type, **kwargs):
                        if source_path == str(failed):
                            with patch(target, side_effect=OSError(code, 'target unavailable')):
                                return original_ingest(project_id, source_path, doc_type, **kwargs)
                        return original_ingest(project_id, source_path, doc_type, **kwargs)

                    before = self._database_counts()['files']
                    with patch('project_kb.ingest_project_file', side_effect=ingest):
                        result = self._run([first, failed, pending])
                    self.assertEqual(result.status, 'system_failure')
                    self.assertEqual([item.outcome for item in result.files], ['created', 'failed', 'not_processed'])
                    self.assertEqual(self._database_counts()['files'], before + 1)
                    self.assertEqual(len(self._stored_files()), before + 1)

    def test_copy_error_naming_both_paths_remains_system_fatal(self):
        source = self._text('dual-path-copy.txt')
        pending = self._text('dual-path-pending.txt')

        def copy(src, dest):
            raise OSError(errno.EIO, 'sendfile failed', str(src), None, str(dest))

        with patch('project_kb.shutil.copy2', side_effect=copy):
            result = self._run([source, pending])
        self.assertEqual(result.status, 'system_failure')
        self.assertEqual(result.files[1].outcome, 'not_processed')
        self.assertEqual(self._stored_files(), [])

    def test_source_read_and_copy_source_errors_remain_recoverable(self):
        for boundary in ('read', 'copy'):
            for code in (errno.ENOENT, errno.EACCES, errno.EIO):
                with self.subTest(boundary=boundary, code=code):
                    bad = self._text(f'source-{boundary}-{code}.txt')
                    good = self._text(f'after-source-{boundary}-{code}.txt')
                    read = Path.read_bytes
                    copy = project_kb.shutil.copy2

                    def fail_read(path):
                        if path == bad:raise OSError(code, 'source unavailable')
                        return read(path)

                    def fail_copy(src, dest):
                        if src == bad:raise OSError(code, 'source unavailable', str(src))
                        return copy(src, dest)

                    target = patch.object(Path, 'read_bytes', fail_read) if boundary == 'read' else patch('project_kb.shutil.copy2', side_effect=fail_copy)
                    with target:
                        result = self._run([bad, good])
                    self.assertEqual(result.status, 'partial_success')
                    self.assertEqual([item.status for item in result.files], ['failed', 'success'])
                    self.assertIsNone(result.fatal_error)

    def test_sqlite_primary_and_extended_availability_codes_are_fatal(self):
        codes = (sqlite3.SQLITE_NOTADB, sqlite3.SQLITE_IOERR | (3 << 8), sqlite3.SQLITE_READONLY, sqlite3.SQLITE_FULL)
        for code in codes:
            with self.subTest(code=code):
                first, failed, pending = [self._text(f'sqlite-{code}-{name}.txt') for name in ('kept', 'failed', 'pending')]
                original = project_kb._insert_project_chunk_fts
                calls = 0

                def insert(con, **kwargs):
                    nonlocal calls
                    calls += 1
                    if calls == 2:
                        exc = sqlite3.DatabaseError('localized database failure')
                        exc.sqlite_errorcode = code
                        raise exc
                    return original(con, **kwargs)

                before = self._database_counts()['files']
                with patch('project_kb._insert_project_chunk_fts', side_effect=insert):
                    result = self._run([first, failed, pending])
                self.assertEqual(result.status, 'system_failure')
                self.assertEqual(result.files[2].outcome, 'not_processed')
                self.assertEqual(self._database_counts()['files'], before + 1)
                self.assertEqual(len(self._stored_files()), before + 1)
        self.assertTrue(project_kb._is_system_fatal_import_error(sqlite3.DatabaseError('file is not a database')))
        ordinary = sqlite3.IntegrityError('constraint failure')
        ordinary.sqlite_errorcode = sqlite3.SQLITE_CONSTRAINT
        self.assertFalse(project_kb._is_system_fatal_import_error(ordinary))

    def test_refresh_failure_still_presents_success_and_failure_outcomes(self):
        for paths in ([self._text('refresh-success.txt')], [self._text('refresh-partial.txt'), self.root / 'missing-refresh.pdf']):
            with self.subTest(paths=paths):
                result = self._run(paths)
                before = result.to_dict()
                refresh = Mock(side_effect=RuntimeError('refresh unavailable'))
                set_text, information, warning = Mock(), Mock(), Mock()
                summary = project_kb.present_batch_import_result(result, refresh=refresh, set_result_text=set_text,
                    show_information=information, show_warning=warning)
                refresh.assert_called_once_with()
                set_text.assert_called_once_with(summary)
                warning.assert_called_once_with(summary)
                information.assert_not_called()
                self.assertIn(project_kb.format_batch_import_summary(result), summary)
                self.assertIn('界面刷新失败', summary)
                self.assertIn('refresh unavailable', summary)
                self.assertEqual(result.to_dict(), before)


if __name__ == "__main__":
    unittest.main()
