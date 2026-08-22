from __future__ import annotations
from PySide6.QtCore import Qt, Signal, QDate
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox, QFormLayout, QGridLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea,
    QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget
)
from desktop.ui.widgets import DataTable
from project_mode import (
    BUILDING_TYPES, PROJECT_NATURES, PROJECT_PHASES, PROJECT_SCOPES, FLAG_LABELS,
    get_active_project, get_project, list_projects, save_project, set_active_project
)

class ProjectDialog(QDialog):
    def __init__(self, project=None, parent=None):
        super().__init__(parent)
        self.project = project or {}
        self.setWindowTitle("项目档案")
        self.resize(720, 760)
        outer=QVBoxLayout(self)
        scroll=QScrollArea(); scroll.setWidgetResizable(True)
        body=QWidget(); form=QFormLayout(body)

        self.name=QLineEdit(self.project.get("name",""))
        self.province=QLineEdit(self.project.get("province","广东省"))
        self.city=QLineEdit(self.project.get("city",""))
        self.district=QLineEdit(self.project.get("district",""))
        self.building=QComboBox(); self.building.addItems(BUILDING_TYPES)
        self.nature=QComboBox(); self.nature.addItems(PROJECT_NATURES)
        self.phase=QComboBox(); self.phase.addItems(PROJECT_PHASES)
        self.usage=QLineEdit(self.project.get("usage",""))
        self.refdate=QDateEdit(); self.refdate.setCalendarPopup(True); self.refdate.setDisplayFormat("yyyy-MM-dd")
        date_text=self.project.get("reference_date","")
        self.refdate.setDate(QDate.fromString(date_text,"yyyy-MM-dd") if date_text else QDate.currentDate())
        for cb, key, default in [(self.building,"building_type","公共建筑"),(self.nature,"project_nature","室内装修"),(self.phase,"phase","施工")]:
            i=cb.findText(self.project.get(key,default)); cb.setCurrentIndex(max(0,i))

        form.addRow("项目名称 *", self.name)
        form.addRow("省份", self.province)
        form.addRow("城市", self.city)
        form.addRow("区/县", self.district)
        form.addRow("建筑类型", self.building)
        form.addRow("项目性质", self.nature)
        form.addRow("用途", self.usage)
        form.addRow("当前阶段", self.phase)
        form.addRow("规范适用基准日", self.refdate)

        scope_box=QGroupBox("涉及专业")
        sg=QGridLayout(scope_box); self.scope_checks={}
        selected=set(self.project.get("scopes",[]) or [])
        for i,s in enumerate(PROJECT_SCOPES):
            ck=QCheckBox(s); ck.setChecked(s in selected); self.scope_checks[s]=ck
            sg.addWidget(ck,i//3,i%3)
        form.addRow(scope_box)

        flag_box=QGroupBox("特殊项目条件")
        fg=QGridLayout(flag_box); self.flag_checks={}
        for i,(k,label) in enumerate(FLAG_LABELS.items()):
            ck=QCheckBox(label); ck.setChecked(bool(self.project.get(k))); self.flag_checks[k]=ck
            fg.addWidget(ck,i//2,i%2)
        form.addRow(flag_box)

        self.design_notes=QTextEdit(self.project.get("design_notes","")); self.design_notes.setMaximumHeight(90)
        self.notes=QTextEdit(self.project.get("notes","")); self.notes.setMaximumHeight(90)
        form.addRow("设计/审图/消防控制条件", self.design_notes)
        form.addRow("项目备注", self.notes)

        scroll.setWidget(body); outer.addWidget(scroll)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    def data(self):
        return {
            "name":self.name.text().strip(),"province":self.province.text().strip(),"city":self.city.text().strip(),
            "district":self.district.text().strip(),"building_type":self.building.currentText(),
            "project_nature":self.nature.currentText(),"usage":self.usage.text().strip(),"phase":self.phase.currentText(),
            "reference_date":self.refdate.date().toString("yyyy-MM-dd"),
            "scopes":[s for s,c in self.scope_checks.items() if c.isChecked()],
            **{k:c.isChecked() for k,c in self.flag_checks.items()},
            "design_notes":self.design_notes.toPlainText().strip(),"notes":self.notes.toPlainText().strip()
        }

    def accept(self):
        if not self.name.text().strip():
            QMessageBox.warning(self,"项目档案","项目名称不能为空。"); return
        super().accept()

class ProjectsPage(QWidget):
    project_changed=Signal()
    def __init__(self,parent=None):
        super().__init__(parent)
        lay=QVBoxLayout(self)
        top=QHBoxLayout()
        self.active=QLabel("当前项目：未选择"); self.active.setStyleSheet("font-weight:700;color:#214f8f;")
        top.addWidget(self.active); top.addStretch()
        self.new_btn=QPushButton("新建项目")
        self.edit_btn=QPushButton("编辑项目"); self.edit_btn.setObjectName("Secondary")
        self.active_btn=QPushButton("设为当前项目")
        top.addWidget(self.new_btn); top.addWidget(self.edit_btn); top.addWidget(self.active_btn)
        lay.addLayout(top)
        self.table=DataTable(["ID","项目名称","地区","建筑类型","项目性质","阶段","基准日"])
        self.table.setColumnWidth(0,55); self.table.setColumnWidth(1,220); self.table.setColumnWidth(2,150)
        lay.addWidget(self.table)
        self.new_btn.clicked.connect(self.new_project)
        self.edit_btn.clicked.connect(self.edit_project)
        self.active_btn.clicked.connect(self.set_active)
        self.table.doubleClicked.connect(self.set_active)
        self.refresh()

    def selected_id(self):
        r=self.table.currentRow()
        if r<0:return None
        item=self.table.item(r,0)
        try:return int(item.text())
        except:return None

    def refresh(self):
        rows=[]
        for p in list_projects():
            rows.append([p["id"],p["name"],f'{p.get("province","")}{p.get("city","")}{p.get("district","")}',
                         p.get("building_type",""),p.get("project_nature",""),p.get("phase",""),p.get("reference_date","")])
        self.table.set_rows(rows)
        p=get_active_project()
        self.active.setText("当前项目：" + (p["name"] if p else "未选择"))

    def new_project(self):
        d=ProjectDialog(parent=self)
        if d.exec():
            pid=save_project(d.data())
            if not get_active_project(): set_active_project(pid)
            self.refresh(); self.project_changed.emit()

    def edit_project(self):
        pid=self.selected_id()
        if not pid: QMessageBox.information(self,"项目","请先选择项目。"); return
        d=ProjectDialog(get_project(pid),self)
        if d.exec():
            save_project(d.data(),pid); self.refresh(); self.project_changed.emit()

    def set_active(self):
        pid=self.selected_id()
        if not pid:return
        set_active_project(pid); self.refresh(); self.project_changed.emit()
