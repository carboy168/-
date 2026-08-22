from __future__ import annotations
import json, os, time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

@dataclass(frozen=True)
class ProviderConfig:
    provider_id: str; model: str; api_key: str = field(default="", repr=False)
    base_url: str = ""; timeout: float = 60.0; enabled: bool = True

@dataclass(frozen=True)
class ChatRequest:
    model: str; messages: list[dict[str, Any]]; system_prompt: str = ""
    temperature: float | None = None; max_output_tokens: int | None = None
    timeout: float | None = None; metadata: dict[str, Any] = field(default_factory=dict)

@dataclass(frozen=True)
class ChatResponse:
    text: str; provider: str; model: str; usage: dict[str, Any] = field(default_factory=dict)
    finish_reason: str | None = None; latency: float = 0.0
    raw_metadata: dict[str, Any] = field(default_factory=dict)

class ProviderError(RuntimeError):
    code = "provider_error"
    def __init__(self, message: str, *, provider: str = "", status_code: int | None = None):
        super().__init__(message); self.provider=provider; self.status_code=status_code
class ProviderConfigurationError(ProviderError): code="configuration"
class ProviderAuthenticationError(ProviderError): code="authentication"
class ProviderPermissionError(ProviderError): code="permission"
class ProviderRateLimitError(ProviderError): code="rate_limit"
class ProviderTimeoutError(ProviderError): code="timeout"
class ProviderNetworkError(ProviderError): code="network"
class ProviderServerError(ProviderError): code="server"
class ProviderModelError(ProviderError): code="model_not_found"
class ProviderCapabilityError(ProviderError): code="unsupported_capability"

class LLMProvider(ABC):
    provider_id="unknown"
    def __init__(self, config: ProviderConfig):
        self.config=config
        if not config.api_key.strip(): raise ProviderConfigurationError("API Key 未配置。",provider=config.provider_id)
        if not config.model.strip(): raise ProviderConfigurationError("模型名称未配置。",provider=config.provider_id)
    @abstractmethod
    def chat(self, request: ChatRequest) -> ChatResponse: raise NotImplementedError
    def test_connection(self, *, model: str | None = None) -> ChatResponse:
        return self.chat(ChatRequest(model=model or self.config.model,messages=[{"role":"user","content":"只回复：连接正常"}],max_output_tokens=16))
    def generate(self, *, model: str, input: Any, instructions: str | None = None) -> str:
        messages=input if isinstance(input,list) else [{"role":"user","content":input}]
        return self.chat(ChatRequest(model=model,messages=messages,system_prompt=instructions or "")).text
AIProvider=LLMProvider

def _usage_dict(usage: Any) -> dict[str,Any]:
    if usage is None:return {}
    if hasattr(usage,"model_dump"):return usage.model_dump()
    if isinstance(usage,dict):return dict(usage)
    return {k:getattr(usage,k) for k in ("input_tokens","output_tokens","total_tokens","prompt_tokens","completion_tokens") if hasattr(usage,k)}

def _raise_provider_error(exc:Exception,provider_id:str):
    status=getattr(exc,"status_code",None); name=exc.__class__.__name__.lower(); detail=str(exc).lower(); kw={"provider":provider_id,"status_code":status}
    if status==401:raise ProviderAuthenticationError("API Key 无效或已过期。",**kw) from None
    if status==403:raise ProviderPermissionError("账号或 API Key 权限不足。",**kw) from None
    if status==429:raise ProviderRateLimitError("请求过于频繁或额度受限。",**kw) from None
    if status and status>=500:raise ProviderServerError("模型服务暂时不可用。",**kw) from None
    if "timeout" in name or "timed out" in detail:raise ProviderTimeoutError("连接模型服务超时。",**kw) from None
    if "model" in detail and any(x in detail for x in ("not found","does not exist","invalid")):raise ProviderModelError("模型不存在或当前账号不可用。",**kw) from None
    if any(x in name+detail for x in ("connection","dns","proxy","network")):raise ProviderNetworkError("网络、DNS 或代理无法连接模型服务。",**kw) from None
    raise ProviderError("模型服务调用失败。",**kw) from None

