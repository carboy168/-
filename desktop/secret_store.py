from __future__ import annotations
import os
from abc import ABC, abstractmethod

ENV_NAMES={"openai":"OPENAI_API_KEY","deepseek":"DEEPSEEK_API_KEY","qwen":"QWEN_API_KEY","zhipu":"ZHIPU_API_KEY"}
TARGET_PREFIX="EngineeringNormAgent/Provider/"
LEGACY_TARGET="EngineeringNormAgent/OpenAI_API_KEY"

class SecretStore(ABC):
    @abstractmethod
    def save(self,provider_id:str,secret:str)->str:raise NotImplementedError
    @abstractmethod
    def load(self,provider_id:str)->str:raise NotImplementedError
    @abstractmethod
    def delete(self,provider_id:str)->None:raise NotImplementedError

def _target(provider_id:str)->str:return TARGET_PREFIX+provider_id.lower()
def _env(provider_id:str)->str:return ENV_NAMES.get(provider_id,provider_id.upper()+"_API_KEY")

class EnvironmentSecretStore(SecretStore):
    def save(self,provider_id:str,secret:str)->str:
        os.environ[_env(provider_id)]=secret;return "environment:"+provider_id
    def load(self,provider_id:str)->str:return os.getenv(_env(provider_id),"").strip()
    def delete(self,provider_id:str)->None:os.environ.pop(_env(provider_id),None)

class WindowsCredentialSecretStore(SecretStore):
    def _types(self):
        import ctypes
        from ctypes import wintypes
        class FILETIME(ctypes.Structure):_fields_=[("dwLowDateTime",wintypes.DWORD),("dwHighDateTime",wintypes.DWORD)]
        class CREDENTIALW(ctypes.Structure):
            _fields_=[("Flags",wintypes.DWORD),("Type",wintypes.DWORD),("TargetName",wintypes.LPWSTR),("Comment",wintypes.LPWSTR),("LastWritten",FILETIME),("CredentialBlobSize",wintypes.DWORD),("CredentialBlob",ctypes.POINTER(ctypes.c_ubyte)),("Persist",wintypes.DWORD),("AttributeCount",wintypes.DWORD),("Attributes",ctypes.c_void_p),("TargetAlias",wintypes.LPWSTR),("UserName",wintypes.LPWSTR)]
        return ctypes,wintypes,CREDENTIALW
    def save(self,provider_id:str,secret:str)->str:
        ctypes,wintypes,CREDENTIALW=self._types();raw=secret.encode("utf-16-le");blob=(ctypes.c_ubyte*len(raw)).from_buffer_copy(raw)
        cred=CREDENTIALW();cred.Type=1;cred.TargetName=_target(provider_id);cred.Comment="工程规范智能体 Provider API Key";cred.CredentialBlobSize=len(raw);cred.CredentialBlob=ctypes.cast(blob,ctypes.POINTER(ctypes.c_ubyte));cred.Persist=2;cred.UserName=provider_id
        fn=ctypes.WinDLL("Advapi32.dll").CredWriteW;fn.argtypes=[ctypes.POINTER(CREDENTIALW),wintypes.DWORD];fn.restype=wintypes.BOOL
        if not fn(ctypes.byref(cred),0):raise ctypes.WinError()
        os.environ[_env(provider_id)]=secret;return "windows-credential:"+provider_id
    def _read_target(self,target:str)->str:
        ctypes,wintypes,CREDENTIALW=self._types();advapi=ctypes.WinDLL("Advapi32.dll");read=advapi.CredReadW;read.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.POINTER(ctypes.POINTER(CREDENTIALW))];read.restype=wintypes.BOOL
        free=advapi.CredFree;free.argtypes=[ctypes.c_void_p];p=ctypes.POINTER(CREDENTIALW)()
        if not read(target,1,0,ctypes.byref(p)):return ""
        try:
            c=p.contents;return ctypes.string_at(c.CredentialBlob,c.CredentialBlobSize).decode("utf-16-le") if c.CredentialBlob and c.CredentialBlobSize else ""
        finally:free(p)
    def load(self,provider_id:str)->str:
        env=os.getenv(_env(provider_id),"").strip()
        if env:return env
        value=self._read_target(_target(provider_id))
        if not value and provider_id=="openai":value=self._read_target(LEGACY_TARGET)
        if value:os.environ[_env(provider_id)]=value
        return value
    def delete(self,provider_id:str)->None:
        import ctypes
        from ctypes import wintypes
        os.environ.pop(_env(provider_id),None);fn=ctypes.WinDLL("Advapi32.dll").CredDeleteW;fn.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD];fn.restype=wintypes.BOOL;fn(_target(provider_id),1,0)

def get_secret_store()->SecretStore:return WindowsCredentialSecretStore() if os.name=="nt" else EnvironmentSecretStore()
def save_provider_secret(provider_id:str,secret:str)->str:return get_secret_store().save(provider_id,(secret or "").strip())
def load_provider_secret(provider_id:str)->str:return get_secret_store().load(provider_id)
def delete_provider_secret(provider_id:str)->None:get_secret_store().delete(provider_id)

# V1.0 compatibility wrappers.
def save_api_key(api_key:str):return save_provider_secret("openai",api_key)
def load_api_key()->str:return load_provider_secret("openai")
def delete_api_key():return delete_provider_secret("openai")
