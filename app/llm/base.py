"""Provider 协议：只依赖 chat 一个方法，换模型不改业务代码。

接入真实模型时实现 chat() 即可；mock 实现保证「没有 API key 也能完整跑通」，
这是 CI 与本地开发不依赖外部服务的前提。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass
class ChatResult:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    raw: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class LLMProvider(Protocol):
    name: str
    model: str

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> ChatResult: ...


def estimate_tokens(text: str) -> int:
    """粗略 token 估算（中文 ~1 字 1 token，英文 ~4 字符 1 token）。仅用于成本看板。"""
    if not text:
        return 0
    ascii_chars = sum(1 for ch in text if ord(ch) < 128)
    non_ascii = len(text) - ascii_chars
    return max(1, int(ascii_chars / 4 + non_ascii / 1.4))
