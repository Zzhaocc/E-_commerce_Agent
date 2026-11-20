"""全局配置：全部来自环境变量，便于容器化与面试演示时切模型。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    app_name: str = "CrossBorderOps Agent Platform"
    version: str = "0.6.0"

    # 存储
    database_url: str = field(
        default_factory=lambda: os.getenv("DATABASE_URL", "sqlite:///./data/studio.db")
    )
    data_dir: Path = field(default_factory=lambda: Path(__file__).resolve().parent.parent / "data")

    # 模型
    llm_provider: str = field(default_factory=lambda: os.getenv("LLM_PROVIDER", "mock"))
    llm_base_url: str = field(default_factory=lambda: os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1"))
    llm_api_key: str = field(default_factory=lambda: os.getenv("LLM_API_KEY", ""))
    llm_model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", "deepseek-chat"))
    llm_temperature: float = field(default_factory=lambda: float(os.getenv("LLM_TEMPERATURE", "0.3")))
    llm_timeout: float = field(default_factory=lambda: float(os.getenv("LLM_TIMEOUT", "60")))

    # Agent 运行时
    max_tool_steps: int = field(default_factory=lambda: int(os.getenv("MAX_TOOL_STEPS", "4")))
    max_attempts: int = field(default_factory=lambda: int(os.getenv("MAX_ATTEMPTS", "2")))
    human_approval: bool = field(default_factory=lambda: _bool("HUMAN_APPROVAL", True))

    # 成本核算（美元 / 千 token），默认取一个通用低价档，仅用于演示成本看板
    cost_per_1k_in: float = 0.00027
    cost_per_1k_out: float = 0.0011

    @property
    def sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return settings
