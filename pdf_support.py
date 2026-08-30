from __future__ import annotations
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import DependencyError, FileNotDecryptedError, WrongPasswordError


PDF_PASSWORD_MESSAGE = "PDF 已加密且需要密码，请先使用有权限的工具解密后再导入。"
PDF_CRYPTO_MESSAGE = "当前安装缺少 PDF AES 解密依赖 cryptography，请安装完整版本后重试。"


def open_pdf_reader(path: str | Path) -> PdfReader:
    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted:
            try:
                if not reader.decrypt(""):
                    raise ValueError(PDF_PASSWORD_MESSAGE)
            except (FileNotDecryptedError, WrongPasswordError):
                raise ValueError(PDF_PASSWORD_MESSAGE) from None
        return reader
    except DependencyError:
        raise RuntimeError(PDF_CRYPTO_MESSAGE) from None
    except (FileNotDecryptedError, WrongPasswordError):
        raise ValueError(PDF_PASSWORD_MESSAGE) from None
