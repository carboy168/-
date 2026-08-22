from __future__ import annotations
from pathlib import Path
from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QSplitter, QTextBrowser, QVBoxLayout, QWidget
from desktop.ui.widgets import DataTable
from desktop.workers import FunctionWorker
from db import list_standards, search_clauses_v3
from ingest_v2 import ingest_pdf_v2
from fetch_public_standard import fetch_one

class StandardsPage(QWidget):
    def __init__(self,parent=None):
        super().__init__(parent); self.pool=QThreadPool.globalInstance(); self.std_rows=[]
        lay=QVBoxLayout(self)
        top=QHBoxLayout()
        self.search=QLineEdit(); self.search.setPlaceholderText("输入规范编号、条文号或关键词，例如：GB 55030 / 3.2.1 / 卫生间防水")
        self.search_btn=QPushButton("搜索条文")
        self.refresh_btn=QPushButton("刷新目录"); self.refresh_btn.setObjectName("Secondary")
        top.addWidget(self.search,1); top.addWidget(self.search_btn); top.addWidget(self.refresh_btn)
        lay.addLayout(top)
        split=QSplitter()
        left=QWidget(); ll=QVBoxLayout(left)
        self.table=DataTable(["ID","编号","名称","专业","状态","实施日期"])
        self.table.setColumnWidth(0,45); self.table.setColumnWidth(1,150); self.table.setColumnWidth(2,280)
        ll.addWidget(QLabel("规范目录")); ll.addWidget(self.table)
        actions=QHBoxLayout()
        self.import_btn=QPushButton("导入所选规范PDF")
        self.fetch_btn=QPushButton("从官方来源获取"); self.fetch_btn.setObjectName("Secondary")
        actions.addWidget(self.import_btn); actions.addWidget(self.fetch_btn); actions.addStretch(); ll.addLayout(actions)
        right=QWidget(); rl=QVBoxLayout(right)
        rl.addWidget(QLabel("条文检索结果"))
        self.results=DataTable(["规范","定位","状态","内容"])
        self.results.setColumnWidth(0,220); self.results.setColumnWidth(1,100); self.results.setColumnWidth(2,100)
        rl.addWidget(self.results,2)
        self.detail=QTextBrowser(); rl.addWidget(self.detail,1)
        split.addWidget(left); split.addWidget(right); split.setSizes([620,820]); lay.addWidget(split)
        self.refresh_btn.clicked.connect(self.refresh)
        self.search_btn.clicked.connect(self.do_search)
        self.search.returnPressed.connect(self.do_search)
        self.results.itemSelectionChanged.connect(self.show_detail)
        self.import_btn.clicked.connect(self.import_pdf)
        self.fetch_btn.clicked.connect(self.fetch_official)
        self.refresh()

    def selected_standard(self):
        r=self.table.currentRow()
        if r<0:return None
        try:
            sid=int(self.table.item(r,0).text())
            return next((x for x in self.std_rows if x["id"]==sid),None)
        except:return None

    def refresh(self):
        self.std_rows=list_standards()
        self.table.set_rows([[x["id"],x["code"],x["title"],x["category"],x["status"],x["effective_date"]] for x in self.std_rows])

    def do_search(self):
        q=self.search.text().strip()
        if not q:return
        rows=search_clauses_v3(q,limit=40)
        self._search_rows=rows
        self.results.set_rows([[f'{x["code"]}《{x["title"]}》',x.get("clause_no") or f'第{x.get("page_no")}页',x["status"],x["content"][:180]] for x in rows])
        if not rows:self.detail.setText("当前条文库未检索到结果。可先导入相关规范全文。")

    def show_detail(self):
        r=self.results.currentRow()
        if r<0 or not hasattr(self,"_search_rows"):return
        x=self._search_rows[r]
        self.detail.setPlainText(f'{x["code"]}《{x["title"]}》\n状态：{x["status"]}\n定位：{x.get("clause_no") or x.get("page_no")}\n来源：{x.get("source_url","")}\n\n{x["content"]}')

    def import_pdf(self):
        std=self.selected_standard()
        if not std: QMessageBox.information(self,"规范全文","请先选择一部规范。");return
        path,_=QFileDialog.getOpenFileName(self,"选择规范PDF","","PDF (*.pdf)")
        if not path:return
        self._run(lambda: ingest_pdf_v2(std["id"],path),f'正在导入 {std["code"]}…',lambda r: self._done(f'导入完成：{r["pages"]}页，{r["clauses"]}个条文/文本块。'))

    def fetch_official(self):
        std=self.selected_standard()
        if not std:return
        self._run(lambda: fetch_one(std["code"]),f'正在从官方白名单来源获取 {std["code"]}…',lambda r:self._done(f'获取并导入完成：{r["pages"]}页，{r["clauses"]}个条文/文本块。'))

    def _run(self,fn,msg,on_done):
        self.detail.setPlainText(msg); w=FunctionWorker(fn)
        w.signals.finished.connect(on_done)
        w.signals.error.connect(lambda e: QMessageBox.critical(self,"操作失败",e))
        self.pool.start(w)

    def _done(self,msg):
        QMessageBox.information(self,"完成",msg); self.do_search()
