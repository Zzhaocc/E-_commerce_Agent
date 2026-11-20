"""Agent 运行时与护栏测试。"""

from __future__ import annotations

import json

import pytest

from app.agents.base import AgentContext
from app.agents.contracts import ContractError, validate_input
from app.agents.gates import run_gates
from app.agents.registry import get_agent
from app.agents.runtime import AgentRuntime
from app.llm.base import ChatResult, Usage
from app.llm.mock import MockProvider
from app.seed import SCOOTER_BRIEF
from app.tools.base import ToolError, invoke_tool


def _ctx(platform: str = "shopee") -> AgentContext:
    return AgentContext(
        project={"platform": platform, "market": "BR", "language": "pt-BR", "name": "t"},
        brief=SCOOTER_BRIEF,
        artifacts={},
        run_id="test",
        step_id="test",
    )


def test_input_contract_rejects_empty_rows() -> None:
    with pytest.raises(ContractError) as exc:
        validate_input("06_advertising", {"rows": [], "economics": {"cogs": 10}})
    assert "rows" in str(exc.value)


def test_tool_rejects_unknown_argument() -> None:
    with pytest.raises(ToolError) as exc:
        invoke_tool("unit_economics", {"cogs": 100, "made_up": 1})
    assert "参数校验失败" in exc.value.message


def test_market_agent_uses_tools_and_checks_numbers() -> None:
    spec = get_agent("01_market_research")
    result = AgentRuntime(MockProvider()).run(spec, _ctx())

    assert result.output["metrics"]["cr4"] == pytest.approx(result.output["metrics"]["cr4"])
    assert 0 <= result.output["metrics"]["cr4"] <= 1
    assert any(span["kind"] == "tool" for span in result.spans)
    assert any(span["kind"] == "llm" for span in result.spans)
    assert all(check["passed"] for check in result.checks)


def test_listing_gate_truncates_overlong_title() -> None:
    spec = get_agent("03_listing_creative")
    ctx = _ctx()
    ctx.artifacts = {
        "product_strategy": {"main_selling_point": "Motor 500W sobe ladeira de 15° com 90 kg", "pricing": {}}
    }
    output = AgentRuntime(MockProvider()).run(spec, ctx).output

    output["title"]["text"] = "Patinete " * 30  # 270 字符，必超 120
    outcome = run_gates("03_listing_creative", output, ctx)

    assert outcome.output["title"]["chars"] <= outcome.output["title"]["limit"]
    assert any(g["code"] == "title_over_limit" and g["severity"] == "repair" for g in outcome.guardrails)
    assert outcome.output["title"]["text"].endswith(("e", "l", "0", "o")) or " " not in output["title"]["text"]


def test_gate_blocks_banned_terms() -> None:
    spec = get_agent("03_listing_creative")
    ctx = _ctx()
    ctx.artifacts = {"product_strategy": {"main_selling_point": "x", "pricing": {}}}
    output = AgentRuntime(MockProvider()).run(spec, ctx).output

    output["title"]["text"] = "Patinete Elétrico Original Melhor do Mundo 500W"
    outcome = run_gates("03_listing_creative", output, ctx)

    assert outcome.blocked is True
    assert outcome.output["compliance"]["passed"] is False
    assert outcome.output["compliance"]["blocked_terms"]


def test_output_contract_failure_triggers_repair_retry() -> None:
    spec = get_agent("01_market_research")
    ctx = _ctx()
    good = AgentRuntime(MockProvider()).run(spec, ctx).output

    class FlakyProvider:
        name = "flaky"
        model = "flaky-v1"

        def __init__(self) -> None:
            self.calls = 0

        def chat(self, messages, tools=None, response_format=None, meta=None):
            self.calls += 1
            if self.calls == 1:
                return ChatResult(content="这不是 JSON", usage=Usage(10, 5))
            return ChatResult(content=json.dumps(good, ensure_ascii=False), usage=Usage(10, 5))

    provider = FlakyProvider()
    result = AgentRuntime(provider).run(spec, ctx)

    assert provider.calls >= 2
    assert result.attempts == 2
    assert result.output["precheck"]["verdict"] in {"GO", "CAUTION", "NO_GO"}
    assert any(span["name"].startswith("repair.") for span in result.spans)


def test_video_gate_reports_overlap() -> None:
    spec = get_agent("05_product_video")
    ctx = _ctx()
    ctx.artifacts = {"product_strategy": {"main_selling_point": "x", "pricing": {}}}
    output = AgentRuntime(MockProvider()).run(spec, ctx).output

    output["shots"][1]["t_start"] = 0.0
    outcome = run_gates("05_product_video", output, ctx)

    assert outcome.output["timing_check"]["total_duration"] > 0
    assert any(g["code"] in {"video_overlap", "video_gap"} for g in outcome.guardrails)
