"""全局配置：全部来自环境变量，便于容器化部署与多环境切换。

设计约定：
- 只读：配置在进程启动时解析一次（``get_settings`` 带缓存），运行期不热更新；
- 无副作用：除 ``data_dir`` 需要创建目录外，构造 ``Settings`` 不触达外部资源；
- 可审计：``describe()`` 只导出非敏感字段，用于 /api/health 与启动日志。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from app import __version__

REPO_ROOT = Path(__file__).resolve().parent.parent


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    try:
        return int(raw) if raw not in (None, "") else default
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    raw = os.getenv(name)
    try:
        return float(raw) if raw not in (None, "") else default
    except ValueError:
        return default


def _items(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return tuple(part.strip() for part in raw.split(",") if part.strip())


@dataclass(frozen=True)
class Settings:
    """进程级配置快照。"""

    app_name: str = "CrossBorderOps Agent Platform"
    version: str = __version__
    environment: str = field(default_factory=lambda: os.getenv("APP_ENV", "development"))
    debug: bool = field(default_factory=lambda: _bool("DEBUG", False))

    # ---- 存储 ----
    database_url: str = field(default_factory=lambda: os.getenv("DATABASE_URL", "sqlite:///./data/studio.db"))
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("DATA_DIR", str(REPO_ROOT / "data"))))

    # ---- 模型 ----
    llm_provider: str = field(default_factory=lambda: os.getenv("LLM_PROVIDER", "mock"))
    llm_base_url: str = field(default_factory=lambda: os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1"))
    llm_api_key: str = field(default_factory=lambda: os.getenv("LLM_API_KEY", ""))
    llm_model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", "deepseek-chat"))
    llm_temperature: float = field(default_factory=lambda: _float("LLM_TEMPERATURE", 0.3))
    llm_timeout: float = field(default_factory=lambda: _float("LLM_TIMEOUT", 60.0))

    # ---- Agent 运行时保护 ----
    max_tool_steps: int = field(default_factory=lambda: _int("MAX_TOOL_STEPS", 4))
    max_attempts: int = field(default_factory=lambda: _int("MAX_ATTEMPTS", 2))
    human_approval: bool = field(default_factory=lambda: _bool("HUMAN_APPROVAL", True))

    # ---- 成本核算（美元 / 千 token，用于成本看板归因）----
    cost_per_1k_in: float = field(default_factory=lambda: _float("COST_PER_1K_IN", 0.00027))
    cost_per_1k_out: float = field(default_factory=lambda: _float("COST_PER_1K_OUT", 0.0011))

    # ---- 接入与可观测 ----
    cors_origins: tuple[str, ...] = field(default_factory=lambda: _items("CORS_ORIGINS", ("*",)))
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO").upper())
    log_format: str = field(default_factory=lambda: os.getenv("LOG_FORMAT", "text").lower())
    request_id_header: str = field(default_factory=lambda: os.getenv("REQUEST_ID_HEADER", "X-Request-ID"))
    docs_enabled: bool = field(default_factory=lambda: _bool("DOCS_ENABLED", True))

    @property
    def sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    def describe(self) -> dict[str, object]:
        """对外暴露的运行态摘要（不含密钥）。"""
        return {
            "app": self.app_name,
            "version": self.version,
            "environment": self.environment,
            "provider": self.llm_provider,
            "model": self.llm_model,
            "database": "sqlite" if self.sqlite else "external",
            "human_approval": self.human_approval,
            "log_level": self.log_level,
            "docs_enabled": self.docs_enabled,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return settings


def reset_settings_cache() -> None:
    """仅供测试：环境变量变更后重新解析配置。"""
    get_settings.cache_clear()
