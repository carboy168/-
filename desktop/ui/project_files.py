from __future__ import annotations
from pathlib import Path
from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import QComboBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QSplitter, QTextBrowser, QVBoxLayout, QWidget
from desktop.ui.widgets import DataTable
from desktop.workers import FunctionWorker
from project_mode import get_active_project
from project_kb import DOC_TYPES, ingest_project_file, list_project_files, search_project_chunks, delete_project_file, project_kb_stats

class ProjectFilesPage(QWidget):
    def __init__(self,parent=None):
        super().__init__(parent); self.pool=QThreadPool.globalInstance(); self.rows=[]
        lay=QVBoxLayout(self)
        self.project=QLabel(); self.project.setStyleSheet("font-weight:700;color:#214f8f;"); lay.addWidget(self.project)
        top=QHBoxLayout()
        self.doc_type=QComboBox(); self.doc_type.addItems(DOC_TYPES)
        self.upload=QPushButton("导入项目文件")
        self.delete=QPushButton("删除所选"); self.delete.setObjectName("Danger")
        self.search=QLineEdit(); self.search.setPlaceholderText("搜索当前项目文件内容，例如：二次排水 / 艺术漆样板 / 总包")
        self.search_btn=QPushButton("搜索")
        top.addWidget(self.doc_type); top.addWidget(self.upload); top.addWidget(self.delete); top.addStretch(); top.addWidget(self.search,1); top.addWidget(self.search_btn)
        lay.addLayout(top)
        split=QSplitter()
        self.table=DataTable(["ID","文件类型","标题","原文件名","页数","文本块","索引状态"])
        self.table.setColumnWidth(0,50); self.table.setColumnWidth(1,170); self.table.setColumnWidth(2,220)
        self.result=QTextBrowser()
        split.addWidget(self.table); split.addWidget(self.result); split.setSizes([760,620])
        lay.addWidget(split,1)
        self.upload.clicked.connect(self.add_files); self.search_btn.clicked.connect(self.do_search); self.search.returnPressed.connect(self.do_search)
        self.delete.clicked.connect(self.delete_selected)
        self.refresh()

    def refresh(self):
        p=get_active_project(); self.project.setText("当前项目：" + (p["name"] if p else "未选择"))
        self.rows=list_project_files(p["id"]) if p else []
        self.table.set_rows([[x["id"],x["doc_type"],x["title"],x["original_name"],x["page_count"],x["chunk_count"],x["index_status"]] for x in self.rows])

    def add_files(self):
        p=get_active_project()
        if not p: QMessageBox.information(self,"项目文件","请先选择当前项目。"); return
        paths,_=QFileDialog.getOpenFileNames(self,"选择项目文件","","项目文件 (*.pdf *.docx *.pptx *.xlsx *.txt *.md *.csv *.json *.xml *.html *.png *.jpg *.jpeg *.webp);;全部文件 (*.*)")
        if not paths:return
        dtype=self.doc_type.currentText()
        def task():
            out=[]
            for x in paths:out.append(ingest_project_file(p["id"],x,dtype,title=Path(x).stem))
            return out
        w=FunctionWorker(task); w.signals.finished.connect(lambda r:(self.refresh(),QMessageBox.information(self,"完成",f"已导入 {len(r)} 个文件。")))
        w.signals.error.connect(lambda e:QMessageBox.critical(self,"导入失败",e)); self.pool.start(w)

    def do_search(self):
        p=get_active_project(); q=self.search.text().strip()
        if not p or not q:return
        rows=search_project_chunks(p["id"],q,limit=15)
        text=[]
        for i,r in enumerate(rows,1):
            loc=f'第{r["page_no"]}页' if r.get("page_no") else r.get("section","")
            text.append(f'【P{i}】{r["doc_type"]}｜{r["title"]}｜{loc}\n{r["content"]}\n')
        self.result.setPlainText("\n".join(text) or "未检索到相关项目文件内容。")

    def delete_selected(self):
        r=self.table.currentRow()
        if r<0:return
        fid=int(self.table.item(r,0).text())
        if QMessageBox.question(self,"删除文件","确定删除所选项目文件及其索引吗？")==QMessageBox.StandardButton.Yes:
            delete_project_file(fid); self.refresh()
