from __future__ import annotations
from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout, QWidget
from desktop.ui.widgets import DataTable
from desktop.workers import FunctionWorker
from db import recent_update_candidates
import update_monitor

class UpdatesPage(QWidget):
    def __init__(self,parent=None):
        super().__init__(parent);self.pool=QThreadPool.globalInstance()
        lay=QVBoxLayout(self); top=QHBoxLayout()
        lab=QLabel("只发现更新候选，不会未经核验自动修改现行规范状态。"); lab.setStyleSheet("color:#64748b;")
        self.scan=QPushButton("扫描官方来源")
        top.addWidget(lab);top.addStretch();top.addWidget(self.scan);lay.addLayout(top)
        self.table=DataTable(["来源","标题","发现日期","状态","URL"])
        self.table.setColumnWidth(0,180);self.table.setColumnWidth(1,420);self.table.setColumnWidth(2,100);self.table.setColumnWidth(3,90)
        lay.addWidget(self.table)
        self.scan.clicked.connect(self.run);self.refresh()

    def refresh(self):
        rows=recent_update_candidates(200)
        self.table.set_rows([[x["source_name"],x["title"],x["discovered_date"],x["status"],x["url"]] for x in rows])

    def run(self):
        self.scan.setEnabled(False)
        w=FunctionWorker(update_monitor.main)
        w.signals.finished.connect(lambda _:(self.scan.setEnabled(True),self.refresh(),QMessageBox.information(self,"完成","官方来源扫描完成，候选已进入待核验队列。")))
        w.signals.error.connect(lambda e:(self.scan.setEnabled(True),QMessageBox.critical(self,"扫描失败",e)))
        self.pool.start(w)
