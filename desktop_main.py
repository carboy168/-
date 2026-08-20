from __future__ import annotations
import os, sys

def main():
    from desktop.runtime import configure_environment, configure_logging, install_crash_hook, initialize_backend, RESOURCE_ROOT
    configure_environment()
    log = configure_logging()
    install_crash_hook()
    settings = initialize_backend()

    from desktop.secret_store import load_api_key
    try:
        key = load_api_key()
        if key: os.environ["OPENAI_API_KEY"] = key
    except Exception:
        pass
    model=settings.get("openai_model","")
    review_model=settings.get("openai_review_model","")
    if model:os.environ["OPENAI_MODEL"]=model
    if review_model:os.environ["OPENAI_REVIEW_MODEL"]=review_model

    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication
    from desktop.ui.theme import APP_QSS
    from desktop.ui.main_window import MainWindow

    app=QApplication(sys.argv)
    app.setApplicationName("工程规范智能体")
    app.setOrganizationName("EngineeringNormAgent")
    app.setStyleSheet(APP_QSS)
    icon=RESOURCE_ROOT/"assets"/"app.ico"
    if icon.exists(): app.setWindowIcon(QIcon(str(icon)))
    w=MainWindow();w.show()
    return app.exec()

if __name__=="__main__":
    raise SystemExit(main())
