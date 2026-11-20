"""OpenAI 兼容协议实现（DeepSeek / Qwen / vLLM / Ollama / 任何中转网关通用）。

这是**真实接入示例**：只调 /chat/completions，支持 function calling 与 JSON 输出。
生产上还应加：重试与指数退避、超时熔断、并发限流、流式输出、多 key 轮询。
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

from app.config import get_settings
from app.llm.base import ChatResult, ToolCall, Usage, estimate_tokens


class OpenAICompatibleProvider:
    name = "openai_compatible"

    def __init__(self, base_url: str | None = None, api_key: str | None = None, model: str | None = None):
        s = get_settings()
        self.base_url = (base_url or s.llm_base_url).rstrip("/")
        self.api_key = api_key or s.llm_api_key
        self.model = model or s.llm_model
        self.temperature = s.llm_temperature
        self.timeout = s.llm_timeout

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> ChatResult:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if response_format:
            payload["response_format"] = response_format

        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        started = time.perf_counter()
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(f"{self.base_url}/chat/completions", json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        latency_ms = int((time.perf_counter() - started) * 1000)

        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        calls: list[ToolCall] = []
        for idx, call in enumerate(message.get("tool_calls") or []):
            fn = call.get("function") or {}
            raw_args = fn.get("arguments") or "{}"
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
            except json.JSONDecodeError:
                args = {"__raw__": raw_args}
            calls.append(ToolCall(id=call.get("id") or f"call_{idx}", name=fn.get("name", ""), arguments=args))

        usage_raw = data.get("usage") or {}
        usage = Usage(
            prompt_tokens=usage_raw.get("prompt_tokens") or estimate_tokens(json.dumps(messages, ensure_ascii=False)),
            completion_tokens=usage_raw.get("completion_tokens") or estimate_tokens(message.get("content") or ""),
        )
        return ChatResult(
            content=message.get("content") or "",
            tool_calls=calls,
            usage=usage,
            raw={"latency_ms": latency_ms, "model": self.model},
        )
