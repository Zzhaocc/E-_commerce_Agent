"""Provider 路由：按配置返回实现，未知 provider 直接报错而不是静默降级。"""

from __future__ import annotations

from app.config import get_settings
from app.llm.base import LLMProvider
from app.llm.openai_compat import OpenAICompatibleProvider


def get_provider(name: str | None = None) -> LLMProvider:
    s = get_settings()
    key = (name or s.llm_provider).lower()
    if key in {"mock", "offline", "replay"}:
        from app.llm.mock import MockProvider

        return MockProvider()
    if key in {"openai_compatible", "openai", "deepseek", "qwen", "vllm"}:
        if not s.llm_api_key:
            raise RuntimeError("LLM_PROVIDER 指向真实模型但未配置 LLM_API_KEY；离线运行请用 LLM_PROVIDER=mock")
        return OpenAICompatibleProvider()
    raise ValueError(f"未知的 LLM_PROVIDER: {key}")
