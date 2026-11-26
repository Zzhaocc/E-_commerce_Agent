"""07 评论分析：确定性的评论归因与卖点排序（不调用任何模型）。

口径：
- 8 个固定类目 + "其他"，多标签子串匹配（不区分大小写），一条评论可命中多个类目；
- negative = rating ≤ 2；
- fixable_rate = 命中可修类目（描述不符 / 包装破损 / 使用难度 / 尺寸容量）的负评 / 负评总数；
- misunderstanding_rate = 命中"描述不符"的负评 / 负评总数；
- 卖点热度只统计 rating ≥ 4（好评）与 rating ≤ 2（差评），3 星视为噪音不计入分子分母。

所有比率分母为 0 时返回 0.0，空评论不崩、归入"其他"。
"""

from __future__ import annotations

from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field

from app.tools.base import ToolError, tool

# 固定类目 + 葡语 / 英语 / 中文关键词表；顺序即并列时的稳定输出顺序。
TAXONOMY: Final[dict[str, tuple[str, ...]]] = {
    "质量做工": (
        "qualidade",
        "quebrou",
        "rachou",
        "defeito",
        "costura",
        "material",
        "质量",
        "做工",
        "坏",
        "defect",
        "broke",
        "quality",
    ),
    "尺寸容量": (
        "tamanho",
        "pequeno",
        "grande",
        "cabe",
        "capacidade",
        "尺寸",
        "大小",
        "size",
        "small",
        "large",
    ),
    "物流时效": (
        "entrega",
        "atraso",
        "demorou",
        "chegou",
        "prazo",
        "物流",
        "发货",
        "delivery",
        "late",
        "shipping",
    ),
    "描述不符": (
        "não é como",
        "diferente",
        "anúncio",
        "foto",
        "esperava",
        "描述",
        "不符",
        "not as described",
        "different",
        "photo",
    ),
    "包装破损": (
        "embalagem",
        "amassado",
        "caixa",
        "quebrado",
        "包装",
        "破损",
        "packaging",
        "box",
        "dented",
    ),
    "使用难度": (
        "instrução",
        "manual",
        "difícil",
        "não entendi",
        "安装",
        "说明书",
        "instruction",
        "hard",
        "manual",
    ),
    "电池续航": (
        "bateria",
        "carrega",
        "autonomia",
        "dura",
        "电池",
        "续航",
        "battery",
        "charge",
        "lasts",
    ),
    "客服售后": (
        "atendimento",
        "suporte",
        "resposta",
        "devolução",
        "客服",
        "售后",
        "support",
        "refund",
        "return",
    ),
}

CATEGORY_ORDER: Final[tuple[str, ...]] = tuple(TAXONOMY)
OTHER_CATEGORY: Final[str] = "其他"
FIXABLE_CATEGORIES: Final[frozenset[str]] = frozenset({"描述不符", "包装破损", "使用难度", "尺寸容量"})
NEGATIVE_MAX_RATING: Final[int] = 2
POSITIVE_MIN_RATING: Final[int] = 4
QUOTE_LIMIT: Final[int] = 3
QUOTE_MAX_CHARS: Final[int] = 120
VARIANT_PLACEHOLDER: Final[str] = "未标注"


class ReviewRow(BaseModel):
    """单条评论（字段约束对齐 contracts.ReviewRow）。"""

    model_config = ConfigDict(extra="forbid")
    rating: int = Field(ge=1, le=5, description="星级 1-5")
    text: str = ""
    variant: str = ""
    date: str = ""
    helpful: int = Field(default=0, ge=0, description="有用票数")


class ReviewStatsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reviews: list[ReviewRow] = Field(min_length=1, description="评论列表，至少 1 条")


class SellingPointRankArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reviews: list[ReviewRow] = Field(min_length=1, description="评论列表，至少 1 条")
    selling_points: list[str] = Field(default_factory=list, description="待排序卖点，不能为空")


def _classify(text: str) -> list[str]:
    """多标签分类：返回命中的类目（保持 TAXONOMY 顺序），无命中返回空列表。"""
    lowered = text.casefold()
    if not lowered.strip():
        return []
    return [
        category
        for category, keywords in TAXONOMY.items()
        if any(keyword.casefold() in lowered for keyword in keywords)
    ]


def _rate(numerator: int, denominator: int) -> float:
    """比率兜底：分母为 0 返回 0.0。"""
    return round(numerator / denominator, 4) if denominator else 0.0


def _top_categories(hit_lists: list[list[str]], limit: int = 3) -> list[dict[str, Any]]:
    """按类目命中评论数排序，取前 limit 个（不包含"其他"）。"""
    counter: dict[str, int] = {category: 0 for category in CATEGORY_ORDER}
    for hits in hit_lists:
        for category in hits:
            if category in counter:
                counter[category] += 1
    ranked = sorted(
        ((category, count) for category, count in counter.items() if count > 0),
        key=lambda item: (-item[1], CATEGORY_ORDER.index(item[0])),
    )
    return [{"point": category, "mentions": count} for category, count in ranked[:limit]]


