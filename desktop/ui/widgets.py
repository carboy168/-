from __future__ import annotations
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget, QTableWidget,
    QTableWidgetItem, QAbstractItemView, QHeaderView
)

class MetricCard(QFrame):
    def __init__(self, title="", value="0", subtitle="", parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        self.title = QLabel(title)
        self.value = QLabel(str(value))
        self.value.setObjectName("Metric")
        self.subtitle = QLabel(subtitle)
        self.subtitle.setStyleSheet("color:#64748b;")
        self.subtitle.setWordWrap(True)
        lay.addWidget(self.title)
        lay.addWidget(self.value)
        lay.addWidget(self.subtitle)
        lay.addStretch(1)

    def set_value(self, value, subtitle=None):
        self.value.setText(str(value))
        if subtitle is not None:
            self.subtitle.setText(subtitle)

class DataTable(QTableWidget):
    def __init__(self, headers, parent=None):
        super().__init__(0, len(headers), parent)
        self.setHorizontalHeaderLabels(headers)
        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.verticalHeader().setVisible(False)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.horizontalHeader().setStretchLastSection(True)

    def set_rows(self, rows):
        self.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, val in enumerate(row):
                item = QTableWidgetItem("" if val is None else str(val))
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.setItem(r, c, item)

class EmptyState(QWidget):
    action = Signal()
    def __init__(self, title, description, action_text="", parent=None):
        super().__init__(parent)
        lay=QVBoxLayout(self)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        t=QLabel(title); t.setStyleSheet("font-size:16pt;font-weight:700;color:#334155;")
        d=QLabel(description); d.setWordWrap(True); d.setAlignment(Qt.AlignmentFlag.AlignCenter); d.setStyleSheet("color:#64748b;")
        lay.addWidget(t); lay.addWidget(d)
        if action_text:
            b=QPushButton(action_text); b.clicked.connect(self.action.emit)
            lay.addWidget(b, alignment=Qt.AlignmentFlag.AlignCenter)
