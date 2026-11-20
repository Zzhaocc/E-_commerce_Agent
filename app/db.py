"""数据库装配。

默认 SQLite（零依赖、可直接演示）；DATABASE_URL 换成 PostgreSQL 即可上生产，
ORM 层不依赖任何 SQLite 特性。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


_settings = get_settings()

engine = create_engine(
    _settings.database_url,
    connect_args={"check_same_thread": False} if _settings.sqlite else {},
    pool_pre_ping=True,
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def init_db() -> None:
    """建表 + 打开 SQLite 的 WAL（并发读更稳，演示时前端轮询不会锁库）。"""
    from app import models  # noqa: F401  —— 触发 ORM 注册

    Base.metadata.create_all(bind=engine)
    if _settings.sqlite:
        with engine.connect() as conn:
            conn.exec_driver_sql("PRAGMA journal_mode=WAL")
            conn.exec_driver_sql("PRAGMA foreign_keys=ON")


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """后台任务（工作流引擎）用：不依赖 FastAPI 依赖注入。"""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
