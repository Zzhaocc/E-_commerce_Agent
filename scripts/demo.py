"""端到端演示脚本：一条命令跑完整个故事。

    python scripts/demo.py

流程：市场 → 策略 → 创意三件套 → 人工审批 → 发布包 → 广告/评论诊断
      → 回流 → 素材 v2 → 第二窗口复测 → 回归评测
不需要 API key，不需要启动服务器。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.db import init_db, session_scope  # noqa: E402
from app.models import Artifact, FeedbackEvent, Project, Run, StepRun  # noqa: E402
from app.seed import DEMO_PROJECT_ID, seed_demo_projects  # noqa: E402
from app.tools.base import load_all_tools  # noqa: E402
from app.workflow.engine import engine  # noqa: E402

LINE = "─" * 74


def section(text: str) -> None:
    print(f"\n{LINE}\n{text}\n{LINE}")


def wait(run_id: str, timeout: float = 30.0) -> Run:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with session_scope() as db:
            run = db.get(Run, run_id)
            if run.status in {"completed", "failed", "awaiting_approval", "cancelled"}:
                db.expunge(run)
                return run
        time.sleep(0.1)
    raise TimeoutError(run_id)


def artifact(db, run_id: str, kind: str) -> Artifact | None:
    return db.scalars(select(Artifact).where(Artifact.run_id == run_id, Artifact.kind == kind)).first()


def main() -> int:
    init_db()
    load_all_tools()
    seed_demo_projects()

    with session_scope() as db:
        project = db.get(Project, DEMO_PROJECT_ID)
        brief = dict(project.brief)
        print(f"项目：{project.name}｜平台：{project.platform}｜市场：{project.market}")
        print(f"竞品样本：{len(brief['competitor_rows'])} 条｜评论：{len(brief['reviews'])} 条")

    section("① 跑完整流水线：01 → 02 → (03 ∥ 04 ∥ 05)")
    run_id = engine.create_run(project, "full_pipeline")
    started = time.time()
    engine.execute(run_id)
    run = wait(run_id)
    print(f"状态：{run.status}｜阶段：{run.stage}｜耗时 {(time.time() - started) * 1000:.0f} ms")

    with session_scope() as db:
        steps = db.scalars(select(StepRun).where(StepRun.run_id == run_id).order_by(StepRun.seq)).all()
        for step in steps:
            passed = sum(1 for c in (step.checks or []) if c.get("passed"))
            total = len(step.checks or [])
            flags = f"护栏 {len(step.guardrails)} 条" if step.guardrails else "无护栏事件"
            print(
                f"  {step.agent_id:<22} ✓ checks {passed}/{total}  tokens {step.tokens_in + step.tokens_out:<5} {flags}"
            )

        market = artifact(db, run_id, "market_report").content
        strategy = artifact(db, run_id, "product_strategy").content
        listing = artifact(db, run_id, "listing_pack").content

    print(f"\n  市场结论：{market['precheck']['verdict']}（{market['precheck']['score']} 分）")
    band = market["price_band"]
    print(f"  价格缝隙：R$ {band['recommended_low']:.0f} – {band['recommended_high']:.0f}")
    print(f"  唯一卖点：{strategy['main_selling_point']}")
    pricing = strategy["pricing"]
    print(
        f"  目标价：R$ {pricing['target_price']:.2f}｜盈亏价：R$ {pricing['breakeven_price']:.2f}"
        f"｜净利率：{pricing['net_margin_at_target']:.0%}"
    )
    print(f"  标题（{listing['title']['chars']}/{listing['title']['limit']} 字符）：{listing['title']['text']}")

    section("② 人工审批闸门")
    print("运行在 creative_ready 暂停，等待人工确认素材（合规与品牌风险由人兜底）")
    print("  approve →", engine.approve(run_id))
    with session_scope() as db:
        pack = artifact(db, run_id, "publish_pack")
        status = db.get(Run, run_id).status
    print(f"  发布包已生成：{bool(pack)}｜运行状态：{status}")
    if pack:
        for item in pack.content["checklist"]:
            mark = "✓" if item["passed"] else "✗"
            print(f"    {mark} {item['item']}")

    section("③ 闭环：06/07 诊断 → 回流 → 素材 v2 → 复测")
    loop_id = engine.create_run(
        project,
        "closed_loop",
        extra={
            "ad_rows": brief["ad_rows"],
            "reviews": brief["reviews"],
            "ad_rows_after": brief["ad_rows_after"],
        },
    )
    engine.execute(loop_id)
    loop = wait(loop_id)

    with session_scope() as db:
        feedback = db.scalars(select(FeedbackEvent).where(FeedbackEvent.run_id == loop_id)).all()
        for fb in feedback:
            print(f"  [{fb.severity}] {fb.source_agent} → {fb.target_agent}")
            print(f"        问题：{fb.issue[:52]}")
            print(f"        动作：{fb.action[:56]}")
        versions = db.scalars(
            select(Artifact)
            .where(Artifact.project_id == DEMO_PROJECT_ID, Artifact.kind == "listing_pack")
            .order_by(Artifact.version)
        ).all()

    retest = (loop.metrics or {}).get("retest") or {}
    if retest:
        before, after = retest["before"], retest["after"]
        print(
            f"\n  复测：ROAS {before['roas']:.2f} → {after['roas']:.2f}（Δ {retest['roas_delta']:+.2f}）"
            f"｜CPA {before['cpa']:.0f} → {after['cpa']:.0f}"
        )
        print(f"  结论：{retest['verdict']}")
    print(f"  Listing 版本：{[(v.version, v.status) for v in versions]}")

    section("④ 回归评测（含 2 条负向用例：必须失败）")
    from app.eval.runner import run_suite

    evaluation = run_suite()
    for item in evaluation["results"]:
        mark = "✓" if item["passed"] else "✗"
        print(f"  {mark} {item['case']:<42} {item['detail'][:48]}")
    print(f"\n  得分：{evaluation['passed']}/{evaluation['total']} = {evaluation['score']}")

    with session_scope() as db:
        all_steps = db.scalars(select(StepRun)).all()
        total_tokens = sum(s.tokens_in + s.tokens_out for s in all_steps)
        total_cost = sum(s.cost_usd for s in all_steps)
    print(f"  累计 token：{total_tokens:,}｜累计成本：${total_cost:.4f}（mock 计费口径，仅演示）")
    print("\n演示结束。启动 Web 控制台：python -m uvicorn app.main:app --port 8000")
    return 0 if evaluation["score"] >= 0.9 else 1


if __name__ == "__main__":
    raise SystemExit(main())
