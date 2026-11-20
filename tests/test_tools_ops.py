"""06 / 07 工具模块的确定性单测。

覆盖：
- 广告漏斗数学（手算可复核的 fixture）；
- roas_below / 零成交浪费信号；
- budget_realloc 从最差挪向最佳；
- 评论多标签归因、可修率 / 误解率；
- 卖点排序。
"""

from __future__ import annotations

import pytest

import app.tools.ads  # noqa: F401 —— 导入即向注册中心注册
import app.tools.review  # noqa: F401
from app.tools.base import ToolError, get_tool, invoke_tool

ECONOMICS = {
    "cogs": 20.0,
    "freight": 5.0,
    "commission_rate": 0.14,
    "payment_rate": 0.02,
    "coupon_budget": 0.05,
}

# A：ROAS 5.0；B：ROAS 2.0。合计 150000 曝光 / 1250 点击 / 55 单 / 750 花费 / 3000 成交额。
ROWS_AB = [
    {"campaign": "A", "impressions": 100000, "clicks": 1000, "orders": 50, "spend": 500, "revenue": 2500},
    {"campaign": "B", "impressions": 50000, "clicks": 250, "orders": 5, "spend": 250, "revenue": 500},
]

REVIEWS = [
    {"rating": 1, "text": "Quebrou na primeira semana, qualidade muito ruim", "variant": "Preto", "helpful": 5},
    {"rating": 2, "text": "A embalagem veio amassada e a caixa estava aberta", "variant": "Preto", "helpful": 2},
    {"rating": 5, "text": "Ótima qualidade, material resistente", "variant": "Azul", "helpful": 1},
    {"rating": 4, "text": "Size fits perfectly, great quality", "variant": "Azul", "helpful": 0},
    {"rating": 3, "text": "Bateria dura pouco", "variant": "Azul", "helpful": 0},
    {"rating": 1, "text": "", "variant": "Azul", "helpful": 0},
    {"rating": 2, "text": "Not as described, the photo is different", "variant": "Preto", "helpful": 0},
]

SELLING_REVIEWS = [
    {"rating": 5, "text": "Bateria dura muito e o material é ótimo", "variant": "A"},
    {"rating": 4, "text": "Bateria excelente", "variant": "A"},
    {"rating": 1, "text": "A bateria descarrega rápido e o material é ruim", "variant": "B"},
    {"rating": 2, "text": "Battery dies fast", "variant": "B"},
]


def _invoke(tool_name: str, payload: dict) -> dict:
    result, _attrs = invoke_tool(tool_name, payload)
    return result


def _ad_payload(rows: list[dict]) -> dict:
    return {"rows": rows, "economics": dict(ECONOMICS), "window_days": 7}


def _budget_payload(rows: list[dict], shift_ratio: float = 0.3) -> dict:
    return {"rows": rows, "economics": dict(ECONOMICS), "shift_ratio": shift_ratio}


def _review_payload(reviews: list[dict] | None = None) -> dict:
    return {"reviews": REVIEWS if reviews is None else reviews}


def test_funnel_math_matches_hand_calculation() -> None:
    result = _invoke("ad_metrics", _ad_payload(ROWS_AB))
    funnel = result["funnel"]

    assert funnel["impressions"] == 150000
    assert funnel["clicks"] == 1250
    assert funnel["orders"] == 55
    assert funnel["spend"] == 750.0
    assert funnel["revenue"] == 3000.0
    # ctr=1250/150000, cvr=55/1250, cpc=750/1250, cpa=750/55, roas=3000/750, aov=3000/55
    assert funnel["ctr"] == pytest.approx(0.0083)
    assert funnel["cvr"] == pytest.approx(0.044)
    assert funnel["cpc"] == pytest.approx(0.6)
    assert funnel["cpa"] == pytest.approx(13.64)
    assert funnel["roas"] == pytest.approx(4.0)
    assert funnel["aov"] == pytest.approx(54.55)

    breakeven = result["breakeven"]
    # contribution = 54.5454×(1−0.14−0.02−0.05) − 20 − 5 = 18.0909
    assert breakeven["cpa_max"] == pytest.approx(18.09)
    assert breakeven["contribution_margin"] == pytest.approx(18.09)
    assert breakeven["roas_min"] == pytest.approx(3.0151)
    assert breakeven["current_roas_vs_target"] == pytest.approx(1.3267)
    assert result["currency"] == "BRL"
    assert result["window_days"] == 7
    assert result["waste_ratio"] == 0.0


