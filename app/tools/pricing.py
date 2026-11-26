"""02 产品策略：单位经济模型（净利率 / 盈亏价 / 券余量）。

口径统一（全部税前、单件、按币种绝对值）：
    net = P·(1 − commission − payment − ad_rate − coupon) − (cogs + freight)
    margin = net / P

由 net=0 反解 → 盈亏价；由 margin=target 反解 → 目标价。
这样定价不靠感觉，券也不是"老板拍脑袋"，而是有数学上限。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.tools.base import ToolError, tool


class UnitEconomicsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cogs: float = Field(gt=0, description="采购成本（单件）")
    freight: float = Field(default=0, ge=0, description="头程 + 尾程 + 退货摊销")
    commission_rate: float = Field(default=0.14, ge=0, le=0.5)
    payment_rate: float = Field(default=0.02, ge=0, le=0.1)
    ad_rate: float = Field(default=0.10, ge=0, le=0.5, description="广告费占售价比例")
    coupon_budget: float = Field(default=0.05, ge=0, le=0.3)
    target_margin: float = Field(default=0.15, ge=0, le=0.6)
    currency: str = "BRL"


def _net(price: float, cost: float, rate_sum: float, coupon: float) -> float:
    return price * (1 - rate_sum - coupon) - cost


def _price_position(net: float, price: float) -> str:
    """把价格阶梯上的每个档位标成经营语义，便于运营直接读。

    <0           亏损区（卖一件亏一件）
    <5%          薄利区（扛不住退货与广告波动）
    <25%         健康区
    ≥25%         高毛利区
    """
    margin = net / price if price else 0.0
    if net < 0:
        return "亏损区"
    if margin < 0.05:
        return "薄利区"
    if margin < 0.25:
        return "健康区"
    return "高毛利区"


@tool(
    "unit_economics",
    "计算单件净利率、盈亏平衡价、目标净利价、券/折余量，并给出价格阶梯。输入缺成本直接报错，不估算。",
    UnitEconomicsArgs,
    tags=("02",),
)
def unit_economics(a: UnitEconomicsArgs) -> dict[str, Any]:
    cost = a.cogs + a.freight
    rate_sum = a.commission_rate + a.payment_rate + a.ad_rate
    if rate_sum + a.coupon_budget + a.target_margin >= 1:
        raise ToolError(
            "unit_economics",
            f"费率合计过高（{rate_sum + a.coupon_budget + a.target_margin:.2f} ≥ 1），"
            "目标净利率在该平台上不可达，请下调目标或换成本结构",
        )

    breakeven = cost / (1 - rate_sum - a.coupon_budget)
    target_price = cost / (1 - rate_sum - a.coupon_budget - a.target_margin)
    net_at_target = _net(target_price, cost, rate_sum, a.coupon_budget)
    margin_at_target = net_at_target / target_price

    # 券余量：在目标价上，净利降到 0 之前还能让出多少钱 / 多少比例
    coupon_headroom = net_at_target
    allowed_discount_rate = coupon_headroom / target_price

    ladder = []
    for mult in (0.80, 0.90, 1.00, 1.10, 1.20, 1.35):
        price = round(target_price * mult, 2)
        net = _net(price, cost, rate_sum, a.coupon_budget)
        ladder.append(
            {
                "price": price,
                "net_profit": round(net, 2),
                "net_margin": round(net / price, 4),
                "position": _price_position(net, price),
            }
        )

    return {
        "currency": a.currency,
        "cost_base": round(cost, 2),
        "rate_sum": round(rate_sum, 4),
        "breakeven_price": round(breakeven, 2),
        "target_price": round(target_price, 2),
        "net_margin_at_target": round(margin_at_target, 4),
        "net_profit_at_target": round(net_at_target, 2),
        "coupon_headroom": round(coupon_headroom, 2),
        "allowed_discount_rate": round(allowed_discount_rate, 4),
        "coupon_budget": a.coupon_budget,
        "coupon_budget_is_safe": a.coupon_budget <= allowed_discount_rate,
        "price_ladder": ladder,
        "formula": {
            "net_profit": "price*(1-commission-payment-ad_rate-coupon) - (cogs+freight)",
            "breakeven_price": "cost/(1-rate_sum-coupon)",
            "target_price": "cost/(1-rate_sum-coupon-target_margin)",
            "version": "v2",
        },
    }


class SkuLadderArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_price: float = Field(gt=0)
    base_config: str = ""
    variants: list[str] = Field(default_factory=list)


@tool(
    "sku_ladder",
    "以目标价为锚生成四角色 SKU 价格阶梯（引流/主推/利润/形象），并检查价差是否足以区隔。",
    SkuLadderArgs,
    tags=("02",),
)
def sku_ladder(a: SkuLadderArgs) -> dict[str, Any]:
    anchors = [
        ("引流款", 0.82, "低于主推价，承担拉新与评价冷启动，净利可接近 0"),
        ("主推款", 1.00, "主力曝光位与广告承接，目标净利由定价模型保证"),
        ("利润款", 1.18, "加配置抬客单，广告依赖低，用于拉高整体毛利"),
        ("形象款", 1.45, "店内天花板，锚定主推款的性价比感知"),
    ]
    ladder = [
        {
            "role": role,
            "price": round(a.target_price * mult, 2),
            "price_index": mult,
            "rationale": why,
        }
        for role, mult, why in anchors
    ]
    gaps = [round(ladder[i + 1]["price"] / ladder[i]["price"] - 1, 3) for i in range(3)]
    warnings = []
    if any(g < 0.08 for g in gaps):
        warnings.append("相邻 SKU 价差 < 8%，用户感知不到配置差异，建议拉开配置或缩小价差档位")
    if a.target_price <= 0:
        warnings.append("目标价异常")
    return {
        "base_config": a.base_config,
        "variants": a.variants,
        "ladder": ladder,
        "adjacent_gaps": gaps,
        "warnings": warnings,
    }
