"""01 市场分析：确定性指标计算。

为什么必须用脚本：CR4、HHI、加权分位数、价格带波谷、配置交叉表，
任何一项让模型"估"都会造成口径不可复核。这里每个数都有公式。
"""

from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.tools.base import ToolError, tool
from app.tools.platform_data import BANNED_TERMS, EXAGGERATION_TERMS, PLATFORM_RULES


class Row(BaseModel):
    model_config = ConfigDict(extra="forbid")
    price: float = Field(gt=0)
    monthly_sales: int = Field(default=0, ge=0)
    rating: float | None = Field(default=None, ge=0, le=5)
    reviews: int = Field(default=0, ge=0)
    config: dict[str, str] = Field(default_factory=dict)


class MarketMetricsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rows: list[Row] = Field(min_length=1, description="竞品导出数据，至少 3 条")
    currency: str = "BRL"


def _weighted_quantile(pairs: list[tuple[float, float]], q: float) -> float:
    """按销量加权的分位数（pairs 已按价格升序）。"""
    total = sum(w for _, w in pairs)
    if total <= 0:
        # 没有销量数据时退化为数量分位
        vals = sorted(p for p, _ in pairs)
        return vals[min(len(vals) - 1, max(0, int(q * len(vals))))]
    target = total * q
    acc = 0.0
    for price, weight in pairs:
        acc += weight
        if acc >= target:
            return price
    return pairs[-1][0]


def _bucketize(prices: list[float], sales: list[int], n: int = 6) -> list[dict[str, Any]]:
    lo, hi = min(prices), max(prices)
    if hi - lo < 1e-9:
        return [{"low": lo, "high": hi, "sku": len(prices), "sales": sum(sales)}]
    width = (hi - lo) / n
    buckets = [{"low": lo + i * width, "high": lo + (i + 1) * width, "sku": 0, "sales": 0} for i in range(n)]
    for price, sale in zip(prices, sales, strict=False):
        idx = min(n - 1, int((price - lo) / width))
        buckets[idx]["sku"] += 1
        buckets[idx]["sales"] += sale
    return buckets