def test_roas_below_signal_fires_when_unprofitable() -> None:
    rows = [
        {"campaign": "Loss", "impressions": 10000, "clicks": 100, "orders": 5, "spend": 200, "revenue": 250},
    ]
    result = _invoke("ad_metrics", _ad_payload(rows))
    signals = {signal["code"]: signal for signal in result["signals"]}

    assert result["funnel"]["roas"] == pytest.approx(1.25)
    assert "roas_below" in signals
    assert signals["roas_below"]["layer"] == "转化"
    assert signals["roas_below"]["severity"] == "P0"
    assert signals["roas_below"]["evidence"]
    # ROAS 1.25 低于盈亏线 3.4483，属于整体不赚钱
    assert result["breakeven"]["roas_min"] == pytest.approx(3.4483)


def test_zero_order_spend_waste_signal() -> None:
    rows = [
        {"campaign": "P1", "impressions": 10000, "clicks": 100, "orders": 10, "spend": 100, "revenue": 500},
        {"campaign": "P2", "impressions": 10000, "clicks": 100, "orders": 0, "spend": 100, "revenue": 0},
    ]
    result = _invoke("ad_metrics", _ad_payload(rows))
    signals = {signal["code"]: signal for signal in result["signals"]}

    assert result["waste_ratio"] == pytest.approx(0.5)
    assert "zero_order_spend" in signals
    assert signals["zero_order_spend"]["layer"] == "点击"
    assert signals["zero_order_spend"]["severity"] == "P0"
    assert "P2" in signals["zero_order_spend"]["evidence"][0]


def test_campaign_table_signal_rule() -> None:
    rows = [
        {"campaign": "Scale", "impressions": 10000, "clicks": 100, "orders": 10, "spend": 100, "revenue": 500},
        {"campaign": "Cut", "impressions": 10000, "clicks": 100, "orders": 2, "spend": 100, "revenue": 100},
    ]
    result = _invoke("ad_metrics", _ad_payload(rows))
    table = {row["campaign"]: row for row in result["campaign_table"]}

    # roas_min = 3.4483：Scale 5.0 ≥ 4.4828 → scale；Cut 1.0 < 2.7586 → cut
    assert table["Scale"]["signal"] == "scale"
    assert table["Cut"]["signal"] == "cut"
    # 按 spend 降序，花费相同按 campaign 名稳定排序
    assert [row["campaign"] for row in result["campaign_table"]] == ["Cut", "Scale"]


def test_budget_realloc_moves_money_from_worst_to_best() -> None:
    result = _invoke("budget_realloc", _budget_payload(ROWS_AB, shift_ratio=0.3))
    moves = {move["campaign"]: move for move in result["moves"]}

    # B 的 ROAS 2.0 < 3.0151 判定为最差，A 的 5.0 为最佳；挪 30%：250×0.3=75
    assert moves["B"]["current_spend"] == pytest.approx(250.0)
    assert moves["B"]["suggested_spend"] == pytest.approx(175.0)
    assert moves["B"]["delta"] == pytest.approx(-75.0)
    assert moves["A"]["current_spend"] == pytest.approx(500.0)
    assert moves["A"]["suggested_spend"] == pytest.approx(575.0)
    assert moves["A"]["delta"] == pytest.approx(75.0)
    # 恒定 ROAS 外推：新营收 3225 / 750 = 4.3，较 4.0 提升 0.3
    assert result["expected_roas_uplift"] == pytest.approx(0.3)
    assert result["guarded"] is True


