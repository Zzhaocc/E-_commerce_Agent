"""Agent 运行时。

一次 run 的完整链路：
    输入契约校验 → 证据工具预计算 → LLM/工具循环 → 输出契约校验
    → 事后护栏（可自动修复/阻塞）→ 用量与 trace 汇总

关键取舍：
- 证据先算：数值类结果不让模型产生，模型只做解读与文案；
- 结构化输出 + 校验失败自动重试：把"格式错误"变成可恢复错误；
- 工具调用有轮次上限，防止模型死循环烧钱。
"""

from __future__ import annotations

import json
import time
from typing import Any

from app.agents.base import AgentContext, AgentResult, AgentSpec
from app.agents.contracts import ContractError, validate_input, validate_output
from app.agents.gates import run_gates
from app.agents.prompts import system_prompt
from app.config import get_settings
from app.llm.base import ChatResult, LLMProvider, Usage, estimate_tokens
from app.llm.router import get_provider
from app.tools.base import ToolError, invoke_tool, tool_schemas


class AgentRunError(RuntimeError):
    """Agent 执行失败，带阶段信息便于定位。"""

    def __init__(self, agent_id: str, stage: str, message: str):
        self.agent_id = agent_id
        self.stage = stage
        super().__init__(f"[{agent_id}/{stage}] {message}")


