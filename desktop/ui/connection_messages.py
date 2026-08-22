from __future__ import annotations

from provider import (
    ProviderAuthenticationError,
    ProviderConfigurationError,
    ProviderError,
    ProviderModelError,
    ProviderNetworkError,
    ProviderPermissionError,
    ProviderRateLimitError,
    ProviderServerError,
    ProviderTimeoutError,
)


def connection_success_text(provider_name: str, model: str, response: object) -> str:
    """Build a useful success message even when the provider returns empty text."""
    actual_model = (getattr(response, "model", "") or model).strip()
    return f"连接成功\n\nProvider: {provider_name}\nModel: {actual_model}"


def connection_error_text(exc: ProviderError) -> str:
    """Map provider failures to safe UI text without exposing SDK details or secrets."""
    status = getattr(exc, "status_code", None)
    if isinstance(exc, ProviderAuthenticationError) or status == 401:
        return "API Key无效 / 401\n请检查密钥是否正确或已过期。"
    if isinstance(exc, ProviderPermissionError) or status == 403:
        return "权限不足 / 403\n当前账号或 API Key 无权访问该模型。"
    if isinstance(exc, ProviderModelError) or status == 404:
        return "模型不存在 / 404或模型错误\n请检查模型名称及账号权限。"
    if isinstance(exc, ProviderNetworkError):
        return "网络连接失败\n请检查网络、DNS 和代理设置。"
    if isinstance(exc, ProviderTimeoutError):
        return "请求超时\n请检查网络或适当增大 Timeout。"
    if isinstance(exc, ProviderRateLimitError) or status == 429:
        return "请求受限 / 429\n请稍后重试并检查账号额度。"
    if isinstance(exc, ProviderServerError) or (status is not None and status >= 500):
        return "模型服务暂时不可用\n上游服务返回错误，请稍后重试。"
    if isinstance(exc, ProviderConfigurationError):
        return "Provider 配置不完整\n请检查 Provider、Model、Base URL 和 API Key。"
    return "连接测试失败\n请检查 Provider 配置、网络和日志。"
