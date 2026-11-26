"""Agent 输入 / 输出契约（Pydantic v2）。

三条硬规则：
1. 输入契约 extra="allow"：允许业务方多传字段，但必填字段缺失直接 422，不许模型"猜"；
2. 输出契约 extra="forbid"：模型多写一个字段就校验失败，杜绝"看起来对"的幻觉字段；
3. 字段带枚举 / 范围约束：severity 只能是 P0/P1/P2，confidence 必须 ∈ [0,1]。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Severity = Literal["P0", "P1", "P2"]
Verdict = Literal["GO", "CAUTION", "NO_GO"]
Platform = Literal["shopee", "mercadolivre", "amazon", "tiktok", "shopify"]


class StrictModel(BaseModel):
    """输出侧基类：禁止未声明字段。"""

    model_config = ConfigDict(extra="forbid")


class InputModel(BaseModel):
    """输入侧基类：允许扩展字段。"""

    model_config = ConfigDict(extra="allow")


# --------------------------------------------------------------------------- #
# 01 市场分析
# --------------------------------------------------------------------------- #
class CompetitorRow(InputModel):
    title: str = ""
    price: float = Field(gt=0)
    monthly_sales: int = Field(default=0, ge=0)
    rating: float | None = Field(default=None, ge=0, le=5)
    reviews: int = Field(default=0, ge=0)
    config: dict[str, str] = Field(default_factory=dict)


class MarketInput(InputModel):
    product: str = Field(min_length=1)
    category: str = ""
    platform: Platform = "shopee"
    market: str = "BR"
    currency: str = "BRL"
    competitor_rows: list[CompetitorRow] = Field(
        min_length=3,
        description="竞品导出数据，少于 3 条无法计算集中度与价格带，直接拒绝",
    )


class PrecheckOut(StrictModel):
    verdict: Verdict
    score: int = Field(ge=0, le=100)
    reasons: list[str]


class MarketMetricsOut(StrictModel):
    competitor_count: int
    cr4: float = Field(ge=0, le=1)
    hhi: float = Field(ge=0, le=10000)
    avg_price: float
    gmv_estimate: float
    demand_index: float = Field(ge=0, le=1)


class PriceBandOut(StrictModel):
    p25: float
    median: float
    p75: float
    valley_low: float
    valley_high: float
    recommended_low: float
    recommended_high: float
    rationale: str
    valley_efficiency: float = 0.0


class PriceBucket(StrictModel):
    """价格带分桶（供前端画供给/需求分布，数据来自 market_metrics，模型只做搬运）。"""

    low: float
    high: float
    sku: int
    sales: int
    supply_index: float
    demand_index: float
    efficiency: float


class ConfigRow(StrictModel):
    combo: str
    share: float
    avg_price: float
    sales: int


class MarketOutput(StrictModel):
    precheck: PrecheckOut
    metrics: MarketMetricsOut
    price_band: PriceBandOut
    price_buckets: list[PriceBucket] = Field(default_factory=list)
    config_matrix: list[ConfigRow]
    opportunity_points: list[str] = Field(min_length=1)
    recommended_config: str
    risks: list[str]
    confidence: float = Field(ge=0, le=1)


# --------------------------------------------------------------------------- #
# 02 产品策略
# --------------------------------------------------------------------------- #
class Economics(InputModel):
    """单位经济模型：成本、费率、目标净利率。缺一项就算不出盈亏线。"""

    cogs: float = Field(gt=0, description="采购成本")
    freight: float = Field(default=0, ge=0, description="头程+尾程物流")
    platform_fee_rate: float = Field(default=0.14, ge=0, le=0.5)
    payment_fee_rate: float = Field(default=0.02, ge=0, le=0.1)
    ad_rate: float = Field(default=0.10, ge=0, le=0.5)
    target_margin: float = Field(default=0.15, ge=0, le=0.6)
    coupon_budget: float = Field(default=0.05, ge=0, le=0.3, description="券/活动折算比例")
    currency: str = "BRL"


class StrategyInput(InputModel):
    product: str
    market_analysis: dict[str, Any] = Field(default_factory=dict)
    economics: Economics
    config_options: list[str] = Field(default_factory=list)


class PricingOut(StrictModel):
    breakeven_price: float
    target_price: float
    net_margin_at_target: float
    coupon_headroom: float
    allowed_discount_rate: float
    formula_version: str = "v2"


class SkuRole(StrictModel):
    role: Literal["引流款", "主推款", "利润款", "形象款"]
    name: str
    price: float
    rationale: str


class StrategyOutput(StrictModel):
    positioning: str
    main_selling_point: str = Field(description="唯一的、可被主图一行字承载的卖点")
    secondary_points: list[str] = Field(min_length=1, max_length=4)
    config_ladder: list[dict[str, Any]]
    pricing: PricingOut
    sku_roles: list[SkuRole] = Field(min_length=4, max_length=4)
    risks: list[str]
    confidence: float = Field(ge=0, le=1)


# --------------------------------------------------------------------------- #
# 03 Listing 创意
# --------------------------------------------------------------------------- #
class ListingInput(InputModel):
    product: str
    strategy: dict[str, Any] = Field(default_factory=dict)
    keywords: list[str] = Field(default_factory=list)
    specs: dict[str, str] = Field(default_factory=dict)
    platform: Platform = "shopee"
    language: str = "pt-BR"


class TitleOut(StrictModel):
    text: str
    chars: int
    limit: int
    head30: str
    checks: list[dict[str, Any]]


class KeywordMap(StrictModel):
    primary: list[str]
    long_tail: list[str]
    negative: list[str]


class SpecOut(StrictModel):
    name: str
    value: str
    source: Literal["fact", "derived"] = "fact"


class ComplianceOut(StrictModel):
    passed: bool
    blocked_terms: list[str]
    notes: list[str]


class ListingOutput(StrictModel):
    keyword_map: KeywordMap
    title: TitleOut
    bullets: list[str] = Field(min_length=3, max_length=6)
    description: dict[str, Any]
    specs: list[SpecOut]
    compliance: ComplianceOut
    self_check: list[dict[str, Any]]
    confidence: float = Field(ge=0, le=1)


# --------------------------------------------------------------------------- #
# 04 图片
# --------------------------------------------------------------------------- #
class ImageInput(InputModel):
    product: str
    strategy: dict[str, Any] = Field(default_factory=dict)
    platform: Platform = "shopee"
    image_path: str | None = Field(default=None, description="可选：本地文件做像素级合规校验")


class GridSlot(StrictModel):
    slot: int = Field(ge=1, le=9)
    purpose: str
    scene: str
    overlay_text: str
    prompt: str
    risk: str = ""


class ImageOutput(StrictModel):
    cover_spec: dict[str, Any]
    nine_grid: list[GridSlot] = Field(min_length=9, max_length=9)
    compliance: ComplianceOut
    self_check: list[dict[str, Any]]
    confidence: float = Field(ge=0, le=1)


# --------------------------------------------------------------------------- #
# 05 视频
# --------------------------------------------------------------------------- #
class VideoInput(InputModel):
    product: str
    strategy: dict[str, Any] = Field(default_factory=dict)
    platform: Platform = "shopee"
    target_duration: int = Field(default=20, ge=9, le=90)


class Shot(StrictModel):
    t_start: float = Field(ge=0)
    t_end: float = Field(gt=0)
    visual: str
    voiceover: str
    caption: str


class VideoOutput(StrictModel):
    hook: str
    shots: list[Shot] = Field(min_length=3, max_length=10)
    narration: str
    captions: list[str]
    timing_check: dict[str, Any]
    self_check: list[dict[str, Any]]
    confidence: float = Field(ge=0, le=1)


# --------------------------------------------------------------------------- #
# 06 广告优化
# --------------------------------------------------------------------------- #
class AdRow(InputModel):
    campaign: str = ""
    impressions: int = Field(ge=0)
    clicks: int = Field(ge=0)
    orders: int = Field(ge=0)
    spend: float = Field(ge=0)
    revenue: float = Field(ge=0)


class AdsInput(InputModel):
    rows: list[AdRow] = Field(min_length=1)
    economics: Economics
    window_days: int = Field(default=7, ge=1, le=90)


class FunnelOut(StrictModel):
    impressions: int
    clicks: int
    orders: int
    spend: float
    revenue: float
    ctr: float
    cvr: float
    cpc: float
    cpa: float
    roas: float
    aov: float


class BreakevenOut(StrictModel):
    aov: float
    contribution_margin: float
    cpa_max: float
    cpc_max: float
    roas_min: float
    current_roas_vs_target: float


class Diagnosis(StrictModel):
    layer: Literal["曝光", "点击", "转化", "客单", "履约"]
    symptom: str
    root_cause: str
    evidence: list[str] = Field(min_length=1)


class ActionItem(StrictModel):
    priority: Severity
    action: str
    expected_impact: str
    owner: str
    sla_hours: int = Field(ge=1, le=168)


class AdsOutput(StrictModel):
    funnel: FunnelOut
    breakeven: BreakevenOut
    diagnosis: list[Diagnosis] = Field(min_length=1)
    actions: list[ActionItem] = Field(min_length=1)
    budget_realloc: list[dict[str, Any]]
    confidence: float = Field(ge=0, le=1)


# --------------------------------------------------------------------------- #
# 07 评论分析
# --------------------------------------------------------------------------- #
class ReviewRow(InputModel):
    rating: int = Field(ge=1, le=5)
    text: str = ""
    variant: str = ""
    date: str = ""
    helpful: int = Field(default=0, ge=0)


class ReviewsInput(InputModel):
    reviews: list[ReviewRow] = Field(min_length=1)
    listing_title: str = ""


class AttributionOut(StrictModel):
    counts: dict[str, int]
    total: int
    negative_rate: float = Field(ge=0, le=1)
    fixable_rate: float = Field(ge=0, le=1)
    misunderstanding_rate: float = Field(ge=0, le=1)


class Patch(StrictModel):
    target: Literal["listing", "image", "video", "packaging", "logistics"]
    change: str
    reason: str
    evidence_count: int = Field(ge=1)


class ReviewOutput(StrictModel):
    attribution: AttributionOut
    top_praise: list[str]
    top_complaint: list[str]
    listing_patches: list[Patch]
    image_patches: list[Patch]
    confidence: float = Field(ge=0, le=1)


# --------------------------------------------------------------------------- #
# 注册表：agent_id -> (input_model, output_model)
# --------------------------------------------------------------------------- #
CONTRACTS: dict[str, tuple[type[BaseModel], type[BaseModel]]] = {
    "01_market_research": (MarketInput, MarketOutput),
    "02_product_strategy": (StrategyInput, StrategyOutput),
    "03_listing_creative": (ListingInput, ListingOutput),
    "04_product_image": (ImageInput, ImageOutput),
    "05_product_video": (VideoInput, VideoOutput),
    "06_advertising": (AdsInput, AdsOutput),
    "07_review": (ReviewsInput, ReviewOutput),
}


class ContractError(ValueError):
    """输入契约不满足——宁可失败，不许编造。"""

    def __init__(self, agent_id: str, errors: list[dict[str, Any]]):
        self.agent_id = agent_id
        self.errors = errors
        detail = "; ".join(f"{'.'.join(str(x) for x in e['loc'])}: {e['msg']}" for e in errors[:5])
        super().__init__(f"[{agent_id}] 输入契约校验失败: {detail}")


def validate_input(agent_id: str, payload: dict[str, Any]) -> BaseModel:
    model = CONTRACTS[agent_id][0]
    try:
        return model.model_validate(payload)
    except Exception as exc:  # pydantic.ValidationError
        errors = exc.errors() if hasattr(exc, "errors") else [{"loc": (), "msg": str(exc)}]
        raise ContractError(agent_id, errors) from exc


def validate_output(agent_id: str, payload: dict[str, Any]) -> BaseModel:
    model = CONTRACTS[agent_id][1]
    try:
        return model.model_validate(payload)
    except Exception as exc:
        errors = exc.errors() if hasattr(exc, "errors") else [{"loc": (), "msg": str(exc)}]
        raise ContractError(agent_id, errors) from exc
