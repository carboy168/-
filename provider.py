from __future__ import annotations
import json, logging, os, re, time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from log_security import redact_sensitive

LOGGER = logging.getLogger(__name__)

@dataclass(frozen=True)
class ProviderConfig:
    provider_id: str; model: str; api_key: str = field(default="", repr=False)
    base_url: str = ""; timeout: float = 60.0; enabled: bool = True

@dataclass(frozen=True)
class ProviderCapabilities:
    text_input: bool = True
    image_input: bool = False
    direct_file_input: bool = False
    responses_api: bool = False
    file_upload: bool = False
    file_reference: bool = False

    def supports(self, capability: str) -> bool:
        if not hasattr(self, capability): raise ValueError(f"未知 Provider capability：{capability}")
        return bool(getattr(self, capability))

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
    def __init__(self, message: str, *, provider: str = "", status_code: int | None = None,
                 error_type: str = "", error_code: str = "", endpoint: str = "", base_url_type: str = ""):
        super().__init__(message); self.provider=provider; self.status_code=status_code
        self.error_type=error_type;self.error_code=error_code;self.endpoint=endpoint;self.base_url_type=base_url_type
class ProviderConfigurationError(ProviderError): code="configuration"
class ProviderAuthenticationError(ProviderError): code="authentication"
class ProviderPermissionError(ProviderError): code="permission"
class ProviderRateLimitError(ProviderError): code="rate_limit"
class ProviderTimeoutError(ProviderError): code="timeout"
class ProviderNetworkError(ProviderError): code="network"
class ProviderServerError(ProviderError): code="server"
class ProviderModelError(ProviderError): code="model_not_found"
class ProviderRequestError(ProviderError): code="bad_request"
class ProviderEndpointError(ProviderError): code="endpoint_not_found"
class ProviderCapabilityError(ProviderError): code="unsupported_capability"

class LLMProvider(ABC):
    provider_id="unknown"
    def __init__(self, config: ProviderConfig):
        self.config=config
        if not config.api_key.strip(): raise ProviderConfigurationError("API Key 未配置。",provider=config.provider_id)
        if not config.model.strip(): raise ProviderConfigurationError("模型名称未配置。",provider=config.provider_id)
    @property
    def capabilities(self) -> ProviderCapabilities:return ProviderCapabilities()
    def supports(self, capability: str) -> bool:return self.capabilities.supports(capability)
    def require_capability(self, capability: str, message: str = "") -> None:
        if not self.supports(capability):
            raise ProviderCapabilityError(message or f"当前 Provider 不支持 {capability}。",provider=self.provider_id)
    def validate_request(self,request:ChatRequest)->None:
        for message in request.messages:
            content=message.get("content") if isinstance(message,dict) else None
            if not isinstance(content,list):continue
            for item in content:
                if not isinstance(item,dict):continue
                kind=item.get("type")
                if kind=="input_image":self.require_capability("image_input","当前 Provider 不支持图片输入。")
                elif kind=="input_file":self.require_capability("direct_file_input","当前兼容接口不支持直接 PDF/文件审查，请切换官方 OpenAI 或使用文本/图片审查模式。")
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

def _official_openai_base(base_url:str)->bool:
    value=(base_url or "").strip()
    if not value:return True
    try:return (urlparse(value).hostname or "").lower()=="api.openai.com"
    except ValueError:return False

def _known_compatible_base(provider_id:str,base_url:str)->bool:
    hosts={"deepseek":{"api.deepseek.com"},"qwen":{"dashscope.aliyuncs.com","maas.aliyuncs.com"},
           "zhipu":{"open.bigmodel.cn","bigmodel.cn"}}
    try:host=(urlparse((base_url or "").strip()).hostname or "").lower()
    except ValueError:return False
    return host in hosts.get(provider_id,set())

def _safe_error_text(value:Any)->str:
    text=redact_sensitive(value or "")
    text=re.sub(r"data:[^;\s]+;base64,[A-Za-z0-9+/=_-]+","[FILE_DATA_REDACTED]",text,flags=re.I)
    text=re.sub(r"[A-Za-z0-9+/=_-]{160,}","[LONG_DATA_REDACTED]",text)
    return text.replace("\r"," ").replace("\n"," ")[:600]

