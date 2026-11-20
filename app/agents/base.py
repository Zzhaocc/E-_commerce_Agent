"""Agent 规格与运行时数据结构。

AgentSpec 把「一个 Agent」拆成 5 个可替换部件：
    prompt      说什么
    build_input 从项目 brief 组装符合契约的输入
    evidence    先跑哪些确定性工具（数字交给脚本）
    tools       允许模型调用的工具（function calling）
    gates       事后硬校验（字符/像素/时序/口径）
这样换模型、换平台、加 Agent 都不用改运行时。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.llm.base import Usage


@dataclass
class AgentContext:
    """运行时上下文：项目事实 + 上游产物。"""

    project: dict[str, Any]
    brief: dict[str, Any]
    artifacts: dict[str, Any] = field(default_factory=dict)
    run_id: str = ""
    step_id: str = ""
    mode: str = "full_pipeline"


@dataclass
class AgentResult:
    output: dict[str, Any]
    checks: list[dict[str, Any]] = field(default_factory=list)
    guardrails: list[dict[str, Any]] = field(default_factory=list)
    spans: list[dict[str, Any]] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    latency_ms: int = 0
    attempts: int = 1
    tool_rounds: int = 0
    blocked: bool = False


@dataclass
class AgentSpec:
    id: str
    name: str
    cn_name: str
    artifact_kind: str
    role: str
    build_input: Callable[[AgentContext], dict[str, Any]]
    evidence: Callable[[AgentContext, dict[str, Any], Callable[..., dict]], dict[str, Any]]
    tools: list[str] = field(default_factory=list)
    gates: Callable[..., Any] | None = None
    max_steps: int = 3

    @property
    def title(self) -> str:
        return f"{self.id} {self.cn_name}"
