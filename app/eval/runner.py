"""回归评测：把「Agent 该做什么、不该做什么」固化成可执行断言。

包含两类用例：
- 正向：输出必须满足业务硬指标（标题长度、四角色价格、P0 动作…）；
- 负向：数据不足时必须失败（缺竞品、费率不可达），不允许模型编造。

每次改 Prompt / 换模型，先跑这套评测再上线——这是"能改"的前提。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from app.agents.base import AgentContext
from app.agents.registry import get_agent
from app.agents.runtime import AgentRuntime
from app.db import session_scope
from app.llm.router import get_provider
from app.models import EvalRun, new_id
from app.seed import PET_BRIEF, SCOOTER_BRIEF


class Case:
    def __init__(
        self,
        case_id: str,
        agent_id: str,
        brief: dict[str, Any],
        assertions: list[tuple[str, Callable[[dict[str, Any]], bool]]],
        platform: str = "shopee",
        artifacts: dict[str, Any] | None = None,
        expect_error: str | None = None,
        note: str = "",
    ):
        self.id = case_id
        self.agent_id = agent_id
        self.brief = brief
        self.assertions = assertions
        self.platform = platform
        self.artifacts = artifacts or {}
        self.expect_error = expect_error
        self.note = note


def _strategy_artifact() -> dict[str, Any]:
    """给 03/04/05 用例准备上游产物，避免"没策略就写标题"。"""
    return {
        "product_strategy": {
            "main_selling_point": "Motor 500W sobe ladeira de 15° com adulto de 90 kg sem perder velocidade",
            "positioning": "Patinete de entrada com motor forte",
            "pricing": {"target_price": 2634.62, "breakeven_price": 2044.78, "net_margin_at_target": 0.15},
        }
    }


def _market_case() -> Case:
    return Case(
        "market_scooter_ok",
        "01_market_research",
        SCOOTER_BRIEF,
        [
            ("CR4 在 0-1", lambda o: 0 <= o["metrics"]["cr4"] <= 1),
            ("HHI 在 0-10000", lambda o: 0 <= o["metrics"]["hhi"] <= 10000),
            ("价格带次序正确", lambda o: o["price_band"]["valley_low"] < o["price_band"]["valley_high"]),
            ("机会点 ≥3", lambda o: len(o["opportunity_points"]) >= 3),
            ("结论是明确档位", lambda o: o["precheck"]["verdict"] in {"GO", "CAUTION", "NO_GO"}),
        ],
        note="正常类目导出数据",
    )


def _cases() -> list[Case]:
    no_data = {**SCOOTER_BRIEF, "competitor_rows": []}
    bad_econ = {
        **SCOOTER_BRIEF,
        "economics": {"cogs": 500.0, "freight": 50.0, "ad_rate": 0.30, "coupon_budget": 0.20, "target_margin": 0.50},
    }
    pet = {**PET_BRIEF}

    return [
        _market_case(),
        Case(
            "market_missing_data_must_fail",
            "01_market_research",
            no_data,
            [],
            expect_error="competitor_rows",
            note="负向：没有竞品数据时必须报错，不许编造市场结论",
        ),
        Case(
            "strategy_scooter_ok",
            "02_product_strategy",
            {**SCOOTER_BRIEF, "market_analysis": {"price_band": {"median": 2699}}},
            [
                (
                    "四角色齐全",
                    lambda o: {r["role"] for r in o["sku_roles"]} == {"引流款", "主推款", "利润款", "形象款"},
                ),
                ("目标价 ≥ 盈亏价", lambda o: o["pricing"]["target_price"] >= o["pricing"]["breakeven_price"]),
                ("净利率达标", lambda o: abs(o["pricing"]["net_margin_at_target"] - 0.15) < 0.01),
                ("唯一主卖点", lambda o: 0 < len(o["main_selling_point"]) <= 80),
            ],
        ),
        Case(
            "strategy_unreachable_margin_must_fail",
            "02_product_strategy",
            bad_econ,
            [],
            expect_error="费率",
            note="负向：费率+目标净利率 >100% 时必须失败",
        ),
        Case(
            "listing_scooter_ok",
            "03_listing_creative",
            SCOOTER_BRIEF,
            [
                ("标题 ≤120 字符", lambda o: o["title"]["chars"] <= 120),
                ("标题 ≥40 字符", lambda o: o["title"]["chars"] >= 40),
                ("五点卖点", lambda o: len(o["bullets"]) == 5),
                ("无禁用词", lambda o: not o["compliance"]["blocked_terms"]),
                ("关键词分层完整", lambda o: bool(o["keyword_map"]["primary"] and o["keyword_map"]["negative"])),
                ("参数可溯源", lambda o: all(s["source"] in {"fact", "derived"} for s in o["specs"])),
            ],
            artifacts=_strategy_artifact(),
        ),
        Case(
            "listing_pet_mercadolivre_title_limit",
            "03_listing_creative",
            pet,
            [
                ("标题 ≤60 字符（ML 上限）", lambda o: o["title"]["chars"] <= 60),
                ("标题未被截成残词", lambda o: not o["title"]["text"].endswith(("-", "|", " "))),
                ("合规通过", lambda o: o["compliance"]["passed"] is True),
            ],
            platform="mercadolivre",
            artifacts={
                "product_strategy": {
                    "main_selling_point": "Aprovada para cabine: 40x30x20 cm e suporta pets de até 8 kg",
                    "pricing": {"target_price": 130.0, "breakeven_price": 95.0, "net_margin_at_target": 0.18},
                }
            },
        ),
        Case(
            "image_grid_ok",
            "04_product_image",
            SCOOTER_BRIEF,
            [
                ("9 图完整", lambda o: len(o["nine_grid"]) == 9),
                ("slot 连续 1-9", lambda o: [g["slot"] for g in o["nine_grid"]] == list(range(1, 10))),
                ("主图无文字", lambda o: o["nine_grid"][0]["overlay_text"] == ""),
                ("叠加文字 ≤16", lambda o: all(len(g["overlay_text"]) <= 16 for g in o["nine_grid"])),
                ("每图有英文 Prompt", lambda o: all(len(g["prompt"]) > 30 for g in o["nine_grid"])),
            ],
            artifacts=_strategy_artifact(),
        ),
        Case(
            "video_timing_ok",
            "05_product_video",
            SCOOTER_BRIEF,
            [
                ("时长在目标 ±15%", lambda o: o["timing_check"]["within_target"] is True),
                ("钩子存在", lambda o: o["timing_check"]["hook_ok"] is True),
                ("分镜 ≥3", lambda o: len(o["shots"]) >= 3),
                ("字幕不超 28 字符", lambda o: all(len(c) <= 28 for c in o["captions"])),
            ],
            artifacts=_strategy_artifact(),
        ),
        Case(
            "ads_losing_must_give_p0",
            "06_advertising",
            SCOOTER_BRIEF,
            [
                ("识别出 ROAS 低于盈亏线", lambda o: o["funnel"]["roas"] < o["breakeven"]["roas_min"]),
                ("给出 P0 动作", lambda o: any(a["priority"] == "P0" for a in o["actions"])),
                ("P0 有 SLA", lambda o: all(a["sla_hours"] <= 24 for a in o["actions"] if a["priority"] == "P0")),
                (
                    "诊断带数字证据",
                    lambda o: any(any(ch.isdigit() for ch in e) for d in o["diagnosis"] for e in d["evidence"]),
                ),
            ],
            note="亏损状态必须给 P0，且 24 小时内可执行",
        ),
        Case(
            "review_attribution_ok",
            "07_review",
            SCOOTER_BRIEF,
            [
                ("样本数与输入一致", lambda o: o["attribution"]["total"] == len(SCOOTER_BRIEF["reviews"])),
                ("归因维度 ≥8", lambda o: len(o["attribution"]["counts"]) >= 8),
                ("可修复率 >0", lambda o: o["attribution"]["fixable_rate"] > 0),
                ("给出详情页修补", lambda o: len(o["listing_patches"]) >= 1),
            ],
        ),
    ]


def run_suite(suite: str = "golden") -> dict[str, Any]:
    cases = _cases()
    runtime = AgentRuntime(get_provider())
    started = time.perf_counter()
    results: list[dict[str, Any]] = []
    passed = 0

    for case in cases:
        ctx = AgentContext(
            project={"platform": case.platform, "market": "BR", "language": "pt-BR", "name": case.id},
            brief=case.brief,
            artifacts=case.artifacts,
            run_id="eval",
            step_id="eval",
        )
        spec = get_agent(case.agent_id)
        item: dict[str, Any] = {"case": case.id, "agent": case.agent_id, "note": case.note}
        try:
            result = runtime.run(spec, ctx)
            if case.expect_error:
                item.update({"passed": False, "detail": f"预期失败但成功了：{case.expect_error}"})
            else:
                failures = [name for name, fn in case.assertions if not fn(result.output)]
                item.update(
                    {
                        "passed": not failures,
                        "detail": "全部通过" if not failures else f"未通过：{', '.join(failures)}",
                        "assertions": len(case.assertions),
                    }
                )
        except Exception as exc:  # noqa: BLE001 —— 评测要记录任何失败，而不是中断整轮
            message = str(exc)
            if case.expect_error:
                ok = case.expect_error in message or case.expect_error in type(exc).__name__
                item.update({"passed": ok, "detail": f"按预期失败：{message[:160]}"})
            else:
                item.update({"passed": False, "detail": f"异常：{message[:160]}"})

        passed += 1 if item["passed"] else 0
        results.append(item)

    total = len(cases)
    score = round(passed / total, 3) if total else 0.0
    payload = {
        "id": new_id("evl"),
        "suite": suite,
        "total": total,
        "passed": passed,
        "score": score,
        "duration_ms": int((time.perf_counter() - started) * 1000),
        "results": results,
    }
    with session_scope() as db:
        db.add(
            EvalRun(
                id=payload["id"],
                suite=suite,
                total=total,
                passed=passed,
                score=score,
                results=results,
            )
        )
    return payload


if __name__ == "__main__":
    import json

    from app.db import init_db

    init_db()
    print(json.dumps(run_suite(), ensure_ascii=False, indent=2))
