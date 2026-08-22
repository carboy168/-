from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


class ProviderError(RuntimeError):
    """Provider-neutral error exposed to the application."""


class AIProvider(ABC):
    """Minimal interface used by RAG, review and desktop UI."""

    name = "unknown"

    @abstractmethod
    def generate(self, *, model: str, input: Any, instructions: str | None = None) -> str:
        raise NotImplementedError

    def test_connection(self, *, model: str) -> str:
        return self.generate(model=model, input="只回复：连接正常")


@dataclass
class OpenAIProvider(AIProvider):
    api_key: str
    name = "openai"

    def __post_init__(self):
        if not self.api_key.strip():
            raise ProviderError("未配置 OpenAI API Key。")

    def generate(self, *, model: str, input: Any, instructions: str | None = None) -> str:
        if not model.strip():
            raise ProviderError("未配置模型名称。")
        try:
            from openai import OpenAI

            kwargs = {"model": model, "input": input}
            if instructions is not None:
                kwargs["instructions"] = instructions
            response = OpenAI(api_key=self.api_key).responses.create(**kwargs)
            return response.output_text
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(f"OpenAI 调用失败：{exc}") from exc


def get_provider(name: str | None = None, *, api_key: str | None = None) -> AIProvider:
    """Create the configured provider without leaking SDK types to callers."""
    provider_name = (name or os.getenv("AI_PROVIDER", "openai")).strip().lower()
    if provider_name != "openai":
        raise ProviderError(f"当前版本仅支持 OpenAI Provider：{provider_name}")
    return OpenAIProvider(api_key=(api_key if api_key is not None else os.getenv("OPENAI_API_KEY", "")))
