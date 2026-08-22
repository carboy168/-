from __future__ import annotations
import os
from typing import Any
from db import connect
from provider import ProviderConfig, ProviderConfigurationError, get_provider, load_provider_catalog

MODEL_HINTS={"deepseek":("deepseek-",),"qwen":("qwen","qwq","qvq"),"zhipu":("glm-","codegeex"),"openai":("gpt-","chatgpt-","o1","o3","o4","text-embedding-","dall-e")}
URL_HINTS={"deepseek":("deepseek.com",),"qwen":("dashscope.aliyuncs.com","maas.aliyuncs.com"),"zhipu":("bigmodel.cn","zhipuai.cn"),"openai":("api.openai.com","openai.azure.com")}

def _provider_signals(model:str="",base_url:str="")->set[str]:
    model_value=(model or "").strip().lower();url_value=(base_url or "").strip().lower()
    signals={pid for pid,hints in MODEL_HINTS.items() if any(model_value.startswith(x) for x in hints)}
    signals|={pid for pid,hints in URL_HINTS.items() if any(x in url_value for x in hints)}
    return signals

def infer_provider_id(model:str="",base_url:str="")->str|None:
    """Infer ownership only when every recognizable legacy signal agrees."""
    signals=_provider_signals(model,base_url)
    return next(iter(signals)) if len(signals)==1 else None

def _is_usable(row:dict[str,Any])->bool:
    if row.get("needs_confirmation"):return False
    signals=_provider_signals(row.get("model",""),row.get("base_url",""))
    return not signals or signals=={row["provider_id"]}

def list_provider_configs()->list[dict[str,Any]]:
    with connect() as con:return [dict(r) for r in con.execute("SELECT * FROM provider_configs ORDER BY is_default DESC,provider_id")]

def get_provider_config(provider_id:str|None=None)->dict[str,Any]|None:
    with connect() as con:
        if provider_id:
            row=con.execute("SELECT * FROM provider_configs WHERE provider_id=?",(provider_id,)).fetchone();value=dict(row) if row else None
            return value if value and _is_usable(value) else None
        rows=con.execute("SELECT * FROM provider_configs WHERE enabled=1 ORDER BY is_default DESC,provider_id").fetchall()
        return next((value for value in map(dict,rows) if _is_usable(value)),None)

def save_provider_config(provider_id:str,model:str,base_url:str="",timeout:float=60,enabled:bool=True,is_default:bool=False,secret_ref:str="")->None:
    catalog=load_provider_catalog()
    if provider_id not in catalog:raise ValueError(f"未知 Provider：{provider_id}")
    signals=_provider_signals(model,base_url);inferred=infer_provider_id(model,base_url)
    if len(signals)>1:raise ValueError("模型与 Base URL 的 Provider 归属冲突，请检查配置。")
    if inferred and inferred!=provider_id:raise ValueError(f"模型或 Base URL 明显属于 {catalog[inferred]['display_name']}，请检查 Provider 选择。")
    with connect() as con:
        if is_default:con.execute("UPDATE provider_configs SET is_default=0")
        con.execute("""INSERT INTO provider_configs(provider_id,model,base_url,timeout,enabled,is_default,secret_ref,key_configured,needs_confirmation)
        VALUES(?,?,?,?,?,?,?,?,0) ON CONFLICT(provider_id) DO UPDATE SET model=excluded.model,base_url=excluded.base_url,
        timeout=excluded.timeout,enabled=excluded.enabled,is_default=excluded.is_default,secret_ref=excluded.secret_ref,
        key_configured=excluded.key_configured,needs_confirmation=0,updated_at=CURRENT_TIMESTAMP""",
        (provider_id,model,base_url,float(timeout),int(enabled),int(is_default),secret_ref,int(bool(secret_ref))))

def set_key_configured(provider_id:str,configured:bool,secret_ref:str="")->None:
    with connect() as con:con.execute("UPDATE provider_configs SET key_configured=?,secret_ref=?,updated_at=CURRENT_TIMESTAMP WHERE provider_id=?",(int(configured),secret_ref if configured else "",provider_id))

def _legacy_value(settings,*keys:str)->str:
    for key in keys:
        value=(settings.get(key,"") or "").strip()
        if value:return value
    return ""

def migrate_legacy_openai_settings(settings)->None:
    """Repair/import legacy settings while preserving every existing credential."""
    from desktop.secret_store import load_provider_secret,save_provider_secret
    review=_legacy_value(settings,"openai_review_model","review_model")
    if review:settings.set("review_model_override",review)
    with connect() as con:rows={row["provider_id"]:dict(row) for row in con.execute("SELECT * FROM provider_configs")}
    bad_openai=rows.get("openai")
    bad_owner=infer_provider_id(bad_openai["model"],bad_openai["base_url"]) if bad_openai else None
    if bad_openai and bad_owner and bad_owner!="openai":
        model=bad_openai["model"];base_url=bad_openai["base_url"]
    elif rows:return
    else:
        model=_legacy_value(settings,"openai_model","model")
        base_url=_legacy_value(settings,"openai_base_url","base_url","api_base")
    if not model and not base_url:return
    provider_id=infer_provider_id(model,base_url)
    if not provider_id:
        with connect() as con:
            con.execute("""INSERT INTO provider_configs(provider_id,model,base_url,enabled,is_default,needs_confirmation)
                VALUES('legacy-unassigned',?,?,0,0,1) ON CONFLICT(provider_id) DO UPDATE SET
                model=excluded.model,base_url=excluded.base_url,needs_confirmation=1,updated_at=CURRENT_TIMESTAMP""",(model,base_url))
        return
    existing_secret=load_provider_secret(provider_id)
    legacy_secret=load_provider_secret("openai") if provider_id!="openai" and not existing_secret else ""
    secret_ref=(rows.get(provider_id) or {}).get("secret_ref","")
    if not existing_secret and legacy_secret:secret_ref=save_provider_secret(provider_id,legacy_secret)
    elif existing_secret and not secret_ref:secret_ref="windows-credential:"+provider_id
    default_url=load_provider_catalog()[provider_id].get("default_base_url","")
    save_provider_config(provider_id,model,base_url or default_url,60,True,True,secret_ref)
    if bad_openai and provider_id!="openai":
        with connect() as con:con.execute("UPDATE provider_configs SET enabled=0,is_default=0,needs_confirmation=1 WHERE provider_id='openai'")

def resolve_provider(provider_id:str|None=None,*,purpose:str="chat"):
    from desktop.secret_store import load_provider_secret
    row=get_provider_config(provider_id)
    if not row:
        configured=list_provider_configs()
        if configured:
            raise ProviderConfigurationError("Provider 配置归属需要用户确认，请在 AI 配置中重新选择并保存。")
        # Backward compatible environment-only setup when no persisted configuration exists.
        return get_provider(provider_id or os.getenv("AI_PROVIDER","openai"))
    pid=row["provider_id"]; catalog=load_provider_catalog()[pid]; prefix=catalog["env_prefix"]
    key=os.getenv(f"{prefix}_API_KEY","").strip() or load_provider_secret(pid)
    model=row["model"]
    if purpose=="review":model=os.getenv(f"{prefix}_REVIEW_MODEL","").strip() or model
    return get_provider(pid,api_key=key,model=model,base_url=row["base_url"],timeout=float(row["timeout"]))
