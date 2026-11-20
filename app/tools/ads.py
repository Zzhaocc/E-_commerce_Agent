"""06 广告优化：确定性漏斗、盈亏线与预算再分配。

口径（全部来自分 campaign 投放明细，不做任何估算）：
    CTR  = clicks / impressions
    CVR  = orders / clicks
    CPC  = spend / clicks
    CPA  = spend / orders
    ROAS = revenue / spend
    AOV  = revenue / orders

盈亏线由单位经济反解（与 pricing 模块同一套口径）：
    contribution = AOV·(1 − commission − payment − coupon) − cogs − freight
    margin_rate  = contribution / AOV
    CPA_max      = contribution
    CPC_max      = CPA_max · CVR
    ROAS_min     = 1 / margin_rate

贡献毛利 ≤ 0 时直接报错——这不是"建议优化"，而是该成本结构在该售价下不可能赚钱。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.tools.base import ToolError, tool

SignalLayer = Literal["曝光", "点击", "转化", "客单", "履约"]
SignalSeverity = Literal["P0", "P1", "P2"]


class AdRow(BaseModel):
    """单条 campaign 投放数据（字段约束对齐 contracts.AdRow，但不依赖 contracts）。"""

    model_config = ConfigDict(extra="forbid")
    campaign: str = ""
    impressions: int = Field(ge=0, description="曝光量")
    clicks: int = Field(ge=0, description="点击量")
    orders: int = Field(ge=0, description="成交订单数")
    spend: float = Field(ge=0, description="广告花费")
    revenue: float = Field(ge=0, description="广告带来的成交额")


class AdEconomics(BaseModel):
    """广告场景下的单位经济模型（字段约束对齐 contracts.Economics）。"""

    model_config = ConfigDict(extra="forbid")
    cogs: float = Field(gt=0, description="采购成本（单件）")
    freight: float = Field(default=0, ge=0, description="头程 + 尾程 + 退货摊销")
    commission_rate: float = Field(default=0.14, ge=0, le=0.5, description="平台佣金率")
    payment_rate: float = Field(default=0.02, ge=0, le=0.1, description="支付通道费率")
    coupon_budget: float = Field(default=0.05, ge=0, le=0.3, description="券 / 活动折算比例")


class AdMetricsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rows: list[AdRow] = Field(min_length=1, description="分 campaign 投放数据，至少 1 条")
    economics: AdEconomics
    window_days: int = Field(default=7, ge=1, le=90, description="统计窗口天数")


class BudgetReallocArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rows: list[AdRow] = Field(min_length=1, description="分 campaign 投放数据（每行一个 campaign）")
    economics: AdEconomics
    shift_ratio: float = Field(default=0.3, ge=0, le=0.6, description="从最差 campaign 挪出的花费比例")


def _row_roas(row: AdRow) -> float:
    """单 campaign ROAS；无花费时按 0 处理，避免把"没投"误判成高 ROAS。"""
    return row.revenue / row.spend if row.spend > 0 else 0.0


def _campaign_name(row: AdRow, index: int) -> str:
    """campaign 名为空时给稳定占位名，保证输出可复现。"""
    name = row.campaign.strip()
    return name or f"广告组{index + 1}"


def _signal(
    layer: SignalLayer,
    code: str,
    severity: SignalSeverity,
    evidence: list[str],
) -> dict[str, Any]:
    """构造一条确定性诊断信号。"""
    return {"layer": layer, "code": code, "severity": severity, "evidence": evidence}


def _breakeven(aov: float, econ: AdEconomics, tool_name: str) -> tuple[float, float, float]:
    """由客单价反解盈亏线，返回 (单均贡献毛利, 净利率, ROAS 下限)。

    贡献毛利 ≤ 0 说明"卖一单亏一单"，直接抛 ToolError，不给出任何乐观建议。
    """
    if aov <= 0:
        raise ToolError(tool_name, "客单价为 0（有订单无营收），无法计算贡献毛利")
    rate_sum = econ.commission_rate + econ.payment_rate + econ.coupon_budget
    contribution = aov * (1 - rate_sum) - econ.cogs - econ.freight
    margin_rate = contribution / aov
    if margin_rate <= 0:
        raise ToolError(
            tool_name,
            f"成本结构在该售价下不可能盈利：单均贡献毛利 {contribution:.2f}，"
            f"净利率 {margin_rate:.4f} ≤ 0（客单价 {aov:.2f}）",
        )
    return contribution, margin_rate, 1 / margin_rate


@tool(
    "ad_metrics",
    "计算广告漏斗(CTR/CVR/CPC/CPA/ROAS/AOV)、由单位经济反解的盈亏线、分 campaign 诊断表与确定性信号，"
    "并给出有花费零成交的浪费占比。",
    AdMetricsArgs,
    tags=("06",),
)
def ad_metrics(a: AdMetricsArgs) -> dict[str, Any]:
    """汇总漏斗 + 盈亏线 + campaign 诊断 + 信号，全部数值可复现。"""
    rows = a.rows
    impressions = sum(r.impressions for r in rows)
    clicks = sum(r.clicks for r in rows)
    orders = sum(r.orders for r in rows)
    spend_raw = sum(r.spend for r in rows)
    revenue_raw = sum(r.revenue for r in rows)
    spend = round(spend_raw, 2)
    revenue = round(revenue_raw, 2)

    # 没有订单就没有客单价，也就没有盈亏线；宁可报错，也不让模型去猜。
    if orders <= 0:
        raise ToolError("ad_metrics", "窗口内没有成交订单，无法计算客单价与盈亏线；请先修复转化再谈放量")
    if revenue_raw <= 0:
        raise ToolError("ad_metrics", "窗口内成交额为 0，无法计算客单价与盈亏线")

    aov = revenue_raw / orders
    contribution, margin_rate, roas_min = _breakeven(aov, a.economics, "ad_metrics")
    cpa_max = contribution

    # 分母为 0 的比率统一置 0.0，保证下游契约不会出现 null。
    ctr_raw = clicks / impressions if impressions else 0.0
    cvr_raw = orders / clicks if clicks else 0.0
    cpc_raw = spend_raw / clicks if clicks else 0.0
    cpa_raw = spend_raw / orders
    roas_raw = revenue_raw / spend_raw if spend_raw else 0.0

    cpc_max = cpa_max * cvr_raw
    current_roas_vs_target = roas_raw / roas_min

    funnel = {
        "impressions": impressions,
        "clicks": clicks,
        "orders": orders,
        "spend": spend,
        "revenue": revenue,
        "ctr": round(ctr_raw, 4),
        "cvr": round(cvr_raw, 4),
        "cpc": round(cpc_raw, 2),
        "cpa": round(cpa_raw, 2),
        "roas": round(roas_raw, 4),
        "aov": round(aov, 2),
    }
    # contribution_margin 为"单均贡献毛利（金额）"，与 contracts.BreakevenOut 对齐。
    breakeven = {
        "aov": round(aov, 2),
        "contribution_margin": round(contribution, 2),
        "cpa_max": round(cpa_max, 2),
        "cpc_max": round(cpc_max, 2),
        "roas_min": round(roas_min, 4),
        "current_roas_vs_target": round(current_roas_vs_target, 4),
    }

    # ---- 分 campaign 诊断表：ROAS 相对盈亏线决定放量 / 优化 / 关停 ----
    campaign_table: list[dict[str, Any]] = []
    zero_order_spend = 0.0
    zero_order_names: list[str] = []
    for index, row in enumerate(rows):
        name = _campaign_name(row, index)
        row_roas = _row_roas(row)
        if row_roas >= roas_min * 1.3:
            signal = "scale"
        elif row_roas < roas_min * 0.8:
            signal = "cut"
        else:
            signal = "optimize"
        campaign_table.append(
            {
                "campaign": name,
                "spend": round(row.spend, 2),
                "revenue": round(row.revenue, 2),
                "roas": round(row_roas, 4),
                # 明细里分母为 0 用 None 表示"不可计算"，避免 0 被误读成优秀 CPA。
                "cpa": round(row.spend / row.orders, 2) if row.orders else None,
                "cvr": round(row.orders / row.clicks, 4) if row.clicks else None,
                "orders": row.orders,
                "signal": signal,
            }
        )
        if row.orders == 0 and row.spend > 0:
            zero_order_spend += row.spend
            zero_order_names.append(name)
    campaign_table.sort(key=lambda item: (-item["spend"], item["campaign"]))

    # ---- 确定性信号：按"点击 → 转化 → 整体盈利 → 客单"的证据链排列 ----
    signals: list[dict[str, Any]] = []
    if impressions > 0 and ctr_raw < 0.008:
        severity: SignalSeverity = "P0" if ctr_raw < 0.004 else "P1"
        signals.append(
            _signal(
                "点击",
                "low_ctr",
                severity,
                [
                    f"CTR {ctr_raw:.4f} < 0.0080 基线",
                    f"曝光 {impressions} 次仅获得 {clicks} 次点击",
                    f"分级：CTR<0.0040 为 P0，<0.0080 为 P1，当前 {severity}",
                ],
            )
        )
    if clicks > 0 and cvr_raw < 0.02:
        severity = "P0" if cvr_raw < 0.01 else "P1"
        signals.append(
            _signal(
                "转化",
                "low_cvr",
                severity,
                [
                    f"CVR {cvr_raw:.4f} < 0.0200 基线",
                    f"{clicks} 次点击仅成交 {orders} 单",
                    f"分级：CVR<0.0100 为 P0，<0.0200 为 P1，当前 {severity}",
                ],
            )
        )
    if cpa_raw > cpa_max:
        severity = "P0" if cpa_raw > cpa_max * 1.3 else "P1"
        signals.append(
            _signal(
                "转化",
                "cpa_over",
                severity,
                [
                    f"CPA {cpa_raw:.2f} > CPA_max {cpa_max:.2f}",
                    f"单均贡献毛利 {contribution:.2f} 已被广告费吃穿",
                    f"分级：CPA>CPA_max×1.3 为 P0，超过 CPA_max 为 P1，当前 {severity}",
                ],
            )
        )
    if spend_raw > 0 and roas_raw < roas_min:
        signals.append(
            _signal(
                "转化",
                "roas_below",
                "P0",
                [
                    f"ROAS {roas_raw:.4f} < ROAS_min {roas_min:.4f}",
                    f"花费 {spend_raw:.2f} 仅换回 {revenue_raw:.2f}，窗口内广告整体亏损",
                    "整体不赚钱不设 P1/P2，立即 P0 止损",
                ],
            )
        )
    waste_ratio = zero_order_spend / spend_raw if spend_raw else 0.0
    if zero_order_names and waste_ratio > 0.15:
        signals.append(
            _signal(
                "点击",
                "zero_order_spend",
                "P0",
                [
                    f"{len(zero_order_names)} 个 campaign 有花费零成交：{'、'.join(zero_order_names)}",
                    f"无效花费 {zero_order_spend:.2f} / 总花费 {spend_raw:.2f} = {waste_ratio:.2%}",
                    "超过 15% 止损线：先关停零成交计划，再谈放量",
                ],
            )
        )
    if aov < a.economics.cogs * 1.6:
        signals.append(
            _signal(
                "客单",
                "low_aov",
                "P1",
                [
                    f"AOV {aov:.2f} < COGS×1.6 = {a.economics.cogs * 1.6:.2f}",
                    "客单价覆盖不了成本带，需靠组合装 / 加配置抬客单",
                ],
            )
        )

    return {
        "funnel": funnel,
        "breakeven": breakeven,
        "campaign_table": campaign_table,
        "signals": signals,
        "waste_ratio": round(waste_ratio, 4),
        "window_days": a.window_days,
        "currency": "BRL",
    }


@tool(
    "budget_realloc",
    "把最差 campaign 的预算按 shift_ratio 挪给 ROAS 最高的 campaign，输出 moves（含逐 campaign 新预算与理由）"
    "和恒定 ROAS 外推的预期 ROAS 提升；数据不足直接报错。",
    BudgetReallocArgs,
    tags=("06",),
)
def budget_realloc(a: BudgetReallocArgs) -> dict[str, Any]:
    """预算再分配：worst → best，按各 campaign 当前 ROAS 恒定外推新混合 ROAS。"""
    if len(a.rows) < 2:
        raise ToolError("budget_realloc", "campaign 少于 2 个，无法做预算再分配")

    total_orders = sum(r.orders for r in a.rows)
    total_revenue = sum(r.revenue for r in a.rows)
    total_spend = sum(r.spend for r in a.rows)
    if total_orders <= 0:
        raise ToolError("budget_realloc", "窗口内没有成交订单，无法判断最佳承接位")
    if total_revenue <= 0:
        raise ToolError("budget_realloc", "窗口内成交额为 0，无法判断最佳承接位")

    aov = total_revenue / total_orders
    _, _, roas_min = _breakeven(aov, a.economics, "budget_realloc")

    entries = [
        {
            "index": index,
            "name": _campaign_name(row, index),
            "spend": row.spend,
            "revenue": row.revenue,
            "orders": row.orders,
            "roas": _row_roas(row),
        }
        for index, row in enumerate(a.rows)
    ]

    # 最佳位必须有订单且有花费，否则无法按"当前 ROAS 恒定"外推增量。
    best_candidates = [e for e in entries if e["orders"] > 0 and e["spend"] > 0]
    worst_candidates = [e for e in entries if e["spend"] > 0 and e["roas"] < roas_min]
    if not best_candidates or not worst_candidates:
        # 没有可挪的亏损位（或没有可承接的最佳位）时，不制造动作。
        return {"moves": [], "expected_roas_uplift": 0.0, "guarded": True}

    best = sorted(best_candidates, key=lambda e: (-e["roas"], -e["spend"], e["name"]))[0]
    worst = sorted(worst_candidates, key=lambda e: (e["roas"], -e["spend"], e["name"]))[0]
    if best["index"] == worst["index"]:
        return {"moves": [], "expected_roas_uplift": 0.0, "guarded": True}

    shift = round(worst["spend"] * a.shift_ratio, 2)
    if shift <= 0:
        return {"moves": [], "expected_roas_uplift": 0.0, "guarded": True}

    new_worst_spend = round(worst["spend"] - shift, 2)
    new_best_spend = round(best["spend"] + shift, 2)

    # 简单加权估计：假设各 campaign 的边际 ROAS 等于当前 ROAS。
    new_revenue = total_revenue
    new_revenue += worst["revenue"] * (new_worst_spend / worst["spend"] - 1)
    new_revenue += best["revenue"] * (new_best_spend / best["spend"] - 1)
    old_roas = total_revenue / total_spend
    new_roas = new_revenue / total_spend
    expected_roas_uplift = round(new_roas - old_roas, 4)

    moves = [
        {
            "campaign": worst["name"],
            "current_spend": round(worst["spend"], 2),
            "suggested_spend": new_worst_spend,
            "delta": round(-shift, 2),
            "reason": f"ROAS {worst['roas']:.4f} 低于盈亏线 {roas_min:.4f}，先减 {shift:.2f} 预算止血",
        },
        {
            "campaign": best["name"],
            "current_spend": round(best["spend"], 2),
            "suggested_spend": new_best_spend,
            "delta": round(shift, 2),
            "reason": f"ROAS {best['roas']:.4f} 为全场最高（盈亏线 {roas_min:.4f}），承接 {shift:.2f} 增量优先放量",
        },
    ]
    return {"moves": moves, "expected_roas_uplift": expected_roas_uplift, "guarded": True}