class OpenAIResponsesProvider(LLMProvider):
    provider_id="openai"
    def chat(self,request:ChatRequest)->ChatResponse:
        started=time.perf_counter()
        try:
            from openai import OpenAI
            client=OpenAI(api_key=self.config.api_key,base_url=self.config.base_url or None,timeout=request.timeout or self.config.timeout)
            kw={"model":request.model,"input":request.messages}
            if request.system_prompt:kw["instructions"]=request.system_prompt
            if request.temperature is not None:kw["temperature"]=request.temperature
            if request.max_output_tokens is not None:kw["max_output_tokens"]=request.max_output_tokens
            response=client.responses.create(**kw)
            return ChatResponse(response.output_text,self.provider_id,request.model,_usage_dict(getattr(response,"usage",None)),getattr(response,"status",None),time.perf_counter()-started,{"response_id":getattr(response,"id",None)})
        except ProviderError:raise
        except Exception as exc:_raise_provider_error(exc,self.provider_id)

def _chat_content(content:Any)->Any:
    if not isinstance(content,list):return content
    out=[]
    for item in content:
        kind=item.get("type")
        if kind=="input_text":out.append({"type":"text","text":item.get("text","")})
        elif kind=="input_image":out.append({"type":"image_url","image_url":{"url":item.get("image_url",""),"detail":item.get("detail","auto")}})
        elif kind=="input_file":raise ProviderCapabilityError("当前 Provider 不支持直接文件输入，请使用 OpenAI 或先转换为文本/图片。")
        else:out.append(item)
    return out

class OpenAICompatibleProvider(LLMProvider):
    def chat(self,request:ChatRequest)->ChatResponse:
        started=time.perf_counter()
        try:
            from openai import OpenAI
            client=OpenAI(api_key=self.config.api_key,base_url=self.config.base_url,timeout=request.timeout or self.config.timeout)
            messages=[]
            if request.system_prompt:messages.append({"role":"system","content":request.system_prompt})
            messages += [{**m,"content":_chat_content(m.get("content"))} for m in request.messages]
            kw={"model":request.model,"messages":messages}
            if request.temperature is not None:kw["temperature"]=request.temperature
            if request.max_output_tokens is not None:kw["max_tokens"]=request.max_output_tokens
            response=client.chat.completions.create(**kw); choice=response.choices[0]
            return ChatResponse(choice.message.content or "",self.provider_id,request.model,_usage_dict(getattr(response,"usage",None)),getattr(choice,"finish_reason",None),time.perf_counter()-started,{"response_id":getattr(response,"id",None)})
        except ProviderError:raise
        except Exception as exc:_raise_provider_error(exc,self.provider_id)

class DeepSeekProvider(OpenAICompatibleProvider):provider_id="deepseek"
class QwenProvider(OpenAICompatibleProvider):provider_id="qwen"
class ZhipuProvider(OpenAICompatibleProvider):provider_id="zhipu"

class ProviderRegistry:
    def __init__(self):self._factories:dict[str,Callable[[ProviderConfig],LLMProvider]]={}
    def register(self,provider_id:str,factory:Callable[[ProviderConfig],LLMProvider]):self._factories[provider_id]=factory
    def create(self,config:ProviderConfig)->LLMProvider:
        if config.provider_id not in self._factories:raise ProviderConfigurationError(f"未知 Provider：{config.provider_id}")
        return self._factories[config.provider_id](config)
    def ids(self)->tuple[str,...]:return tuple(self._factories)
REGISTRY=ProviderRegistry()
for _id,_factory in (("openai",OpenAIResponsesProvider),("deepseek",DeepSeekProvider),("qwen",QwenProvider),("zhipu",ZhipuProvider)):REGISTRY.register(_id,_factory)

def load_provider_catalog(path:Path|None=None)->dict[str,dict[str,Any]]:
    source=path or Path(__file__).resolve().parent/"data"/"provider_catalog.json"
    return {x["id"]:x for x in json.loads(source.read_text(encoding="utf-8"))["providers"]}

class ProviderManager:
    def __init__(self,registry:ProviderRegistry=REGISTRY):self.registry=registry
    def create(self,config:ProviderConfig)->LLMProvider:return self.registry.create(config)

def get_provider(name:str|None=None,*,api_key:str|None=None,model:str|None=None,base_url:str|None=None,timeout:float|None=None)->LLMProvider:
    provider_id=(name or os.getenv("AI_PROVIDER","openai")).strip().lower(); catalog=load_provider_catalog().get(provider_id,{})
    prefix=catalog.get("env_prefix",provider_id.upper())
    config=ProviderConfig(provider_id,model or os.getenv(f"{prefix}_MODEL","") or os.getenv("OPENAI_MODEL",""),api_key if api_key is not None else os.getenv(f"{prefix}_API_KEY",""),base_url if base_url is not None else os.getenv(f"{prefix}_BASE_URL","") or catalog.get("default_base_url",""),timeout or float(os.getenv(f"{prefix}_TIMEOUT","60")))
    return REGISTRY.create(config)
