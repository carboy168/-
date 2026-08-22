from __future__ import annotations
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget
from desktop.ui.widgets import MetricCard
from db import connect
from project_mode import get_active_project, project_context_text
from project_kb import project_kb_stats

class DashboardPage(QWidget):
    go_qa=Signal(); go_projects=Signal(); go_review=Signal()
    def __init__(self,parent=None):
        super().__init__(parent)
        lay=QVBoxLayout(self)
        self.project_label=QLabel()
        self.project_label.setStyleSheet("font-size:14pt;font-weight:700;color:#214f8f;")
        self.context=QLabel(); self.context.setWordWrap(True); self.context.setStyleSheet("color:#64748b;")
        lay.addWidget(self.project_label); lay.addWidget(self.context)

        grid=QGridLayout()
        self.cards=[
            MetricCard("现行/即将实施规范","0","正式目录状态"),
            MetricCard("规范条文证据","0","已导入全文条文/文本块"),
            MetricCard("项目文件","0","当前项目归档文件"),
            MetricCard("待处理审查问题","0","当前项目未关闭问题"),
        ]
        for i,c in enumerate(self.cards):grid.addWidget(c,i//4,i%4)
        lay.addLayout(grid)

        action=QHBoxLayout()
        q=QPushButton("开始技术问答"); q.clicked.connect(self.go_qa.emit)
        p=QPushButton("管理项目"); p.setObjectName("Secondary"); p.clicked.connect(self.go_projects.emit)
        r=QPushButton("图纸 / 方案审查"); r.clicked.connect(self.go_review.emit)
        action.addWidget(q); action.addWidget(p); action.addWidget(r); action.addStretch()
        lay.addLayout(action)
        lay.addStretch()
        self.refresh()

    def refresh(self):
        p=get_active_project()
        self.project_label.setText("当前项目：" + (p["name"] if p else "未选择"))
        self.context.setText(project_context_text(p) if p else "建议先在“我的项目”建立项目档案，再进行规范问答和审查。")
        with connect() as con:
            std=con.execute("SELECT COUNT(*) n FROM standards WHERE status IN ('现行','即将实施')").fetchone()["n"]
            clauses=con.execute("SELECT COUNT(*) n FROM clauses").fetchone()["n"]
        files=findings=0
        if p:
            st=project_kb_stats(p["id"]); files=st["files"]
            with connect() as con:
                findings=con.execute("SELECT COUNT(*) n FROM review_findings WHERE project_id=? AND status NOT IN ('已关闭','提示')",(p["id"],)).fetchone()["n"]
        self.cards[0].set_value(std)
        self.cards[1].set_value(clauses)
        self.cards[2].set_value(files)
        self.cards[3].set_value(findings)
