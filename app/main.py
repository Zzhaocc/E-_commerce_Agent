"""应用入口：装配 API、静态控制台、可观测中间件与启动自检。

启动顺序：配置日志 → 建表 → 注册工具 → 灌入示例数据 → 恢复中断运行 → 暴露 /api 与 /。
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from app import __version__
from app.api.routes import router
from app.config import get_settings
from app.db import init_db, session_scope
from app.errors import install_error_handlers
from app.logging import configure_logging, get_logger
from app.middleware import RequestContextMiddleware, SecurityHeadersMiddleware
from app.models import Run
from app.seed import seed_demo_projects
from app.tools.base import load_all_tools

log = get_logger("app.main")

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

OPENAPI_TAGS = [
    {"name": "meta", "description": "健康检查、能力目录、契约与示例"},
    {"name": "projects", "description": "商品项目：事实、成本、竞品数据"},
    {"name": "runs", "description": "工作流运行：发起、轮询、审批、闭环、trace"},
    {"name": "artifacts", "description": "版本化产物：市场报告 / 策略 / 素材 / 发布包"},
    {"name": "agents", "description": "单 Agent 预览与工具白名单"},
    {"name": "tools", "description": "确定性工具：口径可复核、可单独调用"},
    {"name": "eval", "description": "回归评测与门禁"},
    {"name": "metrics", "description": "平台级成功率、成本与护栏分布"},
]


def _recover_stale_runs() -> int:
    """进程重启后，把上次未结束的运行标记为失败，避免永远停在 running。"""
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
    configure_logging(settings.log_level, settings.log_format)
    log.info("startup", extra=settings.describe())

    init_db()
    load_all_tools()
    seed_demo_projects()
    recovered = _recover_stale_runs()
    if recovered:
        log.warning("recovered_stale_runs", extra={"count": recovered})

    app.state.settings = settings
    app.state.started_at = time.time()
    app.state.recovered_runs = recovered

    yield

    log.info("shutdown", extra={"uptime_s": round(time.time() - app.state.started_at, 3)})


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)

    app = FastAPI(
        title=settings.app_name,
        description=(
            "跨境电商 AI 运营 Agent 平台：7 个 SOP Agent + 确定性工具 + 双契约校验 + "
            "三级护栏 + 版本化闭环。默认 mock provider，无需 API key 即可完整运行。"
        ),
        version=__version__,
        openapi_tags=OPENAPI_TAGS,
        license_info={"name": "MIT"},
        docs_url="/docs" if settings.docs_enabled else None,
        redoc_url="/redoc" if settings.docs_enabled else None,
        openapi_url="/openapi.json" if settings.docs_enabled else None,
        lifespan=lifespan,
    )

    # 添加顺序决定执行顺序：最后添加的在最外层（CORS 需要最先处理预检与错误响应）
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestContextMiddleware, header_name=settings.request_id_header)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials="*" not in settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=[settings.request_id_header],
    )

    install_error_handlers(app)
    app.include_router(router)

    @app.get("/api", include_in_schema=False)
    def api_root() -> dict[str, object]:
        return {
            "name": settings.app_name,
            "version": __version__,
            "environment": settings.environment,
            "docs": "/docs" if settings.docs_enabled else None,
            "console": "/",
        }

    if WEB_DIR.exists():
        app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
    return app


app = create_app()
