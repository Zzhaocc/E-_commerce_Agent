"""工具注册中心。

设计原则（对应仓库「能算的别让模型猜」）：
- 所有数值计算、字符校验、像素校验、归因计数都由 Python 完成；
- 工具参数用 Pydantic 校验，extra="forbid"，模型传错参数直接拒绝执行；
- 每次调用都是纯函数 + 确定性输出，可单测、可复现。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError


class ToolError(RuntimeError):
    """工具执行失败（参数非法 / 数据不足）。"""

    def __init__(self, name: str, message: str):
        self.tool = name
        self.message = message
        super().__init__(f"[{name}] {message}")


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    args_model: type[BaseModel]
    func: Callable[[BaseModel], dict[str, Any]]
    tags: tuple[str, ...] = field(default_factory=tuple)

    def schema(self) -> dict[str, Any]:
        """导出 OpenAI function-calling 兼容 schema。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.args_model.model_json_schema(),
            },
        }

    def invoke(self, raw_args: dict[str, Any]) -> dict[str, Any]:
        try:
            args = self.args_model.model_validate(raw_args or {})
        except ValidationError as exc:
            detail = "; ".join(f"{'.'.join(str(x) for x in e['loc']) or '<args>'}: {e['msg']}" for e in exc.errors())
            raise ToolError(self.name, f"参数校验失败: {detail}") from exc
        try:
            return self.func(args)
        except ToolError:
            raise
        except Exception as exc:  # noqa: BLE001 —— 工具内部异常统一转成可观测错误
            raise ToolError(self.name, f"执行异常: {exc}") from exc


_REGISTRY: dict[str, ToolSpec] = {}


def tool(name: str, description: str, args: type[BaseModel], tags: tuple[str, ...] = ()):
    """注册工具。用法：

    @tool("market_metrics", "计算 CR4/HHI/价格带", MarketMetricsArgs, tags=("01",))
    def market_metrics(a: MarketMetricsArgs) -> dict: ...
    """

    def deco(fn: Callable[[Any], dict[str, Any]]) -> Callable[[Any], dict[str, Any]]:
        if name in _REGISTRY:
            raise ValueError(f"工具重名: {name}")
        _REGISTRY[name] = ToolSpec(name=name, description=description, args_model=args, func=fn, tags=tags)
        return fn

    return deco


def get_tool(name: str) -> ToolSpec:
    spec = _REGISTRY.get(name)
    if spec is None:
        # 懒加载：脚本 / 测试 / 单 Agent 预览不必显式调用 load_all_tools()
        load_all_tools()
        spec = _REGISTRY.get(name)
    if spec is None:
        raise ToolError(name, "工具未注册")
    return spec


def list_tools(tag: str | None = None) -> list[ToolSpec]:
    specs = list(_REGISTRY.values())
    if tag:
        specs = [s for s in specs if tag in s.tags]
    return sorted(specs, key=lambda s: s.name)


def tool_schemas(names: list[str]) -> list[dict[str, Any]]:
    return [get_tool(n).schema() for n in names]


def invoke_tool(name: str, args: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """执行工具，返回 (结果, span 属性)。"""
    spec = get_tool(name)
    started = time.perf_counter()
    result = spec.invoke(args)
    latency = int((time.perf_counter() - started) * 1000)
    attrs = {"tool": name, "args_keys": sorted((args or {}).keys()), "latency_ms": latency}
    return result, attrs


# 触发各工具模块的注册（模块导入即注册）。幂等，可重复调用。
_LOADED = False


def load_all_tools() -> None:
    global _LOADED
    if _LOADED:
        return
    _LOADED = True
    from app.tools import ads, image, listing, market, pricing, review, video  # noqa: F401
