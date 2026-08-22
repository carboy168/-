APP_QSS = r"""
QMainWindow, QWidget {
    background: #f5f7fa;
    color: #1f2937;
    font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI";
    font-size: 10.5pt;
}
QFrame#Sidebar {
    background: #172033;
    border: none;
}
QLabel#Brand {
    color: white;
    font-size: 16pt;
    font-weight: 700;
    padding: 18px 14px 8px 14px;
}
QLabel#SubBrand {
    color: #aeb9cc;
    padding: 0px 14px 16px 14px;
}
QListWidget#NavList {
    background: #172033;
    color: #d8deea;
    border: none;
    outline: none;
    padding: 6px;
}
QListWidget#NavList::item {
    height: 42px;
    border-radius: 7px;
    padding-left: 12px;
    margin: 2px 3px;
}
QListWidget#NavList::item:selected {
    background: #2f5fa7;
    color: white;
}
QListWidget#NavList::item:hover {
    background: #25334d;
}
QFrame#Topbar {
    background: white;
    border-bottom: 1px solid #e5e7eb;
}
QLabel#PageTitle {
    font-size: 17pt;
    font-weight: 700;
}
QFrame#Card {
    background: white;
    border: 1px solid #e6eaf0;
    border-radius: 10px;
}
QLabel#Metric {
    font-size: 22pt;
    font-weight: 700;
    color: #214f8f;
}
QPushButton {
    background: #2f5fa7;
    color: white;
    border: none;
    border-radius: 6px;
    padding: 8px 14px;
    min-height: 18px;
}
QPushButton:hover { background: #244c89; }
QPushButton:disabled { background: #b8c1cf; }
QPushButton#Secondary {
    background: white;
    color: #2b456d;
    border: 1px solid #cbd5e1;
}
QPushButton#Danger { background: #b42318; }
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QDateEdit, QSpinBox {
    background: white;
    border: 1px solid #cfd7e3;
    border-radius: 6px;
    padding: 6px;
    selection-background-color: #2f5fa7;
}
QTableWidget {
    background: white;
    alternate-background-color: #f8fafc;
    gridline-color: #e5e7eb;
    border: 1px solid #e5e7eb;
    border-radius: 7px;
}
QHeaderView::section {
    background: #edf2f7;
    color: #334155;
    border: none;
    border-right: 1px solid #dde4ed;
    padding: 7px;
    font-weight: 600;
}
QTabWidget::pane {
    border: 1px solid #e5e7eb;
    background: white;
}
QTabBar::tab {
    padding: 9px 16px;
    background: #eef2f7;
    margin-right: 2px;
}
QTabBar::tab:selected {
    background: white;
    color: #214f8f;
    font-weight: 700;
}
QStatusBar {
    background: white;
    border-top: 1px solid #e5e7eb;
}
"""
