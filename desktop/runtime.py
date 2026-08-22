from __future__ import annotations
import json, logging, os, shutil, sys, traceback, zipfile
from datetime import datetime
from pathlib import Path

APP_NAME = "工程规范智能体"
APP_ID = "EngineeringNormAgent"
APP_VERSION = "1.0.0-desktop"
PUBLISHER = "Engineering Norm Agent"

def resource_root() -> Path:
    if getattr(sys, "frozen", False):
        # PyInstaller onedir: bundled resources sit next to the executable / _internal.
        candidate = Path(sys.executable).resolve().parent
        internal = candidate / "_internal"
        if (internal / "data").exists():
            return internal
        return candidate
    return Path(__file__).resolve().parents[1]

def user_data_root() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / APP_ID
    return Path.home() / ".engineering_norm_agent"

RESOURCE_ROOT = resource_root()
USER_ROOT = user_data_root()
DB_DIR = USER_ROOT / "database"
PROJECT_DIR = USER_ROOT / "projects"
STANDARD_DOCS_DIR = USER_ROOT / "standards"
BACKUP_DIR = USER_ROOT / "backups"
LOG_DIR = USER_ROOT / "logs"
SETTINGS_DIR = USER_ROOT / "settings"
EXPORT_DIR = USER_ROOT / "exports"
CACHE_DIR = USER_ROOT / "cache"
DB_PATH = DB_DIR / "norms.db"
SETTINGS_PATH = SETTINGS_DIR / "desktop_settings.json"

def ensure_dirs():
    for d in [USER_ROOT, DB_DIR, PROJECT_DIR, STANDARD_DOCS_DIR, BACKUP_DIR, LOG_DIR, SETTINGS_DIR, EXPORT_DIR, CACHE_DIR]:
        d.mkdir(parents=True, exist_ok=True)

class SettingsStore:
    def __init__(self):
        ensure_dirs()
        self.path = SETTINGS_PATH
        self.data = {}
        self.load()

    def load(self):
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        except Exception:
            self.data = {}
        return self.data

    def save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value
        self.save()

def configure_environment():
    ensure_dirs()
    os.environ["DATABASE_PATH"] = str(DB_PATH)
    os.environ["PROJECT_DATA_DIR"] = str(PROJECT_DIR)
    os.environ["STANDARD_DOCS_DIR"] = str(STANDARD_DOCS_DIR)
    # Legacy modules with relative reads should resolve against the read-only application resource folder.
    os.chdir(RESOURCE_ROOT)

def configure_logging():
    ensure_dirs()
    log_file = LOG_DIR / f"desktop_{datetime.now():%Y%m%d}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        handlers=[logging.FileHandler(log_file, encoding="utf-8"), logging.StreamHandler(sys.stderr)]
    )
    return log_file

def install_crash_hook():
    def hook(exc_type, exc, tb):
        logging.critical("Unhandled exception", exc_info=(exc_type, exc, tb))
        try:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(None, "工程规范智能体", f"程序发生错误，已写入日志：\n{LOG_DIR}\n\n{exc}")
        except Exception:
            pass
    sys.excepthook = hook

def _copy_seed_db_if_needed():
    if DB_PATH.exists():
        return False
    seed = RESOURCE_ROOT / "data" / "norms.db"
    if seed.exists():
        shutil.copy2(seed, DB_PATH)
        return True
    return False

def backup_database(reason="upgrade"):
    if not DB_PATH.exists():
        return None
    ensure_dirs()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = BACKUP_DIR / f"norms_{reason}_{stamp}.db"
    shutil.copy2(DB_PATH, out)
    return out

def backup_user_data():
    ensure_dirs()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = BACKUP_DIR / f"工程规范智能体备份_{stamp}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for base in [DB_DIR, PROJECT_DIR, SETTINGS_DIR]:
            if not base.exists():
                continue
            for f in base.rglob("*"):
                if f.is_file():
                    z.write(f, f.relative_to(USER_ROOT))
    return out

def initialize_backend():
    """Prepare writable DB + migrations. Must run before importing the UI pages."""
    configure_environment()
    settings = SettingsStore()
    is_new = not DB_PATH.exists()
    _copy_seed_db_if_needed()
    previous = settings.get("db_schema_app_version", "")
    if DB_PATH.exists() and previous and previous != APP_VERSION:
        try:
            backup_database("pre_upgrade")
        except Exception:
            logging.exception("Could not back up database before migration")

    from db import init_db, connect
    init_db()

    # Seed core catalogue if the seed DB was not bundled or is empty.
    with connect() as con:
        n = con.execute("SELECT COUNT(*) AS n FROM standards").fetchone()["n"]
    if n == 0:
        try:
            import seed_core_standards
            seed_core_standards.main()
        except Exception:
            logging.exception("Core standards seed failed")

    from migrations import migrate
    migrate()

    settings.set("db_schema_app_version", APP_VERSION)
    if is_new:
        settings.set("first_run_completed", True)
    return settings