@tool(
    "market_metrics",
    "计算市场集中度(CR4/HHI)、加权价格分位、价格带波谷、配置组合交叉表。返回可直接写入市场报告的结构化指标。",
    MarketMetricsArgs,
    tags=("01",),
)
def market_metrics(a: MarketMetricsArgs) -> dict[str, Any]:
    rows = a.rows
    if len(rows) < 3:
        raise ToolError("market_metrics", f"竞品数据不足：至少需要 3 条，当前 {len(rows)} 条，无法计算集中度与价格带")
    prices = [r.price for r in rows]
    sales = [max(0, r.monthly_sales) for r in rows]
    total_sales = sum(sales)
    n = len(rows)
    gmv = sum(p * s for p, s in zip(prices, sales, strict=False))

    # ---- 集中度：按销量份额 ----
    if total_sales > 0:
        shares = [s / total_sales for s in sales]
    else:
        shares = [1 / n] * n
    cr4 = sum(sorted(shares, reverse=True)[:4])
    hhi = sum(sh * sh for sh in shares) * 10000

    # ---- 加权价格分位 ----
    pairs = sorted(zip(prices, [s if total_sales > 0 else 1 for s in sales], strict=False), key=lambda x: x[0])
    p25 = _weighted_quantile(pairs, 0.25)
    median = _weighted_quantile(pairs, 0.50)
    p75 = _weighted_quantile(pairs, 0.75)

    # ---- 价格带波谷：相邻价格带的 SKU 密度出现"局部最低"，且该带仍有成交 ----
    buckets = _bucketize(prices, sales, n=6)
    for b in buckets:
        sku_share = b["sku"] / max(1, n)
        sales_share = b["sales"] / max(1, total_sales) if total_sales else 0.0
        b["supply_index"] = round(sku_share, 4)
        b["demand_index"] = round(sales_share, 4)
        b["efficiency"] = round(sales_share / sku_share, 3) if sku_share > 0 else 0.0

    # 只在 p25~p75（真实成交密集区）内找波谷，避免把长尾高价段当成机会
    lo_idx, hi_idx = 0, len(buckets) - 1
    for i, b in enumerate(buckets):
        if b["high"] <= p25 * 1.05:
            lo_idx = i
        if b["low"] < p75 * 0.95:
            hi_idx = i
    window = list(range(lo_idx, min(hi_idx + 1, len(buckets))))
    valleys = []
    for i in window:
        b = buckets[i]
        if b["sku"] == 0:
            continue
        left = buckets[i - 1]["sku"] if i > 0 else b["sku"]
        right = buckets[i + 1]["sku"] if i + 1 < len(buckets) else b["sku"]
        if b["sku"] <= left and b["sku"] <= right and b["demand_index"] >= 0.02:
            valleys.append(b)

    if valleys:
        valley = max(valleys, key=lambda b: (b["efficiency"], b["demand_index"]))
        valley_basis = "相邻价格带 SKU 密度局部最低且仍有成交"
    else:
        # 没有明显波谷：退化为窗口内供需效率最高的带
        pool = [b for b in (buckets[i] for i in window) if b["sku"] > 0] or buckets
        valley = max(pool, key=lambda b: (b["efficiency"], b["demand_index"]))
        valley_basis = "无局部断档，取供需效率最高的价格带"

    valley_low = round(valley["low"] * 0.95, 2)
    valley_high = round(valley["high"] * 1.05, 2)

    # ---- 配置组合交叉表 ----
    combos: dict[str, dict[str, Any]] = {}
    for r in rows:
        if not r.config:
            continue
        key = " / ".join(f"{k}:{v}" for k, v in sorted(r.config.items()))
        c = combos.setdefault(key, {"combo": key, "sku": 0, "sales": 0, "price_sum": 0.0})
        c["sku"] += 1
        c["sales"] += r.monthly_sales
        c["price_sum"] += r.price * max(1, r.monthly_sales)
    config_matrix = []
    for c in sorted(combos.values(), key=lambda x: -x["sales"])[:8]:
        denom = max(1, c["sales"])
        config_matrix.append(
            {
                "combo": c["combo"],
                "share": round(c["sales"] / max(1, total_sales), 3),
                "avg_price": round(c["price_sum"] / denom, 2),
                "sales": c["sales"],
            }
        )

    # ---- 需求指数：月销总量对数归一（2 万件/月 记为 1.0）----
    demand_index = round(min(1.0, math.log1p(total_sales) / math.log1p(20000)), 3)
    ratings = [r.rating for r in rows if r.rating is not None]
    review_total = sum(r.reviews for r in rows)

    return {
        "competitor_count": n,
        "currency": a.currency,
        "cr4": round(cr4, 3),
        "hhi": round(hhi, 1),
        "concentration_label": _concentration_label(cr4, hhi),
        "avg_price": round(sum(prices) / n, 2),
        "weighted_avg_price": round(gmv / total_sales, 2) if total_sales else round(sum(prices) / n, 2),
        "gmv_estimate": round(gmv, 2),
        "total_monthly_sales": total_sales,
        "demand_index": demand_index,
        "avg_rating": round(sum(ratings) / len(ratings), 2) if ratings else None,
        "review_pool": review_total,
        "price_band": {
            "p25": round(p25, 2),
            "median": round(median, 2),
            "p75": round(p75, 2),
            "valley_low": valley_low,
            "valley_high": valley_high,
            "recommended_low": round(valley_low * 0.98, 2),
            "recommended_high": round(valley_high * 1.02, 2),
            "valley_efficiency": valley.get("efficiency", 0),
            "valley_basis": valley_basis,
            "buckets": [{k: v for k, v in b.items() if not k.startswith("_")} for b in buckets],
        },
        "config_matrix": config_matrix,
        "method": {
            "cr4": "销量份额 Top4 之和",
            "hhi": "Σ(份额²)×10000，>2500 视为高集中",
            "quantile": "按月销量加权的价格分位",
            "valley": "p25~p75 内找到 SKU 密度局部最低、且销量占比 ≥2% 的价格带，边界外扩 5%",
        },
    }


def _concentration_label(cr4: float, hhi: float) -> str:
    if hhi >= 2500 or cr4 >= 0.7:
        return "高集中（头部垄断，硬碰硬成本高）"
    if hhi >= 1500 or cr4 >= 0.5:
        return "中集中（有头部但存在缝隙）"
    return "低集中（分散市场，可从细分切入）"


class PlatformRulesArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    platform: str = Field(default="shopee", description="shopee|mercadolivre|amazon|tiktok|shopify")


@tool(
    "platform_rules",
    "查询目标平台的标题字符上限、前段权重、主图规范、佣金费率与禁用词表。",
    PlatformRulesArgs,
    tags=("common",),
)
def platform_rules(a: PlatformRulesArgs) -> dict[str, Any]:
    key = a.platform.lower()
    if key not in PLATFORM_RULES:
        return {"platform": key, "found": False, "supported": sorted(PLATFORM_RULES)}
    return {
        "platform": key,
        "found": True,
        "rules": PLATFORM_RULES[key],
        "banned_terms": BANNED_TERMS.get(key, []),
        "exaggeration_terms": EXAGGERATION_TERMS,
    }
