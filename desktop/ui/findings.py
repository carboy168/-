from __future__ import annotations
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QMessageBox, QPushButton, QTextEdit, QVBoxLayout, QWidget
from desktop.ui.widgets import DataTable
from project_mode import get_active_project
from project_kb import list_findings, update_finding_status

class FindingsPage(QWidget):
    def __init__(self,parent=None):
        super().__init__(parent); self.rows=[]
        lay=QVBoxLayout(self)
        top=QHBoxLayout(); self.project=QLabel(); self.project.setStyleSheet("font-weight:700;color:#214f8f;")
        self.refresh_btn=QPushButton("刷新"); self.refresh_btn.setObjectName("Secondary")
        top.addWidget(self.project); top.addStretch(); top.addWidget(self.refresh_btn); lay.addLayout(top)
        self.table=DataTable(["ID","风险","专业","位置","问题","证据等级","状态","建议"])
        self.table.setColumnWidth(0,50); self.table.setColumnWidth(1,60); self.table.setColumnWidth(2,110); self.table.setColumnWidth(3,120); self.table.setColumnWidth(4,360)
        lay.addWidget(self.table,1)
        edit=QHBoxLayout(); self.status=QComboBox(); self.status.addItems(["待确认","待整改","已整改","已关闭","提示"])
        self.notes=QTextEdit(); self.notes.setMaximumHeight(70); self.notes.setPlaceholderText("复核备注")
        self.save=QPushButton("更新状态")
        edit.addWidget(QLabel("状态："));edit.addWidget(self.status);edit.addWidget(self.notes,1);edit.addWidget(self.save);lay.addLayout(edit)
        self.refresh_btn.clicked.connect(self.refresh); self.save.clicked.connect(self.update); self.refresh()

    def refresh(self):
        p=get_active_project(); self.project.setText("当前项目：" + (p["name"] if p else "未选择"))
        self.rows=list_findings(project_id=p["id"]) if p else []
        self.table.set_rows([[x["id"],x["severity"],x["category"],x["location"],x["issue"],x["evidence_grade"],x["status"],x["recommendation"]] for x in self.rows])

    def update(self):
        r=self.table.currentRow()
        if r<0:return
        fid=int(self.table.item(r,0).text()); update_finding_status(fid,self.status.currentText(),self.notes.toPlainText().strip());self.refresh()
