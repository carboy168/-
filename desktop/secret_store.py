from __future__ import annotations
import os

SERVICE = "EngineeringNormAgent.OpenAI"
TARGET = "EngineeringNormAgent/OpenAI_API_KEY"

def _windows():
    return os.name == "nt"

def save_api_key(api_key: str):
    api_key = (api_key or "").strip()
    if not _windows():
        os.environ["OPENAI_API_KEY"] = api_key
        return
    import ctypes
    from ctypes import wintypes

    CRED_TYPE_GENERIC = 1
    CRED_PERSIST_LOCAL_MACHINE = 2

    class FILETIME(ctypes.Structure):
        _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]

    class CREDENTIALW(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD),
            ("Type", wintypes.DWORD),
            ("TargetName", wintypes.LPWSTR),
            ("Comment", wintypes.LPWSTR),
            ("LastWritten", FILETIME),
            ("CredentialBlobSize", wintypes.DWORD),
            ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
            ("Persist", wintypes.DWORD),
            ("AttributeCount", wintypes.DWORD),
            ("Attributes", ctypes.c_void_p),
            ("TargetAlias", wintypes.LPWSTR),
            ("UserName", wintypes.LPWSTR),
        ]

    advapi = ctypes.WinDLL("Advapi32.dll")
    CredWriteW = advapi.CredWriteW
    CredWriteW.argtypes = [ctypes.POINTER(CREDENTIALW), wintypes.DWORD]
    CredWriteW.restype = wintypes.BOOL

    raw = api_key.encode("utf-16-le")
    blob = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw) if raw else None
    cred = CREDENTIALW()
    cred.Flags = 0
    cred.Type = CRED_TYPE_GENERIC
    cred.TargetName = TARGET
    cred.Comment = "工程规范智能体 OpenAI API Key"
    cred.CredentialBlobSize = len(raw)
    cred.CredentialBlob = ctypes.cast(blob, ctypes.POINTER(ctypes.c_ubyte)) if blob else None
    cred.Persist = CRED_PERSIST_LOCAL_MACHINE
    cred.UserName = "OpenAI"
    if not CredWriteW(ctypes.byref(cred), 0):
        raise ctypes.WinError()
    os.environ["OPENAI_API_KEY"] = api_key

def load_api_key() -> str:
    env = os.getenv("OPENAI_API_KEY", "").strip()
    if env:
        return env
    if not _windows():
        return ""
    import ctypes
    from ctypes import wintypes

    CRED_TYPE_GENERIC = 1

    class FILETIME(ctypes.Structure):
        _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]

    class CREDENTIALW(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD),
            ("Type", wintypes.DWORD),
            ("TargetName", wintypes.LPWSTR),
            ("Comment", wintypes.LPWSTR),
            ("LastWritten", FILETIME),
            ("CredentialBlobSize", wintypes.DWORD),
            ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
            ("Persist", wintypes.DWORD),
            ("AttributeCount", wintypes.DWORD),
            ("Attributes", ctypes.c_void_p),
            ("TargetAlias", wintypes.LPWSTR),
            ("UserName", wintypes.LPWSTR),
        ]

    advapi = ctypes.WinDLL("Advapi32.dll")
    CredReadW = advapi.CredReadW
    CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.POINTER(CREDENTIALW))]
    CredReadW.restype = wintypes.BOOL
    CredFree = advapi.CredFree
    CredFree.argtypes = [ctypes.c_void_p]

    pcred = ctypes.POINTER(CREDENTIALW)()
    if not CredReadW(TARGET, CRED_TYPE_GENERIC, 0, ctypes.byref(pcred)):
        return ""
    try:
        cred = pcred.contents
        if not cred.CredentialBlob or not cred.CredentialBlobSize:
            return ""
        data = ctypes.string_at(cred.CredentialBlob, cred.CredentialBlobSize)
        key = data.decode("utf-16-le")
        os.environ["OPENAI_API_KEY"] = key
        return key
    finally:
        CredFree(pcred)

def delete_api_key():
    os.environ.pop("OPENAI_API_KEY", None)
    if not _windows():
        return
    import ctypes
    from ctypes import wintypes
    advapi = ctypes.WinDLL("Advapi32.dll")
    fn = advapi.CredDeleteW
    fn.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    fn.restype = wintypes.BOOL
    fn(TARGET, 1, 0)
