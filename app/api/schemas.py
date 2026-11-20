"""API 请求 / 响应模型。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

RunMode = Literal["full_pipeline", "creative_only", "single", "diagnose", "closed_loop"]


class ProjectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    category: str = ""
    platform: Literal["shopee", "mercadolivre", "amazon", "tiktok", "shopify"] = "shopee"
    market: str = "BR"
    language: str = "pt-BR"
    brief: dict[str, Any] = Field(default_factory=dict, description="商品事实、成本、竞品、广告与评论数据")


class RunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: RunMode = "full_pipeline"
    agent_id: str | None = Field(default=None, description="mode=single 时必填")
    ad_rows: list[dict[str, Any]] | None = None
    reviews: list[dict[str, Any]] | None = None


class RejectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(default="", max_length=500)


class IterateRequest(BaseModel):
    """闭环输入：诊断窗口的广告与评论数据，可选第二窗口复测数据。"""

    model_config = ConfigDict(extra="forbid")

    ad_rows: list[dict[str, Any]] = Field(min_length=1)
    reviews: list[dict[str, Any]] = Field(default_factory=list)
    ad_rows_after: list[dict[str, Any]] = Field(default_factory=list)


class PreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    brief: dict[str, Any] = Field(default_factory=dict)
    platform: str = "shopee"
    market: str = "BR"
    language: str = "pt-BR"
    artifacts: dict[str, Any] = Field(default_factory=dict, description="上游产物，跑 03/04/05 时必填")