def _parse_json_object(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start == -1 or end <= start:
            raise AgentRunError("runtime", "parse", f"模型输出不是 JSON：{raw[:160]}")
        data = json.loads(raw[start : end + 1])
    if not isinstance(data, dict):
        raise AgentRunError("runtime", "parse", "模型输出的顶层必须是 JSON 对象")
    return data


class AgentRuntime:
    def __init__(self, provider: LLMProvider | None = None):
        self.settings = get_settings()
        self.provider = provider or get_provider()

    # ------------------------------------------------------------------ #
    def run(self, spec: AgentSpec, ctx: AgentContext) -> AgentResult:
        started = time.perf_counter()
        spans: list[dict[str, Any]] = []
        total_usage = Usage()

        # 1) 输入契约
        raw_input = spec.build_input(ctx)
        try:
            validated = validate_input(spec.id, raw_input)
        except ContractError as exc:
            raise AgentRunError(spec.id, "input_contract", str(exc)) from exc
        agent_input = validated.model_dump()

        def call(name: str, args: dict[str, Any]) -> dict[str, Any]:
            for attempt in (1, 2):
                t0 = time.perf_counter()
                try:
                    result, attrs = invoke_tool(name, args)
                    spans.append(
                        {
                            "name": f"tool.{name}",
                            "kind": "tool",
                            "status": "ok",
                            "latency_ms": int((time.perf_counter() - t0) * 1000),
                            "tokens": 0,
                            "cost_usd": 0.0,
                            "attrs": {**attrs, "attempt": attempt},
                        }
                    )
                    return result
                except ToolError as exc:
                    spans.append(
                        {
                            "name": f"tool.{name}",
                            "kind": "tool",
                            "status": "error",
                            "latency_ms": int((time.perf_counter() - t0) * 1000),
                            "tokens": 0,
                            "cost_usd": 0.0,
                            "attrs": {"error": exc.message, "attempt": attempt},
                        }
                    )
                    if attempt == 2:
                        raise AgentRunError(spec.id, "tool", exc.message) from exc
            raise AgentRunError(spec.id, "tool", f"{name} 不可达")

        # 2) 证据工具
        t0 = time.perf_counter()
        try:
            evidence = spec.evidence(ctx, agent_input, call)
        except ToolError as exc:
            raise AgentRunError(spec.id, "evidence", exc.message) from exc

        # 3) LLM / 工具循环
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt(spec.id)},
            {"role": "user", "content": json.dumps({"input": agent_input, "evidence": evidence}, ensure_ascii=False)},
        ]
        schemas = tool_schemas([t for t in spec.tools if t]) or None
        attempts = 0
        tool_rounds = 0
        content = ""
        repair_note: str | None = None

        while attempts < self.settings.max_attempts:
            attempts += 1
            conversation = list(messages)
            if repair_note:
                conversation.append({"role": "user", "content": repair_note})

            for round_no in range(spec.max_steps + 1):
                t0 = time.perf_counter()
                result: ChatResult = self.provider.chat(
                    conversation,
                    tools=schemas,
                    response_format={"type": "json_object"},
                    meta={"agent_id": spec.id, "round": round_no, "run_id": ctx.run_id},
                )
                latency = int((time.perf_counter() - t0) * 1000)
                cost = (
                    result.usage.prompt_tokens / 1000 * self.settings.cost_per_1k_in
                    + result.usage.completion_tokens / 1000 * self.settings.cost_per_1k_out
                )
                total_usage.prompt_tokens += result.usage.prompt_tokens
                total_usage.completion_tokens += result.usage.completion_tokens
                spans.append(
                    {
                        "name": f"llm.{spec.id}",
                        "kind": "llm",
                        "status": "ok",
                        "latency_ms": latency,
                        "tokens": result.usage.total,
                        "cost_usd": round(cost, 6),
                        "attrs": {
                            "provider": self.provider.name,
                            "model": self.provider.model,
                            "round": round_no,
                            "tool_calls": len(result.tool_calls),
                        },
                    }
                )

                if result.tool_calls:
                    if tool_rounds >= spec.max_steps:
                        raise AgentRunError(spec.id, "tool_loop", f"工具调用超过 {spec.max_steps} 轮上限")
                    tool_rounds += 1
                    conversation.append(
                        {
                            "role": "assistant",
                            "content": result.content or "",
                            "tool_calls": [
                                {
                                    "id": c.id,
                                    "type": "function",
                                    "function": {"name": c.name, "arguments": json.dumps(c.arguments, ensure_ascii=False)},
                                }
                                for c in result.tool_calls
                            ],
                        }
                    )
                    for c in result.tool_calls:
                        try:
                            tool_result = call(c.name, c.arguments)
                            payload = {"ok": True, "result": tool_result}
                        except AgentRunError as exc:
                            payload = {"ok": False, "error": str(exc)}
                        conversation.append(
                            {
                                "role": "tool",
                                "tool_call_id": c.id,
                                "name": c.name,
                                "content": json.dumps(payload, ensure_ascii=False),
                            }
                        )
                    continue

                content = result.content
                break

            # 4) 输出契约
            try:
                data = _parse_json_object(content)
                validated_out = validate_output(spec.id, data)
                output = validated_out.model_dump()
                break
            except (ContractError, AgentRunError, json.JSONDecodeError) as exc:
                if attempts >= self.settings.max_attempts:
                    raise AgentRunError(spec.id, "output_contract", str(exc)) from exc
                repair_note = (
                    "上一次输出未通过 JSON Schema 校验，错误如下：\n"
                    f"{exc}\n请严格按 schema 重新输出，只输出 JSON 对象，不要解释。"
                )
                spans.append(
                    {
                        "name": f"repair.{spec.id}",
                        "kind": "guardrail",
                        "status": "retry",
                        "latency_ms": 0,
                        "tokens": 0,
                        "cost_usd": 0.0,
                        "attrs": {"attempt": attempts, "error": str(exc)[:300]},
                    }
                )

        # 5) 事后护栏
        gate = run_gates(spec.id, output, ctx)
        spans.append(
            {
                "name": f"gate.{spec.id}",
                "kind": "guardrail",
                "status": "blocked" if gate.blocked else "ok",
                "latency_ms": 0,
                "tokens": 0,
                "cost_usd": 0.0,
                "attrs": {
                    "checks": len(gate.checks),
                    "failed": sum(1 for c in gate.checks if not c["passed"]),
                    "guardrails": len(gate.guardrails),
                },
            }
        )
        try:
            output = validate_output(spec.id, gate.output).model_dump()
        except ContractError as exc:
            raise AgentRunError(spec.id, "post_validate", str(exc)) from exc

        return AgentResult(
            output=output,
            checks=gate.checks,
            guardrails=gate.guardrails,
            spans=spans,
            usage=total_usage,
            latency_ms=int((time.perf_counter() - started) * 1000),
            attempts=attempts,
            tool_rounds=tool_rounds,
            blocked=gate.blocked,
        )

    # ------------------------------------------------------------------ #
    def estimate_tokens(self, text: str) -> int:
        return estimate_tokens(text)
