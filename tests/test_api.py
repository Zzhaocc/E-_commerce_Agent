"""HTTP 接口测试（TestClient 走真实 ASGI，含后台线程执行）。"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.seed import DEMO_PROJECT_ID, SCOOTER_BRIEF


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _wait_run(client: TestClient, run_id: str, timeout: float = 20.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        data = client.get(f"/api/runs/{run_id}").json()
        if data["status"] in {"completed", "failed", "awaiting_approval", "cancelled"}:
            return data
        time.sleep(0.05)
    raise AssertionError(f"运行未结束：{run_id}")


def test_health_and_catalog(client: TestClient) -> None:
    health = client.get("/api/health").json()
    assert health["status"] == "ok"
    assert health["provider"] == "mock"

    catalog = client.get("/api/meta/catalog").json()
    assert len(catalog["agents"]) == 7
    assert len(catalog["tools"]) >= 10
    assert catalog["pipeline"]["parallel_cluster"] == [
        "03_listing_creative",
        "04_product_image",
        "05_product_video",
    ]


def test_projects_seeded(client: TestClient) -> None:
    data = client.get("/api/projects").json()
    ids = {item["id"] for item in data["items"]}
    assert DEMO_PROJECT_ID in ids
    assert data["total"] >= 2


def test_contract_endpoint(client: TestClient) -> None:
    data = client.get("/api/contracts/01_market_research").json()
    assert "input_schema" in data and "output_schema" in data
    assert data["output_schema"]["properties"]["precheck"]


def test_tool_invoke_endpoint(client: TestClient) -> None:
    rows = [
        {"price": 100, "monthly_sales": 50, "config": {"a": "1"}},
        {"price": 120, "monthly_sales": 40, "config": {"a": "2"}},
        {"price": 140, "monthly_sales": 10, "config": {"a": "3"}},
    ]
    resp = client.post("/api/tools/market_metrics/invoke", json={"args": {"rows": rows}})
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert 0 <= result["cr4"] <= 1
    assert result["price_band"]["valley_low"] <= result["price_band"]["valley_high"]


def test_tool_invoke_rejects_bad_args(client: TestClient) -> None:
    resp = client.post("/api/tools/unit_economics/invoke", json={"args": {"cogs": 1, "oops": True}})
    assert resp.status_code == 422


def test_agent_preview_and_contract_violation(client: TestClient) -> None:
    body = {
        "brief": SCOOTER_BRIEF,
        "platform": "shopee",
        "artifacts": {"product_strategy": {"main_selling_point": "Motor 500W sobe ladeira", "pricing": {}}},
    }
    ok = client.post("/api/agents/03_listing_creative/preview", json=body)
    assert ok.status_code == 200
    payload = ok.json()
    assert payload["output"]["title"]["chars"] > 0
    assert payload["output"]["bullets"]
    assert any(span["kind"] == "tool" for span in payload["trace"])

    bad = client.post("/api/agents/06_advertising/preview", json={"brief": {**SCOOTER_BRIEF, "ad_rows": []}})
    assert bad.status_code == 422


def test_run_lifecycle_approve_and_publish(client: TestClient) -> None:
    created = client.post("/api/projects", json={"name": "接口测试项目", "platform": "shopee", "brief": SCOOTER_BRIEF})
    assert created.status_code == 201
    project_id = created.json()["id"]

    started = client.post(f"/api/projects/{project_id}/runs", json={"mode": "full_pipeline"})
    assert started.status_code == 202
    run_id = started.json()["run_id"]

    run = _wait_run(client, run_id)
    assert run["status"] == "awaiting_approval"
    assert len(run["steps"]) == 5

    approved = client.post(f"/api/runs/{run_id}/approve")
    assert approved.status_code == 200

    done = client.get(f"/api/runs/{run_id}").json()
    assert done["status"] == "completed"
    assert done["metrics"]["checks_total"] > 0

    artifacts = client.get(f"/api/projects/{project_id}/artifacts").json()["items"]
    kinds = {a["kind"] for a in artifacts}
    assert {"market_report", "product_strategy", "listing_pack", "image_pack", "video_pack", "publish_pack"} <= kinds

    trace = client.get(f"/api/runs/{run_id}/trace").json()
    assert trace["summary"]["count"] > 0
    assert any(s["kind"] == "llm" for s in trace["spans"])
    assert trace["summary"]["tokens"] > 0


def test_iterate_endpoint_creates_closed_loop_run(client: TestClient) -> None:
    # 先有一个可迭代的诊断运行
    seeded = client.post(
        f"/api/projects/{DEMO_PROJECT_ID}/runs",
        json={"mode": "diagnose", "ad_rows": SCOOTER_BRIEF["ad_rows"], "reviews": SCOOTER_BRIEF["reviews"]},
    )
    assert seeded.status_code == 202
    base_run = _wait_run(client, seeded.json()["run_id"])
    assert base_run["status"] == "completed"

    body = {
        "ad_rows": SCOOTER_BRIEF["ad_rows"],
        "reviews": SCOOTER_BRIEF["reviews"],
        "ad_rows_after": SCOOTER_BRIEF["ad_rows_after"],
    }
    started = client.post(f"/api/runs/{base_run['id']}/iterate", json=body)
    assert started.status_code == 202
    run = _wait_run(client, started.json()["run_id"])
    assert run["status"] == "completed"
    assert run["metrics"]["retest"]["after"]["roas"] > run["metrics"]["retest"]["before"]["roas"]

    feedback = client.get(f"/api/runs/{run['id']}/feedback").json()
    assert feedback["count"] > 0


def test_eval_and_metrics_endpoints(client: TestClient) -> None:
    evaluation = client.post("/api/eval/run?suite=golden").json()
    assert evaluation["total"] >= 10
    assert evaluation["score"] >= 0.9, evaluation["results"]

    overview = client.get("/api/metrics/overview").json()
    assert overview["runs"]["total"] >= 1
    assert overview["tokens"] >= 0
    assert overview["checks"]["total"] >= 1
