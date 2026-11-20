"""对已启动的服务做端到端冒烟验证。

    python scripts/smoke_http.py            # 默认 http://127.0.0.1:8000
    python scripts/smoke_http.py 8080

覆盖：健康检查 → 建项目 → 跑流水线 → 审批 → 发布包 → 单 Agent 预览
      → 工具口径复算 → 评测 → 指标。全部通过才返回 0。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.seed import SCOOTER_BRIEF  # noqa: E402

BASE = f"http://127.0.0.1:{sys.argv[1] if len(sys.argv) > 1 else 8000}"
OK = "✓"
FAIL = "✗"
failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    mark = OK if condition else FAIL
    print(f"  {mark} {name}{(' — ' + detail) if detail else ''}")
    if not condition:
        failures.append(name)


def main() -> int:
    with httpx.Client(base_url=BASE, timeout=30.0, trust_env=False) as c:
        print(f"目标：{BASE}")

        health = c.get("/api/health").json()
        check("健康检查", health["status"] == "ok", f"{health['provider']} / {health['model']}")

        catalog = c.get("/api/meta/catalog").json()
        check("7 个 Agent", len(catalog["agents"]) == 7, f"{len(catalog['agents'])} 个")
        check("工具注册", len(catalog["tools"]) >= 13, f"{len(catalog['tools'])} 个")

        project = c.post(
            "/api/projects",
            json={"name": "冒烟验证项目", "category": "Patinetes Elétricos", "platform": "shopee", "brief": SCOOTER_BRIEF},
        ).json()
        check("创建项目", bool(project.get("id")), project["id"])

        started = c.post(f"/api/projects/{project['id']}/runs", json={"mode": "full_pipeline"}).json()
        run_id = started["run_id"]
        deadline = time.time() + 30
        run: dict = {}
        while time.time() < deadline:
            run = c.get(f"/api/runs/{run_id}").json()
            if run["status"] in {"completed", "failed", "awaiting_approval", "cancelled"}:
                break
            time.sleep(0.1)

        check("流水线停在人工审批", run["status"] == "awaiting_approval", f"status={run['status']}")
        check("5 个步骤全部成功", all(s["status"] == "succeeded" for s in run["steps"]))
        check("自检通过", run["metrics"]["checks_passed"] >= run["metrics"]["checks_total"] - 1,
              f"{run['metrics']['checks_passed']}/{run['metrics']['checks_total']}")

        approved = c.post(f"/api/runs/{run_id}/approve")
        check("审批通过", approved.status_code == 200)
        final = c.get(f"/api/runs/{run_id}").json()
        check("运行完成", final["status"] == "completed", final.get("error") or "")

        artifacts = {a["kind"]: a["content"] for a in c.get(f"/api/projects/{project['id']}/artifacts").json()["items"]}
        check("发布包生成", "publish_pack" in artifacts)
        if "publish_pack" in artifacts:
            checklist = artifacts["publish_pack"]["checklist"]
            check("发布自检全绿", all(i["passed"] for i in checklist), f"{len(checklist)} 项")

        listing = artifacts.get("listing_pack", {})
        title = listing.get("title", {})
        check("标题在字符上限内", title.get("chars", 0) <= title.get("limit", 1), f"{title.get('chars')}/{title.get('limit')}")
        check("合规通过", listing.get("compliance", {}).get("passed") is True)
        check("标题前段含核心词",
              any(k.lower() in title.get("text", "").lower() for k in listing.get("keyword_map", {}).get("primary", [])[:3]),
              title.get("text", "")[:40])

        band = artifacts.get("market_report", {}).get("price_band", {})
        pricing = artifacts.get("product_strategy", {}).get("pricing", {})
        check("价格带次序正确", band.get("valley_low", 0) < band.get("valley_high", 1))
        check("目标价高于盈亏价", pricing.get("target_price", 0) > pricing.get("breakeven_price", 1),
              f"{pricing.get('breakeven_price')} / {pricing.get('target_price')}")

        tool = c.post(
            "/api/tools/unit_economics/invoke",
            json={"args": {"cogs": 1250, "freight": 120, "commission_rate": 0.14, "payment_rate": 0.02,
                           "ad_rate": 0.12, "coupon_budget": 0.05, "target_margin": 0.15}},
        ).json()["result"]
        expected_breakeven = round(1370 / (1 - 0.28 - 0.05), 2)
        check("盈亏价可复算", abs(tool["breakeven_price"] - expected_breakeven) < 0.01,
              f"{tool['breakeven_price']} == {expected_breakeven}")

        preview = c.post(
            "/api/agents/03_listing_creative/preview",
            json={"brief": SCOOTER_BRIEF, "platform": "shopee",
                  "artifacts": {"product_strategy": {"main_selling_point": "Motor 500W sobe ladeira de 15°", "pricing": {}}}},
        )
        check("单 Agent 预览", preview.status_code == 200)
        if preview.status_code == 200:
            body = preview.json()
            check("预览含 trace", any(s["kind"] == "tool" for s in body["trace"]))

        bad = c.post("/api/agents/06_advertising/preview", json={"brief": {**SCOOTER_BRIEF, "ad_rows": []}})
        check("契约违规返回 422", bad.status_code == 422, str(bad.status_code))

        evaluation = c.post("/api/eval/run?suite=golden").json()
        check("回归评测 ≥0.9", evaluation["score"] >= 0.9, f"{evaluation['passed']}/{evaluation['total']}")

        overview = c.get("/api/metrics/overview").json()
        check("指标可读", overview["runs"]["total"] > 0, f"runs={overview['runs']['total']} tokens={overview['tokens']}")

    print()
    if failures:
        print(f"{FAIL} 失败 {len(failures)} 项：{', '.join(failures)}")
        return 1
    print(f"{OK} 全部冒烟检查通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
