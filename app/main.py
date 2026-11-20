"""应用入口：装配 API、静态控制台、启动自检与崩溃恢复。

启动顺序：建表 → 注册工具 → 灌演示数据 → 恢复中断的运行 → 暴露 /api 与 /
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from app.api.routes import router
from app.config import get_settings
from app.db import init_db, session_scope
from app.models import Run
from app.seed import seed_demo_projects
from app.tools.base import load_all_tools

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


def _recover_stale_runs() -> int:
    """进程重启后，把上次未结束的运行标记为失败，避免永远 running。"""
    with session_scope() as db:
        rows = db.scalars(select(Run).where(Run.status.in_(["running", "queued"]))).all()
        for run in rows:
            run.status = "failed"
            run.error = "进程重启，运行被中断（可重新发起）"
            run.stage = "interrupted"
        return len(rows)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    init_db()
    load_all_tools()
    seed_demo_projects()
    recovered = _recover_stale_runs()
    app.state.recovered_runs = recovered
    app.state.settings = settings
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="CrossBorderOps Agent Platform",
        description=(
            "跨境电商 AI 运营智能体平台：7 个 SOP Agent + 确定性工具 + 契约校验 + "
            "护栏 + 闭环回流。默认 mock provider，无需 API key 即可完整演示。"
        ),
        version=settings.version,
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)

    @app.get("/api", include_in_schema=False)
    def api_root() -> JSONResponse:
        return JSONResponse({"name": settings.app_name, "docs": "/docs", "console": "/"})

    if WEB_DIR.exists():
        app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
    return app


app = create_app()