def _truncate(text: str) -> str:
    """截断到 120 字以内；被截断时用省略号占位，长度仍不超过 120。"""
    if len(text) <= QUOTE_MAX_CHARS:
        return text
    return text[: QUOTE_MAX_CHARS - 1] + "…"


@tool(
    "review_stats",
    "对评论做多标签归因：8 类目计数、负评率、可修率、误解率、变体维度统计、好评/差评 Top3 与负评原声摘录。",
    ReviewStatsArgs,
    tags=("07",),
)
def review_stats(a: ReviewStatsArgs) -> dict[str, Any]:
    """确定性评论归因，空文本不崩（归入"其他"）。"""
    classified: list[tuple[ReviewRow, list[str]]] = []
    counts: dict[str, int] = {category: 0 for category in CATEGORY_ORDER}
    counts[OTHER_CATEGORY] = 0

    for review in a.reviews:
        hits = _classify(review.text)
        if not hits:
            counts[OTHER_CATEGORY] += 1
        else:
            for category in hits:
                counts[category] += 1
        classified.append((review, hits))

    total = len(a.reviews)
    negative = [(review, hits) for review, hits in classified if review.rating <= NEGATIVE_MAX_RATING]
    negative_count = len(negative)

    fixable_negative = sum(1 for _, hits in negative if FIXABLE_CATEGORIES.intersection(hits))
    misunderstanding_negative = sum(1 for _, hits in negative if "描述不符" in hits)

    attribution = {
        "counts": counts,
        "total": total,
        "negative_rate": _rate(negative_count, total),
        "fixable_rate": _rate(fixable_negative, negative_count),
        "misunderstanding_rate": _rate(misunderstanding_negative, negative_count),
    }

    top_praise = _top_categories([hits for review, hits in classified if review.rating >= POSITIVE_MIN_RATING])
    top_complaint = _top_categories([hits for _, hits in negative])

    # ---- 变体维度：数量 / 均分 / 负评数，按数量降序保证稳定 ----
    variant_agg: dict[str, dict[str, int]] = {}
    for review, _ in classified:
        key = review.variant.strip() or VARIANT_PLACEHOLDER
        agg = variant_agg.setdefault(key, {"count": 0, "rating_sum": 0, "negative_count": 0})
        agg["count"] += 1
        agg["rating_sum"] += review.rating
        if review.rating <= NEGATIVE_MAX_RATING:
            agg["negative_count"] += 1
    variant_breakdown = {
        key: {
            "count": agg["count"],
            "avg_rating": round(agg["rating_sum"] / agg["count"], 2),
            "negative_count": agg["negative_count"],
        }
        for key, agg in sorted(variant_agg.items(), key=lambda item: (-item[1]["count"], item[0]))
    }

    # ---- 负评原声：有用票优先，其次星级更低者优先，再按原始顺序 ----
    quote_pool = [
        (index, review)
        for index, (review, _) in enumerate(classified)
        if review.rating <= NEGATIVE_MAX_RATING and review.text.strip()
    ]
    quote_pool.sort(key=lambda item: (-item[1].helpful, item[1].rating, item[0]))
    sample_quotes = [
        {"rating": review.rating, "text": _truncate(review.text.strip())} for _, review in quote_pool[:QUOTE_LIMIT]
    ]

    return {
        "attribution": attribution,
        "top_praise": top_praise,
        "top_complaint": top_complaint,
        "variant_breakdown": variant_breakdown,
        "sample_quotes": sample_quotes,
        "taxonomy": {category: list(keywords) for category, keywords in TAXONOMY.items()},
    }


@tool(
    "selling_point_rank",
    "统计每个卖点在好评(≥4 星)与差评(≤2 星)中的提及次数，按总提及降序输出正负向占比，用于选主卖点。",
    SellingPointRankArgs,
    tags=("07",),
)
def selling_point_rank(a: SellingPointRankArgs) -> dict[str, Any]:
    """卖点热度排序：mentions = 好评提及 + 差评提及（3 星不计）。"""
    points: list[str] = []
    seen: set[str] = set()
    for raw in a.selling_points:
        point = raw.strip()
        if point and point not in seen:
            seen.add(point)
            points.append(point)
    if not points:
        raise ToolError("selling_point_rank", "卖点列表不能为空")

    positive_texts = [review.text.casefold() for review in a.reviews if review.rating >= POSITIVE_MIN_RATING]
    negative_texts = [review.text.casefold() for review in a.reviews if review.rating <= NEGATIVE_MAX_RATING]

    ranking: list[dict[str, Any]] = []
    for point in points:
        needle = point.casefold()
        positive_mentions = sum(text.count(needle) for text in positive_texts)
        negative_mentions = sum(text.count(needle) for text in negative_texts)
        mentions = positive_mentions + negative_mentions
        ranking.append(
            {
                "point": point,
                "mentions": mentions,
                "positive_mentions": positive_mentions,
                "negative_mentions": negative_mentions,
                "positive_ratio": _rate(positive_mentions, mentions),
            }
        )
    ranking.sort(key=lambda item: (-item["mentions"], -item["positive_ratio"], item["point"]))
    return {"ranking": ranking}
