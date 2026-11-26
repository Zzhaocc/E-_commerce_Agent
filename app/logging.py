"""结构化日志。

- 统一字段：``ts`` / ``level`` / ``logger`` / ``msg`` / ``request_id`` + 调用方自定义的 ``extra``；
- 两种格式：``text`` 便于本地阅读，``json`` 便于日志采集（Loki / ELK / CloudWatch）；
- 请求关联：``request_id_var`` 由中间件写入，日志自动带上，可在一次运行的所有日志里串联；
- 幂等：``configure_logging`` 可重复调用，不会重复挂 handler。

用法::

    from app.logging import configure_logging, get_logger

    configure_logging(level="INFO", fmt="json")
    log = get_logger(__name__)
    log.info("run_started", extra={"run_id": run_id, "mode": mode})
"""

from __future__ import annotations

import contextvars
import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

# 请求级关联 ID：中间件写入，日志与错误响应读取
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")

# LogRecord 的内建属性，用于把 extra 里的业务字段挑出来
_RESERVED_ATTRS = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "stacklevel",
        "thread",
        "threadName",
        "taskName",
        "request_id",
    }
)


def get_request_id() -> str:
    """当前请求的关联 ID；非请求上下文返回 ``-``。"""
    return request_id_var.get()


def _extra_fields(record: logging.LogRecord) -> dict[str, Any]:
    return {
        key: value for key, value in record.__dict__.items() if key not in _RESERVED_ATTRS and not key.startswith("_")
    }


class TextFormatter(logging.Formatter):
    """人类可读格式：``2026-01-01T00:00:00Z INFO  [rid] logger: msg key=value``。"""

    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created, tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        parts = [timestamp, record.levelname.ljust(5), f"[{getattr(record, 'request_id', '-')}]", f"{record.name}:"]
        text = " ".join(parts) + " " + record.getMessage()
        extras = _extra_fields(record)
        if extras:
            text += " " + " ".join(f"{k}={json.dumps(v, ensure_ascii=False, default=str)}" for k, v in extras.items())
        if record.exc_info:
            text += "\n" + self.formatException(record.exc_info)
        return text


class JsonFormatter(logging.Formatter):
    """一行一个 JSON，字段固定，便于采集端按 key 建索引。"""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        payload.update(_extra_fields(record))
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class _RequestIdFilter(logging.Filter):
    """把 contextvar 里的 request_id 注入每条记录。"""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            record.request_id = request_id_var.get()
        return True


_HANDLER: logging.Handler | None = None


def configure_logging(level: str = "INFO", fmt: str = "text") -> None:
    """配置根 logger。重复调用只更新级别与格式，不触碰其它 handler（含 pytest 的 caplog）。"""
    global _HANDLER

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    if _HANDLER is None:
        _HANDLER = logging.StreamHandler(stream=sys.stdout)
        _HANDLER.addFilter(_RequestIdFilter())
        root.addHandler(_HANDLER)

    _HANDLER.setFormatter(JsonFormatter() if fmt == "json" else TextFormatter())

    # 第三方库降噪：访问日志由中间件统一输出
    logging.getLogger("uvicorn.access").disabled = True
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
