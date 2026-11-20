"""工作流测试：完整流水线、人工审批、闭环迭代、失败路径。"""

from __future__ import annotations

import time

from sqlalchemy import select

from app.db import session_scope
from app.models import Artifact, FeedbackEvent, Project, Run, StepRun
from app.seed import SCOOTER_BRIEF
from app.workflow.engine import engine


def _retry_project() -> Project:
    """复制一份干净项目，避免版本号互相影响。"""
    import uuid

    with session_scope() as db:
        project = Project(
            name=f"测试项目-{uuid.uuid4().hex[:6]}",
            category="Patinetes Elétricos",
            platform="shopee",
            brief=SCOOTER_BRIEF,
        )
        db.add(project)
        db.flush()
        db.expunge(project)
        return project


def _wait(run_id: str, timeout: float = 20.0) -> Run:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with session_scope() as db:
            run = db.get(Run, run_id)
            if run.status in {"completed", "failed", "awaiting_approval", "cancelled"}:
                db.expunge(run)
                return run
        time.sleep(0.05)
    raise AssertionError(f"运行 {run_id} 超时未结束")


def test_full_pipeline_pauses_for_approval() -> None:
    project = _retry_project()
    run_id = engine.create_run(project, "full_pipeline")
    engine.execute(run_id)
    run = _wait(run_id)

    assert run.status == "awaiting_approval"
    assert run.stage == "creative_ready"

    with session_scope() as db:
        steps = db.scalars(select(StepRun).where(StepRun.run_id == run_id).order_by(StepRun.seq)).all()
        assert [s.agent_id for s in steps] == [
            "01_market_research",
            "02_product_strategy",
            "03_listing_creative",
            "04_product_image",
            "05_product_video",
        ]
        assert all(s.status == "succeeded" for s in steps)
        assert all(s.checks for s in steps), "每个 Agent 都应产出 self-check"
        listing = db.scalars(select(Artifact).where(Artifact.run_id == run_id, Artifact.kind == "listing_pack")).first()
        assert listing is not None
        assert listing.content["title"]["chars"] <= listing.content["title"]["limit"]
        assert listing.content["compliance"]["passed"] is True


def test_approval_publishes_pack() -> None:
    project = _retry_project()
    run_id = engine.create_run(project, "full_pipeline")
    engine.execute(run_id)
    assert _wait(run_id).status == "awaiting_approval"

    result = engine.approve(run_id)
    assert result["ok"] is True

    with session_scope() as db:
        run = db.get(Run, run_id)
        assert run.status == "completed"
        assert run.stage == "published"
        pack = db.scalars(select(Artifact).where(Artifact.run_id == run_id, Artifact.kind == "publish_pack")).first()
        assert pack is not None
        assert pack.status == "approved"
        assert pack.content["listing"]["title"]["text"]
        assert len(pack.content["images"]["grid"]) == 9
        assert all(item["passed"] for item in pack.content["checklist"])


def test_contract_failure_marks_run_failed() -> None:
    with session_scope() as db:
        project = Project(name="缺数据项目", category="X", platform="shopee", brief={**SCOOTER_BRIEF, "competitor_rows": []})
        db.add(project)
        db.flush()
        db.expunge(project)

    run_id = engine.create_run(project, "full_pipeline")
    engine.execute(run_id)
    run = _wait(run_id)

    assert run.status == "failed"
    assert "competitor_rows" in (run.error or "") or "至少 3 条" in (run.error or "")


def test_closed_loop_produces_feedback_and_retest() -> None:
    project = _retry_project()

    # 先有 v1 素材，闭环才有"改前版本"可对照
    first = engine.create_run(project, "creative_only")
    engine.execute(first)
    assert _wait(first).status == "awaiting_approval"

    extra = {
        "ad_rows": SCOOTER_BRIEF["ad_rows"],
        "reviews": SCOOTER_BRIEF["reviews"],
        "ad_rows_after": SCOOTER_BRIEF["ad_rows_after"],
    }
    run_id = engine.create_run(project, "closed_loop", extra=extra)
    engine.execute(run_id)
    run = _wait(run_id)

    assert run.status == "completed"
    retest = (run.metrics or {}).get("retest")
    assert retest is not None
    assert retest["after"]["roas"] > retest["before"]["roas"]
    assert retest["verdict"] == "达标"

    with session_scope() as db:
        feedback = db.scalars(select(FeedbackEvent).where(FeedbackEvent.run_id == run_id)).all()
        assert feedback, "诊断必须产生回流事件"
        assert {f.target_agent for f in feedback} <= {"03_listing_creative", "04_product_image"}
        versions = db.scalars(
            select(Artifact).where(Artifact.project_id == project.id, Artifact.kind == "listing_pack").order_by(Artifact.version)
        ).all()
        assert len(versions) == 2, "闭环应产出 v2"
        assert versions[0].status == "superseded"
        assert versions[1].content.get("applied_feedback"), "v2 必须记录应用了哪些回流条目"


def test_diagnose_mode_is_read_only_for_creative() -> None:
    project = _retry_project()
    run_id = engine.create_run(
        project,
        "diagnose",
        extra={"ad_rows": SCOOTER_BRIEF["ad_rows"], "reviews": SCOOTER_BRIEF["reviews"]},
    )
    engine.execute(run_id)
    run = _wait(run_id)

    assert run.status == "completed"
    with session_scope() as db:
        kinds = {a.kind for a in db.scalars(select(Artifact).where(Artifact.run_id == run_id)).all()}
        assert kinds == {"ad_plan", "review_insight"}
