from __future__ import annotations
import os,subprocess,sys
from PySide6.QtWidgets import QComboBox,QDoubleSpinBox,QFormLayout,QHBoxLayout,QLabel,QLineEdit,QMessageBox,QPushButton,QVBoxLayout,QWidget
from desktop.runtime import USER_ROOT,backup_user_data
from desktop.secret_store import delete_provider_secret,load_provider_secret,save_provider_secret
from provider import ProviderConfig,ProviderError,REGISTRY,load_provider_catalog
from provider_config import get_provider_config,save_provider_config,set_key_configured

class SettingsPage(QWidget):
    settings_changed=__import__("PySide6.QtCore",fromlist=["Signal"]).Signal()
    def __init__(self,parent=None):
        super().__init__(parent);self.catalog=load_provider_catalog();lay=QVBoxLayout(self);form=QFormLayout()
        self.provider=QComboBox()
        for pid,item in self.catalog.items():self.provider.addItem(item["display_name"],pid)
        self.model=QLineEdit();self.model.setPlaceholderText("可手动填写任意账号可用模型")
        self.api=QLineEdit();self.api.setEchoMode(QLineEdit.EchoMode.Password);self.api.setPlaceholderText("未配置")
        self.base_url=QLineEdit();self.timeout=QDoubleSpinBox();self.timeout.setRange(5,600);self.timeout.setValue(60);self.timeout.setSuffix(" 秒")
        form.addRow("Provider",self.provider);form.addRow("Model",self.model);form.addRow("API Key",self.api);form.addRow("Base URL（高级）",self.base_url);form.addRow("Timeout",self.timeout)
        path=QLabel(str(USER_ROOT));path.setTextInteractionFlags(path.textInteractionFlags()|__import__("PySide6.QtCore",fromlist=["Qt"]).Qt.TextInteractionFlag.TextSelectableByMouse);form.addRow("本机数据目录",path);lay.addLayout(form)
        bar=QHBoxLayout();self.save=QPushButton("保存并设为默认");self.test=QPushButton("测试连接");self.test.setObjectName("Secondary");self.delete_key=QPushButton("删除密钥/重新配置");self.delete_key.setObjectName("Secondary");self.open_data=QPushButton("打开数据目录");self.open_data.setObjectName("Secondary");self.backup=QPushButton("立即备份");self.backup.setObjectName("Secondary")
        for b in (self.save,self.test,self.delete_key,self.open_data,self.backup):bar.addWidget(b)
        bar.addStretch();lay.addLayout(bar)
        note=QLabel("API Key 仅保存在 Windows Credential Manager；数据库只保存非敏感配置和 secret reference。不会静默切换供应商。")
        note.setWordWrap(True);note.setStyleSheet("color:#64748b;");lay.addWidget(note);lay.addStretch()
        self.provider.currentIndexChanged.connect(self.load_selected);self.save.clicked.connect(self.save_settings);self.test.clicked.connect(self.test_api);self.delete_key.clicked.connect(self.remove_key);self.open_data.clicked.connect(self.open_folder);self.backup.clicked.connect(self.do_backup)
        self.load_selected()
    def provider_id(self):return self.provider.currentData()
    def load_selected(self):
        pid=self.provider_id();item=self.catalog[pid];row=get_provider_config(pid)
        self.model.setText(row["model"] if row else (item.get("model_suggestions") or [""])[0]);self.base_url.setText(row["base_url"] if row else item.get("default_base_url",""));self.timeout.setValue(float(row["timeout"]) if row else 60)
        configured=bool(row and row.get("key_configured")) or bool(os.getenv(item["env_prefix"]+"_API_KEY",""));self.api.clear();self.api.setPlaceholderText("已安全配置（留空保持不变）" if configured else "未配置")
    def _key(self):return self.api.text().strip() or load_provider_secret(self.provider_id())
    def save_settings(self):
        pid=self.provider_id();key=self.api.text().strip()
        try:
            secret_ref=""
            if key:secret_ref=save_provider_secret(pid,key)
            else:
                old=get_provider_config(pid);secret_ref=(old or {}).get("secret_ref","")
            save_provider_config(pid,self.model.text().strip(),self.base_url.text().strip(),self.timeout.value(),True,True,secret_ref)
            self.settings_changed.emit();self.load_selected();QMessageBox.information(self,"AI配置","Provider 配置已保存并设为默认。")
        except Exception as exc:QMessageBox.critical(self,"保存失败",str(exc))
    def test_api(self):
        pid=self.provider_id();key=self._key();model=self.model.text().strip()
        if not key or not model:QMessageBox.warning(self,"连接测试","请先配置 API Key 和模型。 ");return
        try:
            p=REGISTRY.create(ProviderConfig(pid,model,key,self.base_url.text().strip(),self.timeout.value()));response=p.test_connection();QMessageBox.information(self,"连接成功",response.text[:200])
        except ProviderError as exc:QMessageBox.critical(self,"连接失败",str(exc))
        except Exception:QMessageBox.critical(self,"连接失败","配置或本机安全存储发生错误，请检查设置和日志。")
    def remove_key(self):
        pid=self.provider_id();delete_provider_secret(pid);set_key_configured(pid,False);self.api.clear();self.load_selected();self.settings_changed.emit();QMessageBox.information(self,"AI配置","该 Provider 的密钥已删除，可重新配置。")
    def open_folder(self):
        path=str(USER_ROOT)
        if os.name=="nt":os.startfile(path)
        elif sys.platform=="darwin":subprocess.Popen(["open",path])
        else:subprocess.Popen(["xdg-open",path])
    def do_backup(self):
        try:QMessageBox.information(self,"备份完成",f"备份已保存：\n{backup_user_data()}")
        except Exception as exc:QMessageBox.critical(self,"备份失败",str(exc))
