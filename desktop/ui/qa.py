from __future__ import annotations
from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMessageBox, QPushButton, QSplitter, QTextBrowser, QTextEdit, QVBoxLayout, QWidget
from desktop.workers import FunctionWorker
from project_mode import get_active_project
from rag import retrieve_with_route, answer
from router import route_summary
from project_mode import overlay_summary

class QAPage(QWidget):
    def __init__(self,parent=None):
        super().__init__(parent); self.pool=QThreadPool.globalInstance()
        lay=QVBoxLayout(self)
        self.project=QLabel(); self.project.setStyleSheet("font-weight:700;color:#214f8f;")
        lay.addWidget(self.project)
        self.question=QTextEdit(); self.question.setPlaceholderText("直接用现场语言提问，例如：施工现场电缆能不能直接拖地？"); self.question.setMaximumHeight(110)
        lay.addWidget(self.question)
        bar=QHBoxLayout(); self.ask=QPushButton("检索并回答"); self.clear=QPushButton("清空"); self.clear.setObjectName("Secondary")
        bar.addWidget(self.ask); bar.addWidget(self.clear); bar.addStretch(); lay.addLayout(bar)
        split=QSplitter()
        self.route=QTextBrowser(); self.route.setPlaceholderText("问题路由、项目叠加和证据摘要")
        self.response=QTextBrowser(); self.response.setPlaceholderText("综合回答")
        split.addWidget(self.route); split.addWidget(self.response); split.setSizes([500,900])
        lay.addWidget(split,1)
        self.ask.clicked.connect(self.run); self.clear.clicked.connect(lambda:(self.question.clear(),self.response.clear(),self.route.clear()))
        self.refresh_project()

    def refresh_project(self):
        p=get_active_project(); self.project.setText("当前项目：" + (p["name"] if p else "未选择（仍可做通用规范问答）"))

    def run(self):
        q=self.question.toPlainText().strip()
        if not q:return
        p=get_active_project()
        self.ask.setEnabled(False); self.response.setPlainText("正在检索规范与项目文件，并生成回答…")
        def task():
            rows,route,overlay=retrieve_with_route(q,limit=12,project=p)
            text=answer(q,rows,route=route,project=p,overlay=overlay)
            return rows,route,overlay,text
        w=FunctionWorker(task); w.signals.finished.connect(self.done); w.signals.error.connect(self.error); self.pool.start(w)

    def done(self,data):
        rows,route,overlay,text=data
        evidence="\n".join(f'- {r["code"]} {r.get("clause_no") or ("第"+str(r.get("page_no"))+"页")}｜{r["content"][:100]}' for r in rows[:8]) or "未检索到规范条文"
        self.route.setPlainText(f'{route_summary(route)}\n\n{overlay_summary(overlay)}\n\n规范证据摘要：\n{evidence}')
        self.response.setPlainText(text); self.ask.setEnabled(True)

    def error(self,e):
        self.ask.setEnabled(True); QMessageBox.critical(self,"问答失败",e); self.response.setPlainText("问答失败，请检查设置、网络和日志。")
