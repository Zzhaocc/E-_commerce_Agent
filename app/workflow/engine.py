"""工作流引擎：把 7 个 Agent 串成可断点、可回流、可审批的流水线。

状态机
    queued → running → [awaiting_approval] → completed
                    ↘ failed / cancelled

四条执行路径
    full_pipeline  01 → 02 → (03 ∥ 04 ∥ 05) → 人工审批 → 发布包
    creative_only  02 → (03 ∥ 04 ∥ 05)          用于只改素材
    single         指定一个 Agent              用于接口联调与回放
    diagnose       06 → 07                      只做诊断，不产出素材
    closed_loop    06 → 07 → 回流 03/04（v2）→ 复测 06
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select

from app.agents.base import AgentContext
from app.agents.registry import AGENTS, get_agent
from app.agents.runtime import AgentRunError, AgentRuntime
from app.config import get_settings
from app.db import session_scope
from app.llm.router import get_provider
from app.models import Artifact, FeedbackEvent, Project, Run, StepRun, TraceSpan, new_id

_WRITE_LOCK = threading.Lock()  # SQLite 单写者：持久化串行化，Agent 执行仍并行

CREATIVE_CLUSTER = ["03_listing_creative", "04_product_image", "05_product_video"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


class RunEngine:
    def __init__(self) -> None:
        self.settings = get_settings()

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def create_run(self, project: Project, mode: str, agent_id: str | None = None, extra: dict | None = None) -> str:
        with session_scope() as db:
            run = Run(
                id=new_id("run"),
                project_id=project.id,
                mode=mode,
                status="queued",
                stage="queued",
                context={"agent_id": agent_id or "", "extra": extra or {}, "completed_steps": []},
                metrics={},
            )
            db.add(run)
            db.flush()
            return run.id

    def launch(self, run_id: str) -> None:
        thread = threading.Thread(target=self.execute, args=(run_id,), name=f"run-{run_id}", daemon=True)
        thread.start()

    # ------------------------------------------------------------------ #
    # 执行入口
    # ------------------------------------------------------------------ #
    def execute(self, run_id: str) -> None:
        started = time.perf_counter()
        try:
            with session_scope() as db:
                run = db.get(Run, run_id)
                if run is None:
                    return
                project = db.get(Project, run.project_id)
                run.status = "running"
                run.stage = "starting"
                mode = run.mode
                agent_id = (run.context or {}).get("agent_id")
                extra = (run.context or {}).get("extra") or {}
                project_dict = {
                    "id": project.id,
                    "name": project.name,
                    "category": project.category,
                    "platform": project.platform,
                    "market": project.market,
                    "language": project.language,
                }
                brief = dict(project.brief or {})
                if extra.get("ad_rows"):
                    brief["ad_rows"] = extra["ad_rows"]
                if extra.get("reviews"):
                    brief["reviews"] = extra["reviews"]
                if extra.get("ad_rows_after"):
                    brief["ad_rows_after"] = extra["ad_rows_after"]

            artifacts = self._load_artifacts(project.id)

            if mode == "single":
                self._run_single(run_id, project_dict, brief, artifacts, agent_id)
            elif mode == "diagnose":
                self._run_diagnose(run_id, project_dict, brief, artifacts)
            elif mode == "closed_loop":
                self._run_closed_loop(run_id, project_dict, brief, artifacts)
            elif mode == "creative_only":
                self._run_creative(run_id, project_dict, brief, artifacts)
            else:
                self._run_full(run_id, project_dict, brief, artifacts)

            self._finish(run_id, started)
        except AgentRunError as exc:
            self._fail(run_id, str(exc))
        except Exception as exc:  # noqa: BLE001
            self._fail(run_id, f"{type(exc).__name__}: {exc}")

    # ------------------------------------------------------------------ #
    # 四条路径
    # ------------------------------------------------------------------ #
    def _run_full(self, run_id: str, project: dict, brief: dict, artifacts: dict) -> None:
        seq = 1
        market = self._step(run_id, project, brief, artifacts, "01_market_research", seq)
        artifacts["market_report"] = market
        seq += 1
        strategy = self._step(run_id, project, brief, artifacts, "02_product_strategy", seq)
        artifacts["product_strategy"] = strategy
        seq += 1

        self._update_stage(run_id, "creative_cluster")
        self._run_cluster(run_id, project, brief, artifacts, start_seq=seq)

        if self.settings.human_approval:
            self._set_status(run_id, "awaiting_approval", "creative_ready")
        else:
            self.publish(run_id, project, artifacts)

    def _run_creative(self, run_id: str, project: dict, brief: dict, artifacts: dict) -> None:
        self._update_stage(run_id, "creative_cluster")
        self._run_cluster(run_id, project, brief, artifacts, start_seq=1)
        if self.settings.human_approval:
            self._set_status(run_id, "awaiting_approval", "creative_ready")
        else:
            self.publish(run_id, project, artifacts)

    def _run_single(self, run_id: str, project: dict, brief: dict, artifacts: dict, agent_id: str | None) -> None:
        if not agent_id:
            raise AgentRunError("engine", "single", "single 模式必须指定 agent_id")
        get_agent(agent_id)
        self._step(run_id, project, brief, artifacts, agent_id, 1)

    def _run_diagnose(self, run_id: str, project: dict, brief: dict, artifacts: dict) -> None:
        self._update_stage(run_id, "diagnose")
        ads = self._step(run_id, project, brief, artifacts, "06_advertising", 1)
        artifacts["ad_plan"] = ads
        review = self._step(run_id, project, brief, artifacts, "07_review", 2)
        artifacts["review_insight"] = review
        self._save_feedback(run_id, project["id"], ads, review)

    def _run_closed_loop(self, run_id: str, project: dict, brief: dict, artifacts: dict) -> None:
        """闭环：诊断 → 回流 → 改素材（v2）→ 复测。"""
        self._update_stage(run_id, "diagnose")
        ads = self._step(run_id, project, brief, artifacts, "06_advertising", 1)
        artifacts["ad_plan"] = ads
        review = self._step(run_id, project, brief, artifacts, "07_review", 2)
        artifacts["review_insight"] = review

        feedback = self._save_feedback(run_id, project["id"], ads, review)
        artifacts["feedback"] = feedback

        # 回流：带着诊断结论重写 Listing / 图片（版本 +1）
        self._update_stage(run_id, "iterate_creative")
        listing = self._step(run_id, project, brief, artifacts, "03_listing_creative", 3)
        artifacts["listing_pack"] = listing
        image = self._step(run_id, project, brief, artifacts, "04_product_image", 4)
        artifacts["image_pack"] = image

        # 复测：用第二窗口数据再跑一次诊断，形成"改前 vs 改后"
        if brief.get("ad_rows_after"):
            self._update_stage(run_id, "retest")
            after_brief = {**brief, "ad_rows": brief["ad_rows_after"]}
            after = self._step(run_id, project, after_brief, artifacts, "06_advertising", 5, kind="ad_plan_retest")
            before = (ads.get("funnel") or {})
            after_f = (after.get("funnel") or {})
            with session_scope() as db:
                run = db.get(Run, run_id)
                metrics = dict(run.metrics or {})
                metrics["retest"] = {
                    "before": {"roas": before.get("roas"), "cpa": before.get("cpa"), "cvr": before.get("cvr"), "ctr": before.get("ctr")},
                    "after": {"roas": after_f.get("roas"), "cpa": after_f.get("cpa"), "cvr": after_f.get("cvr"), "ctr": after_f.get("ctr")},
                    "roas_delta": round((after_f.get("roas") or 0) - (before.get("roas") or 0), 4),
                    "cpa_delta": round((after_f.get("cpa") or 0) - (before.get("cpa") or 0), 4),
                    "verdict": "达标" if (after_f.get("roas") or 0) >= (after.get("breakeven") or {}).get("roas_min", 0) else "未达标，回到诊断",
                }
                run.metrics = metrics

    # ------------------------------------------------------------------ #
    # 单步执行
    # ------------------------------------------------------------------ #
    def _step(
        self,
        run_id: str,
        project: dict,
        brief: dict,
        artifacts: dict,
        agent_id: str,
        seq: int,
        kind: str | None = None,
    ) -> dict[str, Any]:
        spec = get_agent(agent_id)
        artifact_kind = kind or spec.artifact_kind
        step_id = new_id("stp")

        with session_scope() as db:
            run = db.get(Run, run_id)
            run.stage = agent_id
            db.add(
                StepRun(
                    id=step_id,
                    run_id=run_id,
                    agent_id=agent_id,
                    seq=seq,
                    status="running",
                    input={},
                )
            )

        ctx = AgentContext(
            project=project,
            brief=brief,
            artifacts=artifacts,
            run_id=run_id,
            step_id=step_id,
        )

        runtime = AgentRuntime(get_provider())
        try:
            result = runtime.run(spec, ctx)
        except AgentRunError as exc:
            with session_scope() as db:
                step = db.get(StepRun, step_id)
                step.status = "failed"
                step.error = str(exc)
                step.input = {"agent_id": agent_id}
            raise

        version = self._next_version(project["id"], artifact_kind)
        output = result.output
        if agent_id in {"03_listing_creative", "04_product_image"} and "feedback" in artifacts and version > 1:
            output = self._annotate_iteration(output, artifacts["feedback"])

        with _WRITE_LOCK, session_scope() as db:
            step = db.get(StepRun, step_id)
            step.status = "succeeded"
            step.input = {"agent_id": agent_id, "artifact_version": version}
            step.output = output
            step.checks = result.checks
            step.guardrails = result.guardrails
            step.provider = runtime.provider.name
            step.model = runtime.provider.model
            step.tokens_in = result.usage.prompt_tokens
            step.tokens_out = result.usage.completion_tokens
            step.cost_usd = round(
                result.usage.prompt_tokens / 1000 * self.settings.cost_per_1k_in
                + result.usage.completion_tokens / 1000 * self.settings.cost_per_1k_out,
                6,
            )
            step.latency_ms = result.latency_ms
            step.attempts = result.attempts

            project_obj = db.get(Project, project["id"])
            if version > 1:
                prev = db.scalars(
                    select(Artifact).where(
                        Artifact.project_id == project["id"],
                        Artifact.kind == artifact_kind,
                        Artifact.version == version - 1,
                    )
                ).all()
                for old in prev:
                    old.status = "superseded"

            db.add(
                Artifact(
                    id=new_id("art"),
                    project_id=project["id"],
                    run_id=run_id,
                    agent_id=agent_id,
                    kind=artifact_kind,
                    version=version,
                    status="draft",
                    content=output,
                )
            )
            for span in result.spans:
                db.add(
                    TraceSpan(
                        id=new_id("spn"),
                        run_id=run_id,
                        step_run_id=step_id,
                        agent_id=agent_id,
                        name=span["name"],
                        kind=span["kind"],
                        status=span["status"],
                        latency_ms=span["latency_ms"],
                        tokens=span["tokens"],
                        cost_usd=span["cost_usd"],
                        attrs=span.get("attrs") or {},
                    )
                )
            _ = project_obj

        self._bump_step_counter(run_id, agent_id)
        return output

    def _run_cluster(self, run_id: str, project: dict, brief: dict, artifacts: dict, start_seq: int) -> None:
        """03/04/05 并行：三者都只依赖 02 的产物，互不阻塞。"""

        def work(agent_id: str) -> tuple[str, dict[str, Any]]:
            return agent_id, self._step(run_id, project, brief, artifacts, agent_id, start_seq + CREATIVE_CLUSTER.index(agent_id))

        with ThreadPoolExecutor(max_workers=3, thread_name_prefix="creative") as pool:
            for agent_id, output in pool.map(work, CREATIVE_CLUSTER):
                kind = get_agent(agent_id).artifact_kind
                artifacts[kind] = output

    def _annotate_iteration(self, output: dict[str, Any], feedback: dict[str, Any]) -> dict[str, Any]:
        """把回流条目写进产物，保证"改了哪一条、因为哪条证据"可追溯。"""
        applied = []
        for item in feedback.get("items", []):
            if item.get("target_agent") != "03_listing_creative" and item.get("target_agent") != "04_product_image":
                continue
            applied.append(
                {
                    "feedback_id": item.get("id"),
                    "source": item.get("source_agent"),
                    "issue": item.get("issue"),
                    "action": item.get("action"),
                    "severity": item.get("severity"),
                }
            )
        if applied:
            output["applied_feedback"] = applied
            output["revision_note"] = f"基于 {len(applied)} 条诊断回流重写（v2）"
        return output

    # ------------------------------------------------------------------ #
    # 审批与发布
    # ------------------------------------------------------------------ #
    def publish(self, run_id: str, project: dict | None = None, artifacts: dict | None = None) -> None:
        with session_scope() as db:
            run = db.get(Run, run_id)
            if project is None:
                proj = db.get(Project, run.project_id)
                project = {"id": proj.id, "name": proj.name, "platform": proj.platform}
        artifacts = artifacts or self._load_artifacts(project["id"])

        listing = artifacts.get("listing_pack") or {}
        image = artifacts.get("image_pack") or {}
        video = artifacts.get("video_pack") or {}
        strategy = artifacts.get("product_strategy") or {}
        market = artifacts.get("market_report") or {}

        if not listing.get("title"):
            self._fail(run_id, "发布包缺少 Listing：创意簇尚未成功产出")
            return
        if not (listing.get("compliance") or {}).get("passed", True):
            self._fail(run_id, "合规护栏未通过，禁止生成发布包")
            return

        pack = {
            "project": project,
            "market": {"verdict": (market.get("precheck") or {}).get("verdict"), "price_band": market.get("price_band")},
            "strategy": {"main_selling_point": strategy.get("main_selling_point"), "pricing": strategy.get("pricing")},
            "listing": listing,
            "images": {"cover_spec": image.get("cover_spec"), "grid": image.get("nine_grid"), "compliance": image.get("compliance")},
            "video": {"hook": video.get("hook"), "shots": video.get("shots"), "captions": video.get("captions")},
            "checklist": self._publish_checklist(listing, image, video),
        }

        with _WRITE_LOCK, session_scope() as db:
            version = self._next_version(project["id"], "publish_pack")
            db.add(
                Artifact(
                    id=new_id("art"),
                    project_id=project["id"],
                    run_id=run_id,
                    agent_id="publisher",
                    kind="publish_pack",
                    version=version,
                    status="approved",
                    content=pack,
                )
            )
            for kind in ("listing_pack", "image_pack", "video_pack"):
                row = db.scalars(
                    select(Artifact).where(
                        Artifact.project_id == project["id"], Artifact.kind == kind
                    ).order_by(Artifact.version.desc())
                ).first()
                if row:
                    row.status = "approved"
            run = db.get(Run, run_id)
            run.status = "completed"
            run.stage = "published"
            run.finished_at = _now()

    def approve(self, run_id: str) -> dict[str, Any]:
        with session_scope() as db:
            run = db.get(Run, run_id)
            if run is None:
                raise KeyError(run_id)
            if run.status != "awaiting_approval":
                return {"ok": False, "reason": f"当前状态 {run.status} 不需要审批"}
            project = db.get(Project, run.project_id)
            project_dict = {"id": project.id, "name": project.name, "platform": project.platform}
        self.publish(run_id, project_dict)
        self._summarize(run_id)
        return {"ok": True, "run_id": run_id}

    def reject(self, run_id: str, reason: str) -> None:
        with session_scope() as db:
            run = db.get(Run, run_id)
            if run is None:
                return
            run.status = "cancelled"
            run.stage = "rejected"
            run.error = f"人工驳回：{reason}"
            run.finished_at = _now()

    # ------------------------------------------------------------------ #
    # 回流事件
    # ------------------------------------------------------------------ #
    def _save_feedback(self, run_id: str, project_id: str, ads: dict, review: dict) -> dict[str, Any]:
        items: list[dict[str, Any]] = []

        for action in (ads.get("actions") or []):
            if action.get("priority") != "P0":
                continue
            text = action.get("action", "")
            # 只有"改素材"类动作才回流给创意 Agent；调价/预算类动作留在广告计划里执行
            if any(k in text for k in ("主图", "图片", "详情页", "标题", "视频")):
                target = "04_product_image" if ("主图" in text or "图片" in text) and "详情页" not in text else "03_listing_creative"
            else:
                continue
            issue = (ads.get("diagnosis") or [{}])[0].get("symptom", "广告漏斗异常")
            items.append(
                {
                    "source_agent": "06_advertising",
                    "target_agent": target,
                    "severity": action["priority"],
                    "issue": issue,
                    "action": text,
                    "evidence": {"expected_impact": action.get("expected_impact")},
                }
            )

        for patch in (review.get("listing_patches") or [])[:3]:
            items.append(
                {
                    "source_agent": "07_review",
                    "target_agent": "03_listing_creative",
                    "severity": "P1",
                    "issue": f"评论归因：{patch.get('reason', '')}",
                    "action": patch.get("change", ""),
                    "evidence": {"target": patch.get("target"), "evidence_count": patch.get("evidence_count")},
                }
            )
        for patch in (review.get("image_patches") or [])[:2]:
            items.append(
                {
                    "source_agent": "07_review",
                    "target_agent": "04_product_image",
                    "severity": "P1",
                    "issue": f"图片需修补：{patch.get('reason', '')}",
                    "action": patch.get("change", ""),
                    "evidence": {"target": patch.get("target"), "evidence_count": patch.get("evidence_count")},
                }
            )

        with _WRITE_LOCK, session_scope() as db:
            for item in items:
                row = FeedbackEvent(
                    id=new_id("fbk"),
                    run_id=run_id,
                    project_id=project_id,
                    source_agent=item["source_agent"],
                    target_agent=item["target_agent"],
                    severity=item["severity"],
                    issue=item["issue"],
                    action=item["action"],
                    evidence=item.get("evidence") or {},
                    applied=False,
                )
                db.add(row)
                db.flush()
                item["id"] = row.id

        return {"items": items, "count": len(items)}

    # ------------------------------------------------------------------ #
    # 持久化辅助
    # ------------------------------------------------------------------ #
    def _load_artifacts(self, project_id: str) -> dict[str, Any]:
        with session_scope() as db:
            rows = db.scalars(
                select(Artifact).where(Artifact.project_id == project_id).order_by(Artifact.version.asc())
            ).all()
            latest: dict[str, Any] = {}
            for row in rows:
                latest[row.kind] = row.content
            return latest

    def _next_version(self, project_id: str, kind: str) -> int:
        with session_scope() as db:
            current = db.scalar(select(func.max(Artifact.version)).where(Artifact.project_id == project_id, Artifact.kind == kind))
            return int(current or 0) + 1

    def _set_status(self, run_id: str, status: str, stage: str) -> None:
        with session_scope() as db:
            run = db.get(Run, run_id)
            run.status = status
            run.stage = stage

    def _update_stage(self, run_id: str, stage: str) -> None:
        with session_scope() as db:
            run = db.get(Run, run_id)
            run.stage = stage

    def _bump_step_counter(self, run_id: str, agent_id: str) -> None:
        with session_scope() as db:
            run = db.get(Run, run_id)
            ctx = dict(run.context or {})
            done = list(ctx.get("completed_steps") or [])
            done.append(agent_id)
            ctx["completed_steps"] = done
            run.context = ctx

    def _summarize(self, run_id: str) -> None:
        with session_scope() as db:
            steps = db.scalars(select(StepRun).where(StepRun.run_id == run_id)).all()
            tokens = sum(s.tokens_in + s.tokens_out for s in steps)
            cost = round(sum(s.cost_usd for s in steps), 6)
            checks = [c for s in steps for c in (s.checks or [])]
            guardrails = [g for s in steps for g in (s.guardrails or [])]
            run = db.get(Run, run_id)
            metrics = dict(run.metrics or {})
            metrics.update(
                {
                    "steps": len(steps),
                    "tokens": tokens,
                    "cost_usd": cost,
                    "latency_ms": sum(s.latency_ms for s in steps),
                    "checks_total": len(checks),
                    "checks_passed": sum(1 for c in checks if c.get("passed")),
                    "guardrails": {
                        "block": sum(1 for g in guardrails if g.get("severity") == "block"),
                        "repair": sum(1 for g in guardrails if g.get("severity") == "repair"),
                        "warn": sum(1 for g in guardrails if g.get("severity") == "warn"),
                    },
                    "per_agent": [
                        {
                            "agent_id": s.agent_id,
                            "tokens": s.tokens_in + s.tokens_out,
                            "cost_usd": s.cost_usd,
                            "latency_ms": s.latency_ms,
                            "attempts": s.attempts,
                        }
                        for s in steps
                    ],
                }
            )
            run.metrics = metrics

    def _publish_checklist(self, listing: dict, image: dict, video: dict) -> list[dict[str, Any]]:
        items = []

        def add(item: str, passed: bool, detail: str = "") -> None:
            items.append({"item": item, "passed": passed, "detail": detail})

        add("标题字符在平台上限内", (listing.get("title") or {}).get("chars", 0) <= (listing.get("title") or {}).get("limit", 999),
            f"{(listing.get('title') or {}).get('chars')}/{(listing.get('title') or {}).get('limit')}")
        add("无禁用词", not (listing.get("compliance") or {}).get("blocked_terms"), str((listing.get("compliance") or {}).get("blocked_terms")))
        add("9 图分工完整", len((image.get("nine_grid") or [])) == 9, f"{len(image.get('nine_grid') or [])} 张")
        add("视频时长达标", bool((video.get("timing_check") or {}).get("within_target")), str((video.get("timing_check") or {}).get("total_duration")))
        add("主卖点唯一", bool(listing.get("title") and listing.get("bullets")), "标题与五点均围绕同一卖点")
        return items

    def _finish(self, run_id: str, started: float) -> None:
        with session_scope() as db:
            run = db.get(Run, run_id)
            if run is None:
                return
            if run.status == "running":
                run.status = "completed"
                run.stage = "done"
                run.finished_at = _now()
        self._summarize(run_id)
        with session_scope() as db:
            run = db.get(Run, run_id)
            metrics = dict(run.metrics or {})
            metrics["wall_ms"] = int((time.perf_counter() - started) * 1000)
            run.metrics = metrics

    def _fail(self, run_id: str, message: str) -> None:
        with session_scope() as db:
            run = db.get(Run, run_id)
            if run is None:
                return
            run.status = "failed"
            run.error = message
            run.finished_at = _now()
        self._summarize(run_id)


engine = RunEngine()


def agent_catalog() -> list[dict[str, Any]]:
    return [
        {
            "id": a.id,
            "name": a.name,
            "cn_name": a.cn_name,
            "role": a.role,
            "artifact_kind": a.artifact_kind,
            "tools": a.tools,
        }
        for a in AGENTS
    ]
