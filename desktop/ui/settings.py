from __future__ import annotations
import os, subprocess, sys
from PySide6.QtWidgets import QCheckBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QVBoxLayout, QWidget
from desktop.runtime import SettingsStore, USER_ROOT, BACKUP_DIR, LOG_DIR, backup_user_data
from desktop.secret_store import load_api_key, save_api_key, delete_api_key
from provider import get_provider

class SettingsPage(QWidget):
    settings_changed = __import__("PySide6.QtCore",fromlist=["Signal"]).Signal()
    def __init__(self,parent=None):
        super().__init__(parent); self.store=SettingsStore()
        lay=QVBoxLayout(self)
        form=QFormLayout()
        self.api=QLineEdit(); self.api.setEchoMode(QLineEdit.EchoMode.Password); self.api.setPlaceholderText("sk-…（保存在Windows凭据管理器）")
        self.model=QLineEdit(self.store.get("openai_model","")); self.model.setPlaceholderText("填写您API账号可用的模型名称")
        self.review_model=QLineEdit(self.store.get("openai_review_model","")); self.review_model.setPlaceholderText("留空则与问答模型相同")
        self.remember=QCheckBox("在本机Windows凭据管理器保存API Key"); self.remember.setChecked(True)
        form.addRow("OpenAI API Key",self.api); form.addRow("",self.remember); form.addRow("问答模型",self.model); form.addRow("审图/方案模型",self.review_model)
        path=QLabel(str(USER_ROOT)); path.setTextInteractionFlags(path.textInteractionFlags()|__import__("PySide6.QtCore",fromlist=["Qt"]).Qt.TextInteractionFlag.TextSelectableByMouse)
        form.addRow("本机数据目录",path); lay.addLayout(form)

        bar=QHBoxLayout()
        self.save=QPushButton("保存设置")
        self.test=QPushButton("测试API"); self.test.setObjectName("Secondary")
        self.open_data=QPushButton("打开数据目录"); self.open_data.setObjectName("Secondary")
        self.backup=QPushButton("立即备份"); self.backup.setObjectName("Secondary")
        bar.addWidget(self.save);bar.addWidget(self.test);bar.addWidget(self.open_data);bar.addWidget(self.backup);bar.addStretch();lay.addLayout(bar)
        note=QLabel("API Key不会写入项目文件或安装目录；正式Windows运行时保存到Windows Credential Manager。模型名称请填写您的API账号实际可用型号。")
        note.setWordWrap(True); note.setStyleSheet("color:#64748b;");lay.addWidget(note);lay.addStretch()

        self.save.clicked.connect(self.save_settings); self.test.clicked.connect(self.test_api); self.open_data.clicked.connect(self.open_folder); self.backup.clicked.connect(self.do_backup)
        try:
            k=load_api_key()
            if k:self.api.setText(k)
        except Exception: pass
        self.apply_env()

    def apply_env(self):
        key=self.api.text().strip() or load_api_key()
        if key:os.environ["OPENAI_API_KEY"]=key
        model=self.model.text().strip()
        rev=self.review_model.text().strip()
        if model:os.environ["OPENAI_MODEL"]=model
        else:os.environ.pop("OPENAI_MODEL",None)
        if rev:os.environ["OPENAI_REVIEW_MODEL"]=rev
        else:os.environ.pop("OPENAI_REVIEW_MODEL",None)

    def save_settings(self):
        key=self.api.text().strip()
        try:
            if self.remember.isChecked() and key:save_api_key(key)
            elif not self.remember.isChecked(): delete_api_key()
            self.store.set("openai_model",self.model.text().strip())
            self.store.set("openai_review_model",self.review_model.text().strip())
            self.apply_env(); self.settings_changed.emit(); QMessageBox.information(self,"设置","设置已保存。")
        except Exception as e:QMessageBox.critical(self,"设置失败",str(e))

    def test_api(self):
        self.apply_env()
        key=os.getenv("OPENAI_API_KEY","").strip();model=os.getenv("OPENAI_MODEL","").strip()
        if not key or not model:QMessageBox.warning(self,"API测试","请先填写API Key和问答模型。");return
        try:
            text=get_provider(api_key=key).test_connection(model=model)
            QMessageBox.information(self,"API测试",text[:200])
        except Exception as e:QMessageBox.critical(self,"API测试失败",str(e))

    def open_folder(self):
        path=str(USER_ROOT)
        if os.name=="nt":os.startfile(path)
        elif sys.platform=="darwin":subprocess.Popen(["open",path])
        else:subprocess.Popen(["xdg-open",path])

    def do_backup(self):
        try:
            out=backup_user_data(); QMessageBox.information(self,"备份完成",f"备份已保存：\n{out}")
        except Exception as e:QMessageBox.critical(self,"备份失败",str(e))
