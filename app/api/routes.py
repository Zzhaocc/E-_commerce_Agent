"""REST 接口：项目 / 运行 / 产物 / 工具 / 评测 / 指标。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.agents.base import AgentContext
from app.agents.contracts import CONTRACTS
from app.agents.registry import AGENTS, get_agent
from app.agents.runtime import AgentRunError, AgentRuntime
from app.api.schemas import IterateRequest, PreviewRequest, ProjectCreate, RejectRequest, RunCreate
from app.config import get_settings
from app.db import get_db
from app.llm.router import get_provider
from app.models import Artifact, EvalRun, FeedbackEvent, Project, Run, StepRun, TraceSpan
from app.tools.base import ToolError, get_tool, invoke_tool, list_tools
from app.workflow.engine import agent_catalog, engine

router = APIRouter(prefix="/api")


def _dt(value: datetime | None) -> str | None:
    return value.isoformat() + "Z" if value else None


def _project_out(p: Project) -> dict[str, Any]:
    return {
        "id": p.id,
        "name": p.name,
        "category": p.category,
        "platform": p.platform,
        "market": p.market,
        "language": p.language,
        "brief": p.brief,
        "created_at": _dt(p.created_at),
    }


def _run_out(run: Run) -> dict[str, Any]:
    return {
        "id": run.id,
        "project_id": run.project_id,
        "mode": run.mode,
        "status": run.status,
        "stage": run.stage,
        "error": run.error,
        "context": run.context,
        "metrics": run.metrics,
        "created_at": _dt(run.created_at),
        "finished_at": _dt(run.finished_at),
    }


# --------------------------------------------------------------------------- #
# 元信息
# --------------------------------------------------------------------------- #
@router.get("/health", tags=["meta"])
def health() -> dict[str, Any]:
    s = get_settings()
    return {
        "status": "ok",
        "version": s.version,
        "provider": s.llm_provider,
        "model": s.llm_model,
        "human_approval": s.human_approval,
        "database": "sqlite" if s.sqlite else "external",
    }


@router.get("/meta/catalog", tags=["meta"])
def catalog() -> dict[str, Any]:
    s = get_settings()
    return {
        "agents": agent_catalog(),
        "pipeline": {
            "full": ["01_market_research", "02_product_strategy", "03_listing_creative", "04_product_image", "05_product_video"],
            "parallel_cluster": ["03_listing_creative", "04_product_image", "05_product_video"],
            "feedback": {"06_advertising": ["03_listing_creative", "04_product_image"], "07_review": ["03_listing_creative", "04_product_image"]},
        },
        "tools": [
            {"name": t.name, "description": t.description, "tags": list(t.tags), "schema": t.schema()["function"]["parameters"]}
            for t in list_tools()
        ],
        "platforms": ["shopee", "mercadolivre", "amazon", "tiktok", "shopify"],
        "runtime": {
            "provider": s.llm_provider,
            "model": s.llm_model,
            "max_tool_steps": s.max_tool_steps,
            "max_attempts": s.max_attempts,
        },
    }


@router.get("/contracts/{agent_id}", tags=["meta"])
def contract(agent_id: str) -> dict[str, Any]:
    if agent_id not in CONTRACTS:
        raise HTTPException(404, f"未知 Agent：{agent_id}")
    in_model, out_model = CONTRACTS[agent_id]
    return {
        "agent_id": agent_id,
        "input_schema": in_model.model_json_schema(),
        "output_schema": out_model.model_json_schema(),
    }


# --------------------------------------------------------------------------- #
# 项目
# --------------------------------------------------------------------------- #
@router.post("/projects", status_code=201, tags=["projects"])
def create_project(body: ProjectCreate, db: Session = Depends(get_db)) -> dict[str, Any]:
    project = Project(
        name=body.name,
        category=body.category,
        platform=body.platform,
        market=body.market,
        language=body.language,
        brief=body.brief,
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return _project_out(project)


@router.get("/projects", tags=["projects"])
def list_projects(limit: int = Query(50, le=200), offset: int = 0, db: Session = Depends(get_db)) -> dict[str, Any]:
    rows = db.scalars(select(Project).order_by(desc(Project.created_at)).limit(limit).offset(offset)).all()
    total = db.scalar(select(func.count()).select_from(Project)) or 0
    counts = dict(
        db.execute(
            select(Run.project_id, func.count(Run.id)).group_by(Run.project_id)
        ).all()
    )
    return {"items": [{**_project_out(p), "run_count": counts.get(p.id, 0)} for p in rows], "total": total}


@router.get("/projects/{project_id}", tags=["projects"])
def get_project(project_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(404, "项目不存在")
    runs = db.scalars(select(Run).where(Run.project_id == project_id).order_by(desc(Run.created_at)).limit(20)).all()
    artifacts = db.scalars(
        select(Artifact).where(Artifact.project_id == project_id).order_by(desc(Artifact.created_at)).limit(50)
    ).all()
    return {
        **_project_out(project),
        "runs": [_run_out(r) for r in runs],
        "artifacts": [
            {"id": a.id, "kind": a.kind, "version": a.version, "status": a.status, "run_id": a.run_id, "created_at": _dt(a.created_at)}
            for a in artifacts
        ],
    }


@router.delete("/projects/{project_id}", status_code=204, tags=["projects"])
def delete_project(project_id: str, db: Session = Depends(get_db)) -> None:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(404, "项目不存在")
    db.delete(project)
    db.commit()


# --------------------------------------------------------------------------- #
# 运行
# --------------------------------------------------------------------------- #
@router.post("/projects/{project_id}/runs", status_code=202, tags=["runs"])
def create_run(project_id: str, body: RunCreate, db: Session = Depends(get_db)) -> dict[str, Any]:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(404, "项目不存在")
    if body.mode == "single" and not body.agent_id:
        raise HTTPException(422, "mode=single 必须提供 agent_id")
    extra: dict[str, Any] = {}
    if body.ad_rows:
        extra["ad_rows"] = body.ad_rows
    if body.reviews:
        extra["reviews"] = body.reviews
    run_id = engine.create_run(project, body.mode, body.agent_id, extra=extra)
    engine.launch(run_id)
    return {"run_id": run_id, "status": "queued", "poll": f"/api/runs/{run_id}"}


@router.get("/runs", tags=["runs"])
def list_runs(
    project_id: str | None = None,
    status: str | None = None,
    limit: int = Query(50, le=200),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    stmt = select(Run).order_by(desc(Run.created_at)).limit(limit)
    if project_id:
        stmt = stmt.where(Run.project_id == project_id)
    if status:
        stmt = stmt.where(Run.status == status)
    rows = db.scalars(stmt).all()
    return {"items": [_run_out(r) for r in rows]}


@router.get("/runs/{run_id}", tags=["runs"])
def get_run(run_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    run = db.get(Run, run_id)
    if run is None:
        raise HTTPException(404, "运行不存在")
    steps = db.scalars(select(StepRun).where(StepRun.run_id == run_id).order_by(StepRun.seq)).all()
    artifacts = db.scalars(select(Artifact).where(Artifact.run_id == run_id).order_by(Artifact.created_at)).all()
    return {
        **_run_out(run),
        "steps": [
            {
                "id": s.id,
                "agent_id": s.agent_id,
                "seq": s.seq,
                "status": s.status,
                "error": s.error,
                "provider": s.provider,
                "model": s.model,
                "tokens_in": s.tokens_in,
                "tokens_out": s.tokens_out,
                "cost_usd": s.cost_usd,
                "latency_ms": s.latency_ms,
                "attempts": s.attempts,
                "checks_total": len(s.checks or []),
                "checks_passed": sum(1 for c in (s.checks or []) if c.get("passed")),
                "guardrails": s.guardrails or [],
                "output_preview": _preview(s.output),
            }
            for s in steps
        ],
        "artifacts": [
            {"id": a.id, "kind": a.kind, "version": a.version, "status": a.status, "agent_id": a.agent_id}
            for a in artifacts
        ],
    }


def _preview(output: dict[str, Any] | None) -> dict[str, Any]:
    if not output:
        return {}
    keys = ("title", "precheck", "main_selling_point", "funnel", "attribution", "hook")
    return {k: output[k] for k in keys if k in output}


@router.post("/runs/{run_id}/approve", tags=["runs"])
def approve_run(run_id: str) -> dict[str, Any]:
    try:
        result = engine.approve(run_id)
    except KeyError as exc:
        raise HTTPException(404, "运行不存在") from exc
    if not result.get("ok"):
        raise HTTPException(409, result.get("reason", "当前状态不可审批"))
    return result


@router.post("/runs/{run_id}/reject", tags=["runs"])
def reject_run(run_id: str, body: RejectRequest) -> dict[str, Any]:
    engine.reject(run_id, body.reason)
    return {"ok": True, "run_id": run_id, "status": "cancelled"}


@router.post("/runs/{run_id}/iterate", status_code=202, tags=["runs"])
def iterate_run(run_id: str, body: IterateRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    run = db.get(Run, run_id)
    if run is None:
        raise HTTPException(404, "运行不存在")
    project = db.get(Project, run.project_id)
    extra = {
        "ad_rows": body.ad_rows,
        "reviews": body.reviews,
        "ad_rows_after": body.ad_rows_after,
        "source_run_id": run_id,
    }
    new_run_id = engine.create_run(project, "closed_loop", extra=extra)
    engine.launch(new_run_id)
    return {"run_id": new_run_id, "status": "queued", "poll": f"/api/runs/{new_run_id}"}


@router.get("/runs/{run_id}/trace", tags=["runs"])
def get_trace(run_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    spans = db.scalars(select(TraceSpan).where(TraceSpan.run_id == run_id).order_by(TraceSpan.created_at)).all()
    return {
        "run_id": run_id,
        "spans": [
            {
                "id": s.id,
                "step_run_id": s.step_run_id,
                "agent_id": s.agent_id,
                "name": s.name,
                "kind": s.kind,
                "status": s.status,
                "latency_ms": s.latency_ms,
                "tokens": s.tokens,
                "cost_usd": s.cost_usd,
                "attrs": s.attrs,
                "created_at": _dt(s.created_at),
            }
            for s in spans
        ],
        "summary": {
            "count": len(spans),
            "tokens": sum(s.tokens for s in spans),
            "cost_usd": round(sum(s.cost_usd for s in spans), 6),
            "latency_ms": sum(s.latency_ms for s in spans),
            "errors": sum(1 for s in spans if s.status == "error"),
        },
    }


@router.get("/runs/{run_id}/feedback", tags=["runs"])
def get_feedback(run_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    rows = db.scalars(select(FeedbackEvent).where(FeedbackEvent.run_id == run_id)).all()
    return {
        "items": [
            {
                "id": f.id,
                "source_agent": f.source_agent,
                "target_agent": f.target_agent,
                "severity": f.severity,
                "issue": f.issue,
                "action": f.action,
                "evidence": f.evidence,
            }
            for f in rows
        ],
        "count": len(rows),
    }


# --------------------------------------------------------------------------- #
# 产物
# --------------------------------------------------------------------------- #
@router.get("/projects/{project_id}/artifacts", tags=["artifacts"])
def list_artifacts(
    project_id: str,
    kind: str | None = None,
    latest_only: bool = True,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    stmt = select(Artifact).where(Artifact.project_id == project_id)
    if kind:
        stmt = stmt.where(Artifact.kind == kind)
    rows = db.scalars(stmt.order_by(desc(Artifact.version), desc(Artifact.created_at))).all()
    if latest_only:
        seen: set[str] = set()
        picked = []
        for row in rows:
            if row.kind in seen:
                continue
            seen.add(row.kind)
            picked.append(row)
        rows = picked
    return {
        "items": [
            {
                "id": a.id,
                "kind": a.kind,
                "version": a.version,
                "status": a.status,
                "agent_id": a.agent_id,
                "run_id": a.run_id,
                "created_at": _dt(a.created_at),
                "content": a.content,
            }
            for a in rows
        ]
    }


@router.get("/artifacts/{artifact_id}", tags=["artifacts"])
def get_artifact(artifact_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    a = db.get(Artifact, artifact_id)
    if a is None:
        raise HTTPException(404, "产物不存在")
    return {
        "id": a.id,
        "project_id": a.project_id,
        "kind": a.kind,
        "version": a.version,
        "status": a.status,
        "agent_id": a.agent_id,
        "run_id": a.run_id,
        "content": a.content,
        "created_at": _dt(a.created_at),
    }


# --------------------------------------------------------------------------- #
# 工具与单 Agent 预览（接口示例用）
# --------------------------------------------------------------------------- #
@router.get("/tools", tags=["tools"])
def tools(tag: str | None = None) -> dict[str, Any]:
    return {
        "items": [
            {
                "name": t.name,
                "description": t.description,
                "tags": list(t.tags),
                "args_schema": t.schema()["function"]["parameters"],
            }
            for t in list_tools(tag)
        ]
    }


@router.post("/tools/{tool_name}/invoke", tags=["tools"])
def invoke(tool_name: str, body: dict[str, Any]) -> dict[str, Any]:
    try:
        result, attrs = invoke_tool(tool_name, body.get("args") or {})
    except ToolError as exc:
        raise HTTPException(422, {"tool": tool_name, "message": exc.message}) from exc
    return {"tool": tool_name, "result": result, "attrs": attrs}


@router.post("/agents/{agent_id}/preview", tags=["agents"])
def preview(agent_id: str, body: PreviewRequest) -> dict[str, Any]:
    if agent_id not in {a.id for a in AGENTS}:
        raise HTTPException(404, f"未知 Agent：{agent_id}")
    spec = get_agent(agent_id)
    ctx = AgentContext(
        project={"platform": body.platform, "market": body.market, "language": body.language, "name": ""},
        brief=body.brief,
        artifacts=body.artifacts,
        run_id="preview",
        step_id="preview",
        mode="preview",
    )
    runtime = AgentRuntime(get_provider())
    try:
        result = runtime.run(spec, ctx)
    except AgentRunError as exc:
        raise HTTPException(422, {"agent_id": agent_id, "stage": exc.stage, "message": str(exc)}) from exc
    return {
        "agent_id": agent_id,
        "output": result.output,
        "checks": result.checks,
        "guardrails": result.guardrails,
        "blocked": result.blocked,
        "metrics": {
            "tokens_in": result.usage.prompt_tokens,
            "tokens_out": result.usage.completion_tokens,
            "latency_ms": result.latency_ms,
            "attempts": result.attempts,
            "tool_rounds": result.tool_rounds,
        },
        "trace": result.spans,
    }


@router.get("/agents/{agent_id}/tools", tags=["agents"])
def agent_tools(agent_id: str) -> dict[str, Any]:
    try:
        spec = get_agent(agent_id)
    except KeyError as exc:
        raise HTTPException(404, f"未知 Agent：{agent_id}") from exc
    return {"agent_id": agent_id, "tools": [get_tool(t).schema() for t in spec.tools]}


# --------------------------------------------------------------------------- #
# 指标与评测
# --------------------------------------------------------------------------- #
@router.get("/metrics/overview", tags=["metrics"])
def metrics_overview(db: Session = Depends(get_db)) -> dict[str, Any]:
    runs = db.scalars(select(Run)).all()
    steps = db.scalars(select(StepRun)).all()
    total = len(runs)
    completed = sum(1 for r in runs if r.status == "completed")
    failed = sum(1 for r in runs if r.status == "failed")
    checks = [c for s in steps for c in (s.checks or [])]
    guardrails = [g for s in steps for g in (s.guardrails or [])]
    per_agent: dict[str, dict[str, Any]] = {}
    for s in steps:
        bucket = per_agent.setdefault(
            s.agent_id, {"runs": 0, "tokens": 0, "cost_usd": 0.0, "latency_ms": 0, "checks": 0, "checks_passed": 0}
        )
        bucket["runs"] += 1
        bucket["tokens"] += s.tokens_in + s.tokens_out
        bucket["cost_usd"] = round(bucket["cost_usd"] + s.cost_usd, 6)
        bucket["latency_ms"] += s.latency_ms
        bucket["checks"] += len(s.checks or [])
        bucket["checks_passed"] += sum(1 for c in (s.checks or []) if c.get("passed"))
    for bucket in per_agent.values():
        bucket["latency_ms"] = int(bucket["latency_ms"] / max(1, bucket["runs"]))
        bucket["check_pass_rate"] = round(bucket["checks_passed"] / bucket["checks"], 3) if bucket["checks"] else None
    return {
        "runs": {"total": total, "completed": completed, "failed": failed, "success_rate": round(completed / total, 3) if total else None},
        "steps": len(steps),
        "tokens": sum(s.tokens_in + s.tokens_out for s in steps),
        "cost_usd": round(sum(s.cost_usd for s in steps), 6),
        "checks": {"total": len(checks), "passed": sum(1 for c in checks if c.get("passed"))},
        "guardrails": {
            "block": sum(1 for g in guardrails if g.get("severity") == "block"),
            "repair": sum(1 for g in guardrails if g.get("severity") == "repair"),
            "warn": sum(1 for g in guardrails if g.get("severity") == "warn"),
        },
        "per_agent": per_agent,
    }


@router.post("/eval/run", tags=["eval"])
def run_eval(suite: str = "golden") -> dict[str, Any]:
    from app.eval.runner import run_suite

    return run_suite(suite)


@router.get("/eval/runs", tags=["eval"])
def list_evals(limit: int = 20, db: Session = Depends(get_db)) -> dict[str, Any]:
    rows = db.scalars(select(EvalRun).order_by(desc(EvalRun.created_at)).limit(limit)).all()
    return {
        "items": [
            {"id": e.id, "suite": e.suite, "total": e.total, "passed": e.passed, "score": e.score, "results": e.results, "created_at": _dt(e.created_at)}
            for e in rows
        ]
    }


@router.get("/demo/curl", tags=["meta"])
def demo_curl() -> dict[str, Any]:
    """给面试演示用：直接返回可复制的 curl 命令。"""
    from app.seed import DEMO_PROJECT_ID

    return {
        "create_run": f'curl -X POST http://127.0.0.1:8000/api/projects/{DEMO_PROJECT_ID}/runs -H "Content-Type: application/json" -d \'{{"mode":"full_pipeline"}}\'',
        "preview_listing": 'curl -X POST http://127.0.0.1:8000/api/agents/03_listing_creative/preview -H "Content-Type: application/json" -d @examples/listing_preview.json',
        "invoke_tool": 'curl -X POST http://127.0.0.1:8000/api/tools/market_metrics/invoke -H "Content-Type: application/json" -d @examples/tool_market_metrics.json',
        "closed_loop": 'curl -X POST http://127.0.0.1:8000/api/runs/RUN_ID/iterate -H "Content-Type: application/json" -d @examples/iterate_closed_loop.json',
    }
