"""7 个 Agent 的注册表：输入适配 + 证据工具 + 事后校验。

这里就是"业务配置"，新增一个 Agent 只需要在 AGENTS 里加一条。
"""

from __future__ import annotations

from typing import Any

from app.agents.base import AgentContext, AgentSpec
from app.agents.gates import run_gates
from app.tools.platform_data import PLATFORM_RULES


def _platform(ctx: AgentContext) -> str:
    return str(ctx.project.get("platform") or "shopee").lower()


def _econ(ctx: AgentContext) -> dict[str, Any]:
    """把项目 brief 里的成本结构补齐成工具参数（平台费率来自规则快照）。"""
    brief = ctx.brief
    e = dict(brief.get("economics") or {})
    rules = PLATFORM_RULES.get(_platform(ctx), PLATFORM_RULES["shopee"])
    return {
        "cogs": float(e.get("cogs", 0)),
        "freight": float(e.get("freight", 0)),
        "commission_rate": float(e.get("commission_rate", rules["commission_rate"])),
        "payment_rate": float(e.get("payment_rate", rules["payment_rate"])),
        "ad_rate": float(e.get("ad_rate", 0.10)),
        "coupon_budget": float(e.get("coupon_budget", 0.05)),
        "target_margin": float(e.get("target_margin", 0.15)),
        "currency": brief.get("currency", "BRL"),
    }


# --------------------------------------------------------------------------- #
# 输入适配
# --------------------------------------------------------------------------- #
def _in_market(ctx: AgentContext) -> dict[str, Any]:
    b = ctx.brief
    return {
        "product": b.get("product", ctx.project.get("name", "")),
        "category": b.get("category", ctx.project.get("category", "")),
        "platform": _platform(ctx),
        "market": ctx.project.get("market", "BR"),
        "currency": b.get("currency", "BRL"),
        "competitor_rows": b.get("competitor_rows") or [],
    }


def _in_strategy(ctx: AgentContext) -> dict[str, Any]:
    b = ctx.brief
    e = _econ(ctx)
    return {
        "product": b.get("product", ctx.project.get("name", "")),
        "market_analysis": ctx.artifacts.get("market_report") or {},
        "economics": {
            "cogs": e["cogs"],
            "freight": e["freight"],
            "platform_fee_rate": e["commission_rate"],
            "payment_fee_rate": e["payment_rate"],
            "ad_rate": e["ad_rate"],
            "target_margin": e["target_margin"],
            "coupon_budget": e["coupon_budget"],
            "currency": e["currency"],
        },
        "config_options": b.get("config_options") or [],
    }


def _in_listing(ctx: AgentContext) -> dict[str, Any]:
    b = ctx.brief
    return {
        "product": b.get("product", ctx.project.get("name", "")),
        "strategy": ctx.artifacts.get("product_strategy") or {},
        "keywords": b.get("keywords") or [],
        "specs": b.get("facts") or {},
        "platform": _platform(ctx),
        "language": ctx.project.get("language", "pt-BR"),
    }


def _in_image(ctx: AgentContext) -> dict[str, Any]:
    b = ctx.brief
    return {
        "product": b.get("product", ctx.project.get("name", "")),
        "strategy": ctx.artifacts.get("product_strategy") or {},
        "platform": _platform(ctx),
        "image_path": b.get("cover_image_path"),
    }


def _in_video(ctx: AgentContext) -> dict[str, Any]:
    b = ctx.brief
    return {
        "product": b.get("product", ctx.project.get("name", "")),
        "strategy": ctx.artifacts.get("product_strategy") or {},
        "platform": _platform(ctx),
        "target_duration": int(b.get("target_duration") or 20),
    }


def _in_ads(ctx: AgentContext) -> dict[str, Any]:
    b = ctx.brief
    e = _econ(ctx)
    rows = b.get("ad_rows") or []
    if not rows:
        # 允许从"最新一次投放数据"回填：演示闭环时把 ad_rows 注入 brief
        rows = b.get("ad_rows_latest") or []
    return {
        "rows": [
            {
                "campaign": r.get("campaign", ""),
                "impressions": int(r.get("impressions", 0)),
                "clicks": int(r.get("clicks", 0)),
                "orders": int(r.get("orders", 0)),
                "spend": float(r.get("spend", 0)),
                "revenue": float(r.get("revenue", 0)),
            }
            for r in rows
        ],
        "economics": {
            "cogs": e["cogs"],
            "freight": e["freight"],
            "platform_fee_rate": e["commission_rate"],
            "payment_fee_rate": e["payment_rate"],
            "ad_rate": e["ad_rate"],
            "target_margin": e["target_margin"],
            "coupon_budget": e["coupon_budget"],
            "currency": e["currency"],
        },
        "window_days": int(b.get("ad_window_days") or 7),
    }


def _in_review(ctx: AgentContext) -> dict[str, Any]:
    b = ctx.brief
    listing = ctx.artifacts.get("listing_pack") or {}
    return {
        "reviews": b.get("reviews") or [],
        "listing_title": ((listing.get("title") or {}).get("text") or "")[:120],
    }


# --------------------------------------------------------------------------- #
# 证据工具（先算，再让模型解读）
# --------------------------------------------------------------------------- #
def _ev_market(ctx: AgentContext, inp: dict, call) -> dict[str, Any]:
    rows = [
        {
            "price": float(r["price"]),
            "monthly_sales": int(r.get("monthly_sales", 0)),
            "rating": r.get("rating"),
            "reviews": int(r.get("reviews", 0)),
            "config": r.get("config") or {},
        }
        for r in inp["competitor_rows"]
    ]
    return {
        "market_metrics": call("market_metrics", {"rows": rows, "currency": inp.get("currency", "BRL")}),
        "platform_rules": call("platform_rules", {"platform": inp["platform"]}),
    }