def test_budget_realloc_requires_two_campaigns() -> None:
    with pytest.raises(ToolError, match="少于 2 个"):
        invoke_tool("budget_realloc", _budget_payload(ROWS_AB[:1]))


def test_review_classification_counts() -> None:
    result = _invoke("review_stats", _review_payload())
    attribution = result["attribution"]
    counts = attribution["counts"]

    # 质量：r1/r3/r4；尺寸：r4；描述：r7；包装：r2；电池：r5；空文本：r6 → 其他
    assert counts == {
        "质量做工": 3,
        "尺寸容量": 1,
        "物流时效": 0,
        "描述不符": 1,
        "包装破损": 1,
        "使用难度": 0,
        "电池续航": 1,
        "客服售后": 0,
        "其他": 1,
    }
    assert attribution["total"] == 7
    assert attribution["negative_rate"] == pytest.approx(0.5714)
    assert result["top_praise"] == [
        {"point": "质量做工", "mentions": 2},
        {"point": "尺寸容量", "mentions": 1},
    ]
    assert {item["point"] for item in result["top_complaint"]} == {"质量做工", "描述不符", "包装破损"}
    assert len(result["taxonomy"]) == 8
    assert "quebrou" in result["taxonomy"]["质量做工"]


def test_fixable_and_misunderstanding_rates() -> None:
    result = _invoke("review_stats", _review_payload())
    attribution = result["attribution"]

    # 负评 r1/r2/r6/r7 共 4 条；可修 r2（包装）+ r7（描述）→ 0.5；误解 r7 → 0.25
    assert attribution["negative_rate"] == pytest.approx(0.5714)
    assert attribution["fixable_rate"] == pytest.approx(0.5)
    assert attribution["misunderstanding_rate"] == pytest.approx(0.25)


def test_variant_breakdown_and_quotes() -> None:
    result = _invoke("review_stats", _review_payload())
    variants = result["variant_breakdown"]

    assert variants["Preto"]["count"] == 3
    assert variants["Preto"]["avg_rating"] == pytest.approx(1.67)
    assert variants["Preto"]["negative_count"] == 3
    assert variants["Azul"]["count"] == 4
    assert variants["Azul"]["avg_rating"] == pytest.approx(3.25)
    assert variants["Azul"]["negative_count"] == 1

    quotes = result["sample_quotes"]
    # 负评原声按 helpful 降序：r1(5) → r2(2) → r7(0)，空文本 r6 被跳过
    assert [quote["rating"] for quote in quotes] == [1, 2, 2]
    assert all(len(quote["text"]) <= 120 for quote in quotes)


def test_selling_point_rank_ordering() -> None:
    result = _invoke(
        "selling_point_rank",
        {"reviews": SELLING_REVIEWS, "selling_points": ["bateria", "material", "battery", "防水"]},
    )
    ranking = result["ranking"]

    assert [item["point"] for item in ranking] == ["bateria", "material", "battery", "防水"]
    assert [item["mentions"] for item in ranking] == [3, 2, 1, 0]
    assert ranking[0]["positive_mentions"] == 2
    assert ranking[0]["negative_mentions"] == 1
    assert ranking[0]["positive_ratio"] == pytest.approx(0.6667)
    assert ranking[1]["positive_ratio"] == pytest.approx(0.5)
    assert ranking[3]["positive_ratio"] == 0.0


def test_selling_point_rank_rejects_empty_points() -> None:
    with pytest.raises(ToolError, match="不能为空"):
        invoke_tool("selling_point_rank", {"reviews": SELLING_REVIEWS, "selling_points": []})


def test_tools_registered_with_expected_tags() -> None:
    assert get_tool("ad_metrics").tags == ("06",)
    assert get_tool("budget_realloc").tags == ("06",)
    assert get_tool("review_stats").tags == ("07",)
    assert get_tool("selling_point_rank").tags == ("07",)
