"""平台级测试：探针、请求关联 ID、安全响应头、错误契约、配置解析。

这些是网关 / K8s / 日志采集依赖的接口约定，改动中间件或错误处理会立刻红。
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app import __version__
from app.config import Settings, get_settings, reset_settings_cache
from app.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_health_reports_runtime(client: TestClient) -> None:
    body = client.get("/api/health").json()

    assert body["status"] == "ok"
    assert body["version"] == __version__
    assert body["provider"] == "mock"
    assert body["database"] == "sqlite"
    assert body["uptime_s"] >= 0
    assert "provider_error" not in body


def test_readiness_checks_dependencies(client: TestClient) -> None:
    resp = client.get("/api/ready")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert set(body["checks"]) == {"database", "tools", "agents", "contracts"}
    assert all(body["checks"].values())


def test_request_id_generated_and_propagated(client: TestClient) -> None:
    generated = client.get("/api/health")
    request_id = generated.headers["X-Request-ID"]
    assert request_id and len(request_id) >= 8

    propagated = client.get("/api/health", headers={"X-Request-ID": "trace-0001"})
    assert propagated.headers["X-Request-ID"] == "trace-0001"

    not_found = client.get("/api/projects/not-exist", headers={"X-Request-ID": "trace-404"})
    assert not_found.json()["request_id"] == "trace-404"


def test_security_headers_present(client: TestClient) -> None:
    headers = client.get("/api/health").headers

    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["X-Frame-Options"] == "SAMEORIGIN"
    assert headers["Referrer-Policy"] == "no-referrer"
    assert "Permissions-Policy" in headers


def test_error_envelope_carries_request_id(client: TestClient) -> None:
    resp = client.get("/api/runs/run_not_exist")

    assert resp.status_code == 404
    body = resp.json()
    assert body["detail"] == "运行不存在"
    assert body["request_id"] == resp.headers["X-Request-ID"]


def test_validation_error_lists_offending_fields(client: TestClient) -> None:
    resp = client.post("/api/projects", json={"unexpected": 1})

    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["message"]
    assert any(item["loc"] == ["body", "name"] and item["type"] == "missing" for item in detail["errors"])
    assert any(item["type"] == "extra_forbidden" for item in detail["errors"])


def test_timestamps_are_utc_iso8601(client: TestClient) -> None:
    items = client.get("/api/projects").json()["items"]

    assert items
    created_at = items[0]["created_at"]
    assert created_at.endswith("Z")
    parsed = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None


def test_settings_describe_never_exposes_secrets() -> None:
    settings = Settings(llm_api_key="sk-do-not-leak")

    payload = json.dumps(settings.describe(), ensure_ascii=False)

    assert "sk-do-not-leak" not in payload
    assert settings.describe()["provider"] == "mock"


def test_settings_read_overrides_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "https://a.example.com,https://b.example.com")
    monkeypatch.setenv("LOG_LEVEL", "debug")
    reset_settings_cache()
    try:
        settings = get_settings()
        assert settings.cors_origins == ("https://a.example.com", "https://b.example.com")
        assert settings.log_level == "DEBUG"
    finally:
        reset_settings_cache()
