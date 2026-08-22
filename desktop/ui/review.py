from __future__ import annotations
from pathlib import Path
from PySide6.QtCore import Qt, QThreadPool
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QComboBox, QSplitter, QTextBrowser, QTextEdit, QVBoxLayout, QWidget
from desktop.ui.widgets import DataTable
from desktop.workers import FunctionWorker
from project_mode import get_active_project
from project_kb import list_project_files, list_reviews, list_findings
from review_engine import REVIEW_TYPES, run_review

class ReviewPage(QWidget):
    def __init__(self,parent=None):
        super().__init__(parent); self.pool=QThreadPool.globalInstance(); self.external=[]
        lay=QVBoxLayout(self)
        self.project=QLabel(); self.project.setStyleSheet("font-weight:700;color:#214f8f;"); lay.addWidget(self.project)
        top=QHBoxLayout()
        self.review_type=QComboBox(); self.review_type.addItems(REVIEW_TYPES)
        self.run_btn=QPushButton("开始审查")
        self.add_external=QPushButton("添加外部文件"); self.add_external.setObjectName("Secondary")
        top.addWidget(QLabel("审查类型：")); top.addWidget(self.review_type); top.addWidget(self.add_external); top.addWidget(self.run_btn); top.addStretch()
        lay.addLayout(top)
        split=QSplitter()
        left=QWidget(); ll=QVBoxLayout(left)
        ll.addWidget(QLabel("选择当前项目已归档文件（可多选）"))
        self.files=QListWidget(); self.files.setSelectionMode(QListWidget.SelectionMode.MultiSelection); ll.addWidget(self.files,1)
        ll.addWidget(QLabel("审查范围 / 重点"))
        self.scope=QTextEdit(); self.scope.setMaximumHeight(100); self.scope.setPlaceholderText("可留空，按默认清单全面审查。")
        right=QWidget(); rl=QVBoxLayout(right)
        self.summary=QTextBrowser(); self.summary.setMaximumHeight(150); rl.addWidget(self.summary)
        self.findings=DataTable(["风险","专业","位置","问题","证据","建议","状态"])
        self.findings.setColumnWidth(0,60); self.findings.setColumnWidth(1,100); self.findings.setColumnWidth(2,120); self.findings.setColumnWidth(3,320)
        rl.addWidget(self.findings,1)
        split.addWidget(left); split.addWidget(right); split.setSizes([450,950]); lay.addWidget(split,1)
        self.add_external.clicked.connect(self.choose_external); self.run_btn.clicked.connect(self.run)
        self.refresh()

    def refresh(self):
        p=get_active_project(); self.project.setText("当前项目：" + (p["name"] if p else "未选择"))
        self.files.clear()
        if p:
            for f in list_project_files(p["id"]):
                it=QListWidgetItem(f'{f["doc_type"]}｜{f["title"]}｜{f["original_name"]}')
                it.setData(Qt.ItemDataRole.UserRole,f["stored_path"]); self.files.addItem(it)

    def choose_external(self):
        paths,_=QFileDialog.getOpenFileNames(self,"选择审查文件","","支持文件 (*.pdf *.png *.jpg *.jpeg *.webp *.docx *.pptx *.xlsx *.txt *.md *.csv);;全部文件 (*.*)")
        if paths:
            self.external=paths
            self.summary.setPlainText("已添加外部文件：\n"+"\n".join(paths))

    def run(self):
        p=get_active_project()
        if not p: QMessageBox.information(self,"审查","请先选择当前项目。"); return
        paths=[it.data(Qt.ItemDataRole.UserRole) for it in self.files.selectedItems()] + self.external
        paths=list(dict.fromkeys(x for x in paths if x))
        if not paths: QMessageBox.information(self,"审查","请至少选择一个文件。"); return
        self.run_btn.setEnabled(False); self.summary.setPlainText("正在调用AI进行审查，请勿关闭程序…")
        typ=self.review_type.currentText(); scope=self.scope.toPlainText().strip()
        w=FunctionWorker(lambda:run_review(p,paths,typ,scope=scope))
        w.signals.finished.connect(self.done); w.signals.error.connect(self.error); self.pool.start(w)

    def done(self,result):
        self.run_btn.setEnabled(True); self.summary.setPlainText(result.get("summary",""))
        rows=[]
        for f in result.get("findings",[]):
            refs=" ".join(f.get("norm_refs",[])+f.get("project_refs",[]))
            rows.append([f.get("severity"),f.get("category"),f.get("location"),f.get("issue"),refs,f.get("recommendation"),f.get("status")])
        self.findings.set_rows(rows)

    def error(self,e):
        self.run_btn.setEnabled(True); QMessageBox.critical(self,"审查失败",e); self.summary.setPlainText("审查失败，请检查API设置、文件大小、网络和日志。")
