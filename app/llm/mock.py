"""Mock Provider：规则型 Agent，保证零依赖、零 API key 也能跑完整链路。

它不是"假数据生成器"：数值来自真实工具计算，文案来自商品知识库，
输出仍要过 Pydantic 契约与护栏，与真实模型走完全相同的运行时。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from app.llm.base import ChatResult, ToolCall, Usage, estimate_tokens
from app.llm.scenarios import BUILDERS

# 需要演示 function calling 的两个 Agent：先调工具，再出结构化稿
_PLANS: dict[str, dict[str, Callable[..., Any]]] = {
    "03_listing_creative": {
        "tool": "title_lint",
        "args": lambda draft, payload: {
            "text": draft["title"]["text"],
            "platform": payload["input"].get("platform", "shopee"),
            "keywords": (draft["keyword_map"]["primary"] or [])[:3],
        },
        "apply": lambda draft, result, payload: _apply_title_lint(draft, result),
    },
}


def _apply_title_lint(draft: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    title = draft["title"]
    text = title["text"]
    limit = int(result.get("limit") or title["limit"])
    if len(text) > limit:
        cut = text[:limit]
        if " " in cut:
            cut = cut[: cut.rfind(" ")].rstrip(" -|,")
        text = cut
    title.update(
        {
            "text": text,
            "chars": len(text),
            "limit": limit,
            "head30": text[:30],
            "checks": result.get("checks", []),
        }
    )
    hits = result.get("banned_hits", [])
    draft["compliance"] = {
        "passed": bool(result.get("passed", True)) and not hits,
        "blocked_terms": hits,
        "notes": [f"标题 {len(text)}/{limit} 字符"] + ([f"命中禁用词：{', '.join(hits)}"] if hits else []),
    }
    return draft


def _extract_payload(messages: list[dict[str, Any]]) -> dict[str, Any]:
    for msg in messages:
        if msg.get("role") == "user":
            content = msg.get("content") or ""
            try:
                return json.loads(content)
            except json.JSONDecodeError:
                continue
    raise ValueError("MockProvider 无法从消息中解析 payload")


class MockProvider:
    name = "mock"
    model = "mock-rule-agent-v1"

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> ChatResult:
        agent_id = (meta or {}).get("agent_id", "")
        if agent_id not in BUILDERS:
            raise ValueError(f"MockProvider 不支持的 agent: {agent_id}")

        payload = _extract_payload(messages)
        draft = BUILDERS[agent_id](payload)
        usage_in = sum(estimate_tokens(m.get("content") or "") for m in messages)

        plan = _PLANS.get(agent_id)
        tool_messages = [m for m in messages if m.get("role") == "tool"]

        if plan and not tool_messages:
            args = plan["args"](draft, payload)
            call = ToolCall(id=f"call_{agent_id[:6]}", name=plan["tool"], arguments=args)
            return ChatResult(
                content="",
                tool_calls=[call],
                usage=Usage(prompt_tokens=usage_in, completion_tokens=40),
                raw={"provider": "mock", "phase": "tool_call"},
            )

        if plan and tool_messages:
            raw = tool_messages[-1].get("content") or "{}"
            try:
                result = json.loads(raw)
            except json.JSONDecodeError:
                result = {}
            if result.get("ok") is not False:
                draft = plan["apply"](draft, result.get("result", result), payload)

        content = json.dumps(draft, ensure_ascii=False)
        return ChatResult(
            content=content,
            usage=Usage(prompt_tokens=usage_in, completion_tokens=estimate_tokens(content)),
            raw={"provider": "mock", "phase": "final"},
        )
