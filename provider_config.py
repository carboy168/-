from __future__ import annotations
import os
from typing import Any
from db import connect
from provider import ProviderConfig, get_provider, load_provider_catalog

def list_provider_configs()->list[dict[str,Any]]:
    with connect() as con:return [dict(r) for r in con.execute("SELECT * FROM provider_configs ORDER BY is_default DESC,provider_id")]

def get_provider_config(provider_id:str|None=None)->dict[str,Any]|None:
    with connect() as con:
        row=con.execute("SELECT * FROM provider_configs WHERE provider_id=?",(provider_id,)).fetchone() if provider_id else con.execute("SELECT * FROM provider_configs WHERE enabled=1 ORDER BY is_default DESC,provider_id LIMIT 1").fetchone()
        return dict(row) if row else None

def save_provider_config(provider_id:str,model:str,base_url:str="",timeout:float=60,enabled:bool=True,is_default:bool=False,secret_ref:str="")->None:
    if provider_id not in load_provider_catalog():raise ValueError(f"未知 Provider：{provider_id}")
    with connect() as con:
        if is_default:con.execute("UPDATE provider_configs SET is_default=0")
        con.execute("""INSERT INTO provider_configs(provider_id,model,base_url,timeout,enabled,is_default,secret_ref,key_configured)
        VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(provider_id) DO UPDATE SET model=excluded.model,base_url=excluded.base_url,
        timeout=excluded.timeout,enabled=excluded.enabled,is_default=excluded.is_default,secret_ref=excluded.secret_ref,
        key_configured=excluded.key_configured,updated_at=CURRENT_TIMESTAMP""",
        (provider_id,model,base_url,float(timeout),int(enabled),int(is_default),secret_ref,int(bool(secret_ref))))

def set_key_configured(provider_id:str,configured:bool,secret_ref:str="")->None:
    with connect() as con:con.execute("UPDATE provider_configs SET key_configured=?,secret_ref=?,updated_at=CURRENT_TIMESTAMP WHERE provider_id=?",(int(configured),secret_ref if configured else "",provider_id))

def migrate_legacy_openai_settings(settings)->None:
    if get_provider_config("openai"):return
    model=(settings.get("openai_model","") or "").strip(); review=(settings.get("openai_review_model","") or "").strip()
    if model:
        from desktop.secret_store import load_provider_secret
        secret_ref="windows-credential:openai" if load_provider_secret("openai") else ""
        save_provider_config("openai",model,timeout=60,enabled=True,is_default=True,secret_ref=secret_ref)
    if review:settings.set("review_model_override",review)

def resolve_provider(provider_id:str|None=None,*,purpose:str="chat"):
    from desktop.secret_store import load_provider_secret
    row=get_provider_config(provider_id)
    if not row:
        # Backward compatible environment-only OpenAI setup.
        return get_provider(provider_id or os.getenv("AI_PROVIDER","openai"))
    pid=row["provider_id"]; catalog=load_provider_catalog()[pid]; prefix=catalog["env_prefix"]
    key=os.getenv(f"{prefix}_API_KEY","").strip() or load_provider_secret(pid)
    model=row["model"]
    if purpose=="review":model=os.getenv(f"{prefix}_REVIEW_MODEL","").strip() or model
    return get_provider(pid,api_key=key,model=model,base_url=row["base_url"],timeout=float(row["timeout"]))
