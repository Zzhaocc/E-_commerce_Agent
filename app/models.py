"""持久化模型。

设计要点（面试可讲）：
1. Run / StepRun 分离 —— 一次运行有多个 Agent 步骤，步骤级记录才能做 trace、
   断点续跑与成本归因；
2. Artifact 带 version 与 status —— 闭环迭代不是"覆盖"，而是新版本 supersede 旧版本，
   任何一次投放的素材都可回溯；
3. TraceSpan 独立成表 —— LLM 调用、工具调用、护栏检查统一进同一条时间线。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Project(Base):
    """一个商品项目：商品事实 + 目标平台，是所有 Agent 的输入锚点。"""

    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("prj"))
    name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(120), default="")
    platform: Mapped[str] = mapped_column(String(40), default="shopee")
    market: Mapped[str] = mapped_column(String(40), default="BR")
    language: Mapped[str] = mapped_column(String(20), default="pt-BR")
    brief: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    runs: Mapped[list["Run"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    artifacts: Mapped[list["Artifact"]] = relationship(back_populates="project", cascade="all, delete-orphan")


class Run(Base):
    """一次工作流运行。status 是状态机的唯一真相来源。"""

    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("run"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    mode: Mapped[str] = mapped_column(String(30), default="full_pipeline")
    status: Mapped[str] = mapped_column(String(30), default="queued")  # queued|running|awaiting_approval|completed|failed|cancelled
    stage: Mapped[str] = mapped_column(String(60), default="")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    context: Mapped[dict] = mapped_column(JSON, default=dict)  # 断点续跑用：已完成步骤产物
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)  # tokens / cost / 步骤耗时
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    project: Mapped["Project"] = relationship(back_populates="runs")
    steps: Mapped[list["StepRun"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="StepRun.seq"
    )


class StepRun(Base):
    """单个 Agent 步骤执行记录，含自检结果与模型用量。"""

    __tablename__ = "step_runs"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("stp"))
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    agent_id: Mapped[str] = mapped_column(String(60))
    seq: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(30), default="pending")  # running|succeeded|failed|skipped
    input: Mapped[dict] = mapped_column(JSON, default=dict)
    output: Mapped[dict] = mapped_column(JSON, default=dict)
    checks: Mapped[list] = mapped_column(JSON, default=list)  # 自检清单，逐条 pass/fail
    guardrails: Mapped[list] = mapped_column(JSON, default=list)  # 护栏事件
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider: Mapped[str] = mapped_column(String(40), default="mock")
    model: Mapped[str] = mapped_column(String(80), default="")
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    run: Mapped["Run"] = relationship(back_populates="steps")


class Artifact(Base):
    """可交付产物：市场报告 / 策略 / Listing / 图片包 / 视频包 / 诊断报告。"""

    __tablename__ = "artifacts"
    __table_args__ = (Index("ix_artifact_project_kind", "project_id", "kind"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("art"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    run_id: Mapped[str] = mapped_column(String(40), index=True)
    agent_id: Mapped[str] = mapped_column(String(60))
    kind: Mapped[str] = mapped_column(String(40), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft|approved|rejected|superseded
    content: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    project: Mapped["Project"] = relationship(back_populates="artifacts")


class TraceSpan(Base):
    """统一 trace：llm / tool / guardrail / step 四类 span 落在同一时间线。"""

    __tablename__ = "trace_spans"
    __table_args__ = (Index("ix_span_run", "run_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("spn"))
    run_id: Mapped[str] = mapped_column(String(40), index=True)
    step_run_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    agent_id: Mapped[str] = mapped_column(String(60), default="")
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(20), default="llm")
    status: Mapped[str] = mapped_column(String(20), default="ok")
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    attrs: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class FeedbackEvent(Base):
    """回流事件：06/07 的结论回流到 03/04，驱动版本迭代。闭环的"证据"。"""

    __tablename__ = "feedback_events"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("fbk"))
    run_id: Mapped[str] = mapped_column(String(40), index=True)
    project_id: Mapped[str] = mapped_column(String(40), index=True)
    source_agent: Mapped[str] = mapped_column(String(60))
    target_agent: Mapped[str] = mapped_column(String(60))
    severity: Mapped[str] = mapped_column(String(10), default="P1")  # P0|P1|P2
    issue: Mapped[str] = mapped_column(Text)
    action: Mapped[str] = mapped_column(Text)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    applied: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class EvalRun(Base):
    """离线评测结果：回归门禁，防止改 Prompt 把通过率改坏。"""

    __tablename__ = "eval_runs"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("evl"))
    suite: Mapped[str] = mapped_column(String(60), default="golden")
    total: Mapped[int] = mapped_column(Integer, default=0)
    passed: Mapped[int] = mapped_column(Integer, default=0)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    results: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