def _upstream_summary(exc:Exception,provider_id:str,endpoint:str,base_url_type:str)->dict[str,Any]:
    status=getattr(exc,"status_code",None);body=getattr(exc,"body",None);source=body
    if not isinstance(source,dict):
        response=getattr(exc,"response",None)
        try:source=response.json() if response is not None else None
        except Exception:source=None
    error=source.get("error",source) if isinstance(source,dict) else {}
    message=error.get("message") if isinstance(error,dict) else ""
    error_type=error.get("type") if isinstance(error,dict) else ""
    error_code=error.get("code") if isinstance(error,dict) else ""
    if not message:message=getattr(exc,"message",None) or str(exc)
    return {"provider":provider_id,"base_url_type":base_url_type,"endpoint":endpoint,"http_status":status,
            "error_type":_safe_error_text(error_type),"error_code":_safe_error_text(error_code),"message":_safe_error_text(message)}

def _raise_provider_error(exc:Exception,provider_id:str,*,endpoint:str="",base_url_type:str=""):
    summary=_upstream_summary(exc,provider_id,endpoint,base_url_type)
    LOGGER.error("Provider upstream error | %s",json.dumps(summary,ensure_ascii=False,sort_keys=True))
    status=summary["http_status"]; name=exc.__class__.__name__.lower(); detail=(summary["message"]+" "+summary["error_type"]+" "+summary["error_code"]).lower()
    kw={"provider":provider_id,"status_code":status,"error_type":summary["error_type"],"error_code":summary["error_code"],"endpoint":endpoint,"base_url_type":base_url_type}
    if status==401:raise ProviderAuthenticationError("API Key 无效或已过期。",**kw) from None
    if status==403:raise ProviderPermissionError("账号或 API Key 权限不足。",**kw) from None
    if status==429:raise ProviderRateLimitError("请求过于频繁或额度受限。",**kw) from None
    if status and status>=500:raise ProviderServerError("模型服务暂时不可用。",**kw) from None
    if "timeout" in name or "timed out" in detail:raise ProviderTimeoutError("连接模型服务超时。",**kw) from None
    if "model" in detail and any(x in detail for x in ("not found","does not exist","invalid")):raise ProviderModelError("模型不存在或当前账号不可用。",**kw) from None
    if status==404:raise ProviderEndpointError("接口端点不存在或与当前兼容服务不匹配。",**kw) from None
    if status==400:raise ProviderRequestError("请求参数或接口能力与上游服务不兼容。",**kw) from None
    if any(x in name+detail for x in ("connection","dns","proxy","network")):raise ProviderNetworkError("网络、DNS 或代理无法连接模型服务。",**kw) from None
    raise ProviderError("模型服务调用失败。",**kw) from None

class OpenAIResponsesProvider(LLMProvider):
    provider_id="openai"
    @property
    def capabilities(self)->ProviderCapabilities:
        official=_official_openai_base(self.config.base_url)
        return ProviderCapabilities(text_input=True,image_input=official,direct_file_input=official,responses_api=True,
                                    file_upload=official,file_reference=official)
    def chat(self,request:ChatRequest)->ChatResponse:
        started=time.perf_counter()
        try:
            self.validate_request(request)
            from openai import OpenAI
            client=OpenAI(api_key=self.config.api_key,base_url=self.config.base_url or None,timeout=request.timeout or self.config.timeout)
            kw={"model":request.model,"input":request.messages}
            if request.system_prompt:kw["instructions"]=request.system_prompt
            if request.temperature is not None:kw["temperature"]=request.temperature
            if request.max_output_tokens is not None:kw["max_output_tokens"]=request.max_output_tokens
            response=client.responses.create(**kw)
            return ChatResponse(response.output_text,self.provider_id,request.model,_usage_dict(getattr(response,"usage",None)),getattr(response,"status",None),time.perf_counter()-started,{"response_id":getattr(response,"id",None)})
        except ProviderError:raise
        except Exception as exc:_raise_provider_error(exc,self.provider_id,endpoint="/responses",base_url_type="official_openai" if _official_openai_base(self.config.base_url) else "custom_compatible")

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
    image_input=False
    @property
    def capabilities(self)->ProviderCapabilities:
        return ProviderCapabilities(text_input=True,image_input=self.image_input and _known_compatible_base(self.provider_id,self.config.base_url))
    def chat(self,request:ChatRequest)->ChatResponse:
        started=time.perf_counter()
        try:
            self.validate_request(request)
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
        except Exception as exc:_raise_provider_error(exc,self.provider_id,endpoint="/chat/completions",base_url_type="provider_compatible")

class DeepSeekProvider(OpenAICompatibleProvider):provider_id="deepseek"
class QwenProvider(OpenAICompatibleProvider):provider_id="qwen";image_input=True
class ZhipuProvider(OpenAICompatibleProvider):provider_id="zhipu";image_input=True

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
