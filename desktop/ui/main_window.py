from __future__ import annotations
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMainWindow, QStackedWidget, QStatusBar, QVBoxLayout, QWidget
from desktop.runtime import APP_NAME, APP_VERSION, LOG_DIR, USER_ROOT, RESOURCE_ROOT
from desktop.ui.dashboard import DashboardPage
from desktop.ui.projects import ProjectsPage
from desktop.ui.standards import StandardsPage
from desktop.ui.qa import QAPage
from desktop.ui.project_files import ProjectFilesPage
from desktop.ui.review import ReviewPage
from desktop.ui.findings import FindingsPage
from desktop.ui.updates import UpdatesPage
from desktop.ui.settings import SettingsPage
from project_mode import get_active_project

NAV = [
    ("首页","dashboard"),
    ("我的项目","projects"),
    ("查规范","standards"),
    ("技术问答","qa"),
    ("项目文件","files"),
    ("图纸 / 方案审查","review"),
    ("整改问题","findings"),
    ("规范更新","updates"),
    ("设置","settings"),
]

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} V1.0 Desktop")
        self.resize(1480, 900)
        icon=RESOURCE_ROOT/"assets"/"app.ico"
        if icon.exists():self.setWindowIcon(QIcon(str(icon)))

        central=QWidget(); self.setCentralWidget(central)
        root=QHBoxLayout(central); root.setContentsMargins(0,0,0,0); root.setSpacing(0)

        sidebar=QFrame(); sidebar.setObjectName("Sidebar"); sidebar.setFixedWidth(230)
        sl=QVBoxLayout(sidebar); sl.setContentsMargins(0,0,0,0)
        brand=QLabel("工程规范智能体"); brand.setObjectName("Brand")
        sub=QLabel("V1.0 Desktop · Windows"); sub.setObjectName("SubBrand")
        self.nav=QListWidget(); self.nav.setObjectName("NavList")
        for title,key in NAV:
            it=QListWidgetItem(title); it.setData(Qt.ItemDataRole.UserRole,key); self.nav.addItem(it)
        sl.addWidget(brand);sl.addWidget(sub);sl.addWidget(self.nav,1)
        root.addWidget(sidebar)

        content=QWidget(); cl=QVBoxLayout(content); cl.setContentsMargins(0,0,0,0);cl.setSpacing(0)
        top=QFrame(); top.setObjectName("Topbar"); tl=QHBoxLayout(top);tl.setContentsMargins(22,11,22,11)
        self.title=QLabel("首页");self.title.setObjectName("PageTitle")
        self.current_project=QLabel();self.current_project.setStyleSheet("color:#64748b;")
        self.api_state=QLabel();self.api_state.setStyleSheet("padding:5px 9px;border-radius:10px;background:#eef2f7;color:#475569;")
        tl.addWidget(self.title);tl.addStretch();tl.addWidget(self.current_project);tl.addSpacing(12);tl.addWidget(self.api_state)
        cl.addWidget(top)

        self.stack=QStackedWidget()
        self.pages={
            "dashboard":DashboardPage(),"projects":ProjectsPage(),"standards":StandardsPage(),"qa":QAPage(),
            "files":ProjectFilesPage(),"review":ReviewPage(),"findings":FindingsPage(),"updates":UpdatesPage(),"settings":SettingsPage()
        }
        for _,key in NAV:self.stack.addWidget(self.pages[key])
        cl.addWidget(self.stack,1);root.addWidget(content,1)

        self.nav.currentRowChanged.connect(self.change_page)
        self.pages["projects"].project_changed.connect(self.refresh_context)
        self.pages["settings"].settings_changed.connect(self.refresh_context)
        self.pages["dashboard"].go_qa.connect(lambda:self.goto("qa"))
        self.pages["dashboard"].go_projects.connect(lambda:self.goto("projects"))
        self.pages["dashboard"].go_review.connect(lambda:self.goto("review"))

        sb=QStatusBar();self.setStatusBar(sb)
        sb.showMessage(f"数据目录：{USER_ROOT}    |    日志：{LOG_DIR}")
        self.nav.setCurrentRow(0);self.refresh_context()

    def goto(self,key):
        for i in range(self.nav.count()):
            if self.nav.item(i).data(Qt.ItemDataRole.UserRole)==key:
                self.nav.setCurrentRow(i);return

    def change_page(self,row):
        if row<0:return
        self.stack.setCurrentIndex(row);self.title.setText(NAV[row][0])
        page=self.stack.currentWidget()
        if hasattr(page,"refresh"):page.refresh()
        if hasattr(page,"refresh_project"):page.refresh_project()
        self.refresh_context()

    def refresh_context(self):
        import os
        p=get_active_project();self.current_project.setText("当前项目：" + (p["name"] if p else "未选择"))
        key=bool(os.getenv("OPENAI_API_KEY","").strip());model=os.getenv("OPENAI_MODEL","").strip()
        self.api_state.setText("AI已配置" if key and model else "AI未配置")
        if hasattr(self.pages["dashboard"],"refresh"):self.pages["dashboard"].refresh()