def _ev_strategy(ctx: AgentContext, inp: dict, call) -> dict[str, Any]:
    e = inp["economics"]
    ue = call(
        "unit_economics",
        {
            "cogs": e["cogs"],
            "freight": e["freight"],
            "commission_rate": e["platform_fee_rate"],
            "payment_rate": e["payment_fee_rate"],
            "ad_rate": e["ad_rate"],
            "coupon_budget": e["coupon_budget"],
            "target_margin": e["target_margin"],
            "currency": e.get("currency", "BRL"),
        },
    )
    ladder = call(
        "sku_ladder",
        {"target_price": ue["target_price"], "variants": inp.get("config_options") or []},
    )
    return {"unit_economics": ue, "sku_ladder": ladder}


def _ev_listing(ctx: AgentContext, inp: dict, call) -> dict[str, Any]:
    return {"platform_rules": call("platform_rules", {"platform": inp["platform"]})}


def _ev_image(ctx: AgentContext, inp: dict, call) -> dict[str, Any]:
    return {"platform_rules": call("platform_rules", {"platform": inp["platform"]})}


def _ev_video(ctx: AgentContext, inp: dict, call) -> dict[str, Any]:
    return {"platform_rules": call("platform_rules", {"platform": inp["platform"]})}


def _ev_ads(ctx: AgentContext, inp: dict, call) -> dict[str, Any]:
    e = inp["economics"]
    return {
        "ad_metrics": call(
            "ad_metrics",
            {
                "rows": inp["rows"],
                "economics": {
                    "cogs": e["cogs"],
                    "freight": e["freight"],
                    "commission_rate": e["platform_fee_rate"],
                    "payment_rate": e["payment_fee_rate"],
                    "coupon_budget": e["coupon_budget"],
                },
                "window_days": inp.get("window_days", 7),
            },
        )
    }


def _ev_review(ctx: AgentContext, inp: dict, call) -> dict[str, Any]:
    stats = call("review_stats", {"reviews": inp["reviews"]})
    strategy = ctx.artifacts.get("product_strategy") or {}
    points = [strategy.get("main_selling_point", "")] + list(strategy.get("secondary_points") or [])
    points = [p for p in points if p]
    rank = (
        call("selling_point_rank", {"reviews": inp["reviews"], "selling_points": points}) if points else {"ranking": []}
    )
    return {"review_stats": stats, "selling_point_rank": rank}


AGENTS: list[AgentSpec] = [
    AgentSpec(
        id="01_market_research",
        name="MarketResearchAgent",
        cn_name="市场分析",
        artifact_kind="market_report",
        role="判断类目能否进入、从哪个价格带切入",
        build_input=_in_market,
        evidence=_ev_market,
        tools=["market_metrics", "platform_rules"],
        max_steps=2,
    ),
    AgentSpec(
        id="02_product_strategy",
        name="ProductStrategyAgent",
        cn_name="产品策略",
        artifact_kind="product_strategy",
        role="定位、配置阶梯、唯一主卖点、定价与 SKU 四角色",
        build_input=_in_strategy,
        evidence=_ev_strategy,
        tools=["unit_economics", "sku_ladder"],
        max_steps=2,
    ),
    AgentSpec(
        id="03_listing_creative",
        name="ListingCreativeAgent",
        cn_name="Listing 创意",
        artifact_kind="listing_pack",
        role="关键词分层、标题、五点卖点、详情页、参数溯源",
        build_input=_in_listing,
        evidence=_ev_listing,
        tools=["title_lint", "spec_consistency", "platform_rules"],
        gates=run_gates,
        max_steps=3,
    ),
    AgentSpec(
        id="04_product_image",
        name="ProductImageAgent",
        cn_name="主图与详情图",
        artifact_kind="image_pack",
        role="9 图规划、出图 Prompt、像素级合规",
        build_input=_in_image,
        evidence=_ev_image,
        tools=["check_main_image", "platform_rules"],
        gates=run_gates,
        max_steps=2,
    ),
    AgentSpec(
        id="05_product_video",
        name="ProductVideoAgent",
        cn_name="短视频",
        artifact_kind="video_pack",
        role="钩子、分镜、口播、字幕与时长校验",
        build_input=_in_video,
        evidence=_ev_video,
        tools=["video_timing", "platform_rules"],
        gates=run_gates,
        max_steps=2,
    ),
    AgentSpec(
        id="06_advertising",
        name="AdvertisingAgent",
        cn_name="广告优化",
        artifact_kind="ad_plan",
        role="漏斗诊断、盈亏线核算、P0/P1/P2 分级动作",
        build_input=_in_ads,
        evidence=_ev_ads,
        tools=["ad_metrics", "budget_realloc"],
        gates=run_gates,
        max_steps=3,
    ),
    AgentSpec(
        id="07_review",
        name="ReviewAnalysisAgent",
        cn_name="评论分析",
        artifact_kind="review_insight",
        role="八类归因、可修复率、详情页修补建议",
        build_input=_in_review,
        evidence=_ev_review,
        tools=["review_stats", "selling_point_rank"],
        gates=run_gates,
        max_steps=2,
    ),
]

AGENT_MAP: dict[str, AgentSpec] = {a.id: a for a in AGENTS}


def get_agent(agent_id: str) -> AgentSpec:
    if agent_id not in AGENT_MAP:
        raise KeyError(f"未知 Agent: {agent_id}")
    return AGENT_MAP[agent_id]
