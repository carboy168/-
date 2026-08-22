from __future__ import annotations

import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

import db


class FakeProvider:
    name = "fake"

    def __init__(self, text="模拟回答"):
        self.text = text
        self.calls = []
        self.config = SimpleNamespace(model="fake-model")

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        return self.text

    def test_connection(self, **kwargs):
        return "连接正常"


class RegressionTests(unittest.TestCase):
    def setUp(self):
        test_tmp = Path(__file__).resolve().parents[1] / ".test-tmp"
        test_tmp.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=test_tmp)
        self.root = Path(self.tmp.name)
        self.old_db_path = db.DB_PATH
        db.DB_PATH = self.root / "norms.db"
        os.environ["DATABASE_PATH"] = str(db.DB_PATH)
        os.environ["PROJECT_DATA_DIR"] = str(self.root / "projects")

    def tearDown(self):
        db.DB_PATH = self.old_db_path
        self.tmp.cleanup()

    def _seed_clause(self, *, status="现行", code="GB 50000-2024", clause="1.0.1"):
        db.init_db()
        sid = db.upsert_standard({"code": code, "title": "测试规范", "status": status})
        db.replace_clauses(sid, [{"clause_no": clause, "content": "施工现场电缆应采取保护措施"}], "test.pdf")
        from ingest_v2 import ensure_v2_schema
        from search_zh import build_index_text

        ensure_v2_schema()
        with db.connect() as con:
            row = con.execute("SELECT id FROM clauses WHERE standard_id=?", (sid,)).fetchone()
            con.execute(
                "INSERT INTO clauses_zh_fts(search_tokens,clause_id,standard_code,standard_title) VALUES(?,?,?,?)",
                (build_index_text(code, clause, "", "施工现场电缆应采取保护措施"), str(row["id"]), code, "测试规范"),
            )
        return sid

    def test_database_initialization(self):
        db.init_db()
        with db.connect() as con:
            names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"standards", "clauses", "route_logs"}.issubset(names))

    def test_old_database_migration_is_idempotent_and_preserves_data(self):
        db.init_db()
        sid = db.upsert_standard({"code": "GB 1-2000", "title": "用户规范", "status": "现行"})
        db.replace_clauses(sid, [{"clause_no": "1.0.1", "content": "用户条文"}], "user.pdf")
        from project_mode import add_project_requirement, ensure_project_schema, save_project
        from project_kb import ensure_project_kb_schema

        ensure_project_schema()
        ensure_project_kb_schema()
        pid = save_project({"name": "用户项目", "province": "广东省"})
        add_project_requirement(pid, "合同", "用户要求", "必须保留")
        with db.connect() as con:
            review_id = con.execute(
                "INSERT INTO project_reviews(project_id,review_type,title,summary,status) VALUES(?,?,?,?,?)",
                (pid, "施工方案审查", "用户审查", "原审查记录", "已完成"),
            ).lastrowid
            con.execute(
                "INSERT INTO review_findings(review_id,project_id,issue,status) VALUES(?,?,?,?)",
                (review_id, pid, "原整改问题", "待整改"),
            )
        from migrations import LATEST_SCHEMA_VERSION, get_schema_version, migrate

        self.assertEqual(migrate(), LATEST_SCHEMA_VERSION)
        self.assertEqual(migrate(), LATEST_SCHEMA_VERSION)
        self.assertEqual(get_schema_version(), LATEST_SCHEMA_VERSION)
        with db.connect() as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM standards WHERE title='用户规范'").fetchone()[0], 1)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM clauses WHERE content='用户条文'").fetchone()[0], 1)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM projects WHERE name='用户项目'").fetchone()[0], 1)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM project_requirements WHERE requirement_text='必须保留'").fetchone()[0], 1)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM project_reviews WHERE summary='原审查记录'").fetchone()[0], 1)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM review_findings WHERE issue='原整改问题'").fetchone()[0], 1)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0], 5)

    def test_norm_retrieval(self):
        self._seed_clause()
        rows = db.search_clauses_v2("施工现场电缆", limit=5)
        self.assertTrue(rows)
        self.assertEqual(rows[0]["status"], "现行")

    def test_question_routing(self):
        from router import route_question

        route = route_question("施工现场临时用电电缆能否拖地？")
        self.assertTrue(route["intent"])
        self.assertFalse(route["router_is_evidence"])

    def test_rag_uses_provider_mock(self):
        fake = FakeProvider("基于本地证据的模拟结论")
        rows = [{
            "clause_no": "1.0.1", "page_no": 1, "code": "GB 50000-2024", "title": "测试规范",
            "status": "现行", "mandatory_level": "", "effective_date": "2024-01-01",
            "source_url": "local", "content": "测试条文",
        }]
        with patch.dict(os.environ, {"OPENAI_API_KEY": "fake-key", "OPENAI_MODEL": "fake-model"}, clear=False), \
             patch("rag.resolve_provider", return_value=fake):
            from rag import answer

            text = answer("测试问题", rows, project=None)
        self.assertEqual(text, "基于本地证据的模拟结论")
        self.assertEqual(len(fake.calls), 1)

    def test_repealed_clause_is_blocked(self):
        self._seed_clause(code="GB 50000-2024", clause="1.0.1")
        with db.connect() as con:
            con.execute(
                "INSERT INTO standard_clause_overrides(standard_code,clause_no,override_type,superseding_code,note) VALUES(?,?,?,?,?)",
                ("GB 50000-2024", "1.0.1", "repealed", "GB 50000-2025", "测试失效"),
            )
        self.assertEqual(db.search_clauses_v2("GB 50000-2024 1.0.1", limit=5), [])

    def test_project_knowledge_base(self):
        db.init_db()
        from project_mode import ensure_project_schema, save_project
        from project_kb import ensure_project_kb_schema, ingest_project_file, search_project_chunks

        ensure_project_schema()
        ensure_project_kb_schema()
        pid = save_project({"name": "测试项目", "province": "广东省"})
        source = self.root / "会审.txt"
        source.write_text("卫生间二次排水由总包负责施工", encoding="utf-8")
        ingest_project_file(pid, str(source), "图纸会审/设计回复")
        self.assertTrue(search_project_chunks(pid, "二次排水 总包", limit=5))

    def test_provider_mock_never_calls_paid_api(self):
        fake = FakeProvider()
        self.assertEqual(fake.test_connection(model="mock"), "连接正常")
        self.assertEqual(fake.calls, [])


if __name__ == "__main__":
    unittest.main()
