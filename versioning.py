from __future__ import annotations

import configparser
import re
import sys
from functools import lru_cache
from pathlib import Path


VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")


def version_file() -> Path:
    if getattr(sys, "frozen", False):
        root = Path(sys.executable).resolve().parent
        internal = root / "_internal"
        if (internal / "version.ini").exists():
            return internal / "version.ini"
        return root / "version.ini"
    return Path(__file__).resolve().parent / "version.ini"


@lru_cache(maxsize=1)
def app_version() -> str:
    parser = configparser.ConfigParser()
    path = version_file()
    if not path.exists() or not parser.read(path, encoding="utf-8"):
        raise RuntimeError(f"缺少版本文件：{path}")
    value = parser.get("version", "app_version", fallback="").strip()
    if not VERSION_PATTERN.fullmatch(value):
        raise RuntimeError("version.ini 中的 app_version 不是有效语义版本。")
    return value


APP_VERSION = app_version()

