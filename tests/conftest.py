"""测试入口：必须在导入 app 之前把数据库指到独立的测试文件。"""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
TEST_DB = BASE_DIR / "data" / "test_studio.db"

os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB.as_posix()}"
os.environ["LLM_PROVIDER"] = "mock"
os.environ["HUMAN_APPROVAL"] = "true"

for suffix in ("", "-wal", "-shm"):
    candidate = Path(str(TEST_DB) + suffix)
    if candidate.exists():
        candidate.unlink()

import pytest  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def prepared_db():
    from app.db import init_db
    from app.seed import seed_demo_projects
    from app.tools.base import load_all_tools

    init_db()
    load_all_tools()
    seed_demo_projects()
    yield
