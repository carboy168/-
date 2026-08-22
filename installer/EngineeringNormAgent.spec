# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import os
from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(os.getcwd()).resolve()
datas = []
for folder in ["data","config","prompts","assets"]:
    base = ROOT / folder
    if base.exists():
        for f in base.rglob("*"):
            if f.is_file() and "source_docs" not in f.parts and "projects" not in f.parts:
                datas.append((str(f), str(f.parent.relative_to(ROOT))))
for f in ROOT.glob("*.xlsx"):
    datas.append((str(f), "."))

hiddenimports = collect_submodules("openai") + collect_submodules("PySide6")

a = Analysis(
    [str(ROOT / "desktop_main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["streamlit"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="EngineeringNormAgent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "assets" / "app.ico"),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="EngineeringNormAgent",
)
