from __future__ import annotations

import json
import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfWriter

import db
import project_kb
import review_engine
from pdf_support import PDF_PASSWORD_MESSAGE
from project_kb import ingest_project_file, list_findings, save_review
from project_mode import ensure_project_schema, get_project, save_project
from provider import ProviderRequestError


ROOT = Path(__file__).resolve().parents[1]


class HotfixV132Tests(unittest.TestCase):
    def setUp(self):
        base = ROOT / ".test-tmp"
        base.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=base)
        self.root = Path(self.tmp.name)
        self.old_db = db.DB_PATH
        self.old_project_dir = project_kb.PROJECT_DIR
        db.DB_PATH = self.root / "v132.sqlite"
        os.environ["DATABASE_PATH"] = str(db.DB_PATH)
        project_kb.PROJECT_DIR = self.root / "projects"
        db.init_db()
        ensure_project_schema()
        project_kb.ensure_project_kb_schema()
        self.project_id = save_project({"name": "V1.3.2 测试项目"})
        self.sample = self.root / "review.txt"
        self.sample.write_text("施工方案审查内容", encoding="utf-8")

    def tearDown(self):
        db.DB_PATH = self.old_db
        os.environ["DATABASE_PATH"] = str(self.old_db)
        project_kb.PROJECT_DIR = self.old_project_dir
        self.tmp.cleanup()

    @staticmethod
    def _finding(status: str) -> dict:
        return {
            "severity": "中",
            "category": "模型辅助发现",
            "location": "第1页",
            "issue": "模型识别到待核对问题",
            "norm_refs": [],
            "project_refs": [],
            "recommendation": "人工核对后决定是否整改",
            "evidence_grade": "D",
            "confidence": "低",
            "finding_type": "需核对",
            "status": status,
            "notes": "",
        }

    def _run_model_review(self, status: str):
        payload = json.dumps({"summary": "模型审查", "findings": [self._finding(status)]}, ensure_ascii=False)
        provider = types.SimpleNamespace(
            provider_id="mock",
            config=types.SimpleNamespace(model="mock-model"),
            supports=lambda capability: capability == "text_input",
            generate=lambda **kwargs: payload,
        )
        with patch("review_engine.resolve_provider", return_value=provider), \
             patch("review_engine._norm_evidence", return_value=[]), \
             patch("review_engine.search_project_chunks", return_value=[]), \
             patch("review_engine.list_project_requirements", return_value=[]), \
             patch("review_engine.project_context_text", return_value="project"):
            return review_engine.run_review(get_project(self.project_id), [str(self.sample)], "施工方案审查")

    def test_model_requested_rectification_and_confirmed_states_fail_closed(self):
        for model_status in ("待整改", "confirmed"):
            with self.subTest(model_status=model_status):
                result = self._run_model_review(model_status)
                self.assertEqual(result["findings"][0]["status"], "待确认")
                self.assertEqual(result["findings"][0]["discovery_mode"], "model_assisted")
                self.assertEqual(result["review_workflow"][0]["status"], "pending_confirmation")
                persisted = list_findings(review_id=result["review_id"])
                self.assertEqual(persisted[0]["status"], "待确认")

    def test_legacy_persistence_cannot_bypass_confirmation_after_restart(self):
        result = {"summary": "legacy", "findings": [self._finding("待整改")]}
        review_id = save_review(self.project_id, "施工方案审查", "legacy", "", "mock", ["review.txt"], result)
        # list_findings opens a new database connection, matching application restart semantics.
        persisted = list_findings(review_id=review_id)
        self.assertEqual(persisted[0]["status"], "待确认")
        with db.connect() as con:
            self.assertEqual(con.execute("SELECT status FROM review_findings WHERE review_id=?", (review_id,)).fetchone()["status"], "待确认")
            raw = json.loads(con.execute("SELECT raw_json FROM project_reviews WHERE id=?", (review_id,)).fetchone()["raw_json"])
            self.assertEqual(raw["findings"][0]["status"], "待确认")

    def test_failed_model_call_creates_no_review_or_finding(self):
        provider = types.SimpleNamespace(
            provider_id="mock",
            config=types.SimpleNamespace(model="mock-model"),
            supports=lambda capability: capability == "text_input",
            generate=lambda **kwargs: (_ for _ in ()).throw(ProviderRequestError("上游请求失败")),
        )
        with patch("review_engine.resolve_provider", return_value=provider), \
             patch("review_engine._norm_evidence", return_value=[]), \
             patch("review_engine.search_project_chunks", return_value=[]), \
             patch("review_engine.list_project_requirements", return_value=[]), \
             patch("review_engine.project_context_text", return_value="project"), \
             self.assertRaises(ProviderRequestError):
            review_engine.run_review(get_project(self.project_id), [str(self.sample)], "施工方案审查")
        with db.connect() as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM project_reviews").fetchone()[0], 0)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM review_findings").fetchone()[0], 0)

    def test_password_pdf_failure_leaves_no_file_database_index_or_claim_source(self):
        protected = self.root / "password-protected.pdf"
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        writer.encrypt("required-password", algorithm="AES-256")
        with protected.open("wb") as handle:
            writer.write(handle)
        with self.assertRaisesRegex(ValueError, PDF_PASSWORD_MESSAGE):
            ingest_project_file(self.project_id, str(protected), "施工图/设计图纸")
        stored = list(project_kb.PROJECT_DIR.rglob("*.pdf")) if project_kb.PROJECT_DIR.exists() else []
        self.assertEqual(stored, [])
        with db.connect() as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM project_files").fetchone()[0], 0)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM project_file_chunks").fetchone()[0], 0)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM project_file_chunks_fts").fetchone()[0], 0)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM project_requirements").fetchone()[0], 0)

    def test_plain_pdf_still_imports_after_validation_first(self):
        plain = self.root / "plain.pdf"
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        with plain.open("wb") as handle:
            writer.write(handle)
        result = ingest_project_file(self.project_id, str(plain), "施工图/设计图纸")
        self.assertEqual(result["original_name"], plain.name)
        self.assertTrue(Path(result["stored_path"]).is_file())


if __name__ == "__main__":
    unittest.main()
