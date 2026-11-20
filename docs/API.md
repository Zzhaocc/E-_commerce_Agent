# API 参考

Base URL：`http://127.0.0.1:8000`　交互式文档：`/docs`（Swagger UI）

## 约定

- 所有接口前缀 `/api`，请求/响应均为 JSON。
- **运行是异步的**：`POST .../runs` 返回 `202 + run_id`，客户端轮询 `GET /api/runs/{id}`，
  直到 `status ∈ {completed, failed, awaiting_approval, cancelled}`。
- 错误语义：

| 状态码 | 含义 | 例子 |
|---|---|---|
| 400 | 请求体不合法 | JSON 结构错误 |
| 404 | 资源不存在 | 未知 project / run / artifact |
| 409 | 状态冲突 | 对非 `awaiting_approval` 的运行调用 approve |
| 422 | **契约校验失败** | 竞品数据 < 3 条、广告数据为空、工具参数多传字段 |

- 所有时间字段为 UTC ISO8601（带 `Z`）。

---

## 端点总览

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 健康检查、当前 provider / model |
| GET | `/api/meta/catalog` | 7 个 Agent、工具清单、流水线定义 |
| GET | `/api/contracts/{agent_id}` | 输入/输出 JSON Schema |
| POST | `/api/projects` | 新建项目（商品事实 + 成本 + 竞品数据） |
| GET | `/api/projects` | 项目列表 |
| GET | `/api/projects/{id}` | 项目详情（含运行与产物索引） |
| DELETE | `/api/projects/{id}` | 删除项目（级联） |
| POST | `/api/projects/{id}/runs` | 发起运行 |
| GET | `/api/runs` | 运行列表（可按 project/status 过滤） |
| GET | `/api/runs/{id}` | 运行详情（步骤、自检、护栏、产物） |
| POST | `/api/runs/{id}/approve` | 人工审批通过 → 生成发布包 |
| POST | `/api/runs/{id}/reject` | 驳回 |
| POST | `/api/runs/{id}/iterate` | 闭环迭代（诊断→回流→v2→复测） |
| GET | `/api/runs/{id}/trace` | 统一 trace（llm / tool / guardrail） |
| GET | `/api/runs/{id}/feedback` | 回流事件列表 |
| GET | `/api/projects/{id}/artifacts` | 产物（`latest_only=true` 按 kind 去重） |
| GET | `/api/artifacts/{id}` | 单个产物全文 |
| GET | `/api/tools` | 工具列表（可按 tag 过滤） |
| POST | `/api/tools/{name}/invoke` | 直接执行确定性工具 |
| POST | `/api/agents/{id}/preview` | 单 Agent 试跑（不落库、不产生运行） |
| GET | `/api/agents/{id}/tools` | 该 Agent 可用的 function-calling schema |
| POST | `/api/eval/run?suite=golden` | 跑回归评测 |
| GET | `/api/eval/runs` | 评测历史 |
| GET | `/api/metrics/overview` | 平台级指标（成功率、token、成本、护栏分布） |
| GET | `/api/demo/curl` | 返回可直接复制的 curl 命令 |

---

## 1. 发起运行

```http
POST /api/projects/prj_demo_scooter/runs
Content-Type: application/json

{"mode": "full_pipeline"}
```

`mode` 取值：

| mode | 做什么 | 结束状态 |
|---|---|---|
| `full_pipeline` | 01 → 02 → (03 ∥ 04 ∥ 05) | 审批开启时 `awaiting_approval`，否则 `completed` |
| `creative_only` | 03 ∥ 04 ∥ 05（复用已有 02 产物） | 同上 |
| `diagnose` | 06 → 07，只诊断不出素材 | `completed` |
| `closed_loop` | 06 → 07 → 回流 → 03/04 v2 → 复测 | `completed` |
| `single` | 指定一个 Agent（需 `agent_id`） | `completed` |

响应：

```json
{"run_id": "run_9f2c1a7b3d10", "status": "queued", "poll": "/api/runs/run_9f2c1a7b3d10"}
```

运行时可临时覆盖数据（演示不同窗口）：

```json
{
  "mode": "diagnose",
  "ad_rows": [{"campaign": "Search", "impressions": 85000, "clicks": 1100, "orders": 3, "spend": 3100, "revenue": 7902}],
  "reviews": [{"rating": 2, "text": "A bateria não dura o anunciado", "variant": "500W 12Ah"}]
}
```

---

## 2. 查询运行

```http
GET /api/runs/{run_id}
```

```json
{
  "id": "run_9f2c1a7b3d10",
  "mode": "full_pipeline",
  "status": "awaiting_approval",
  "stage": "creative_ready",
  "error": null,
  "metrics": {
    "steps": 5,
    "tokens": 13873,
    "cost_usd": 0.006276,
    "wall_ms": 174,
    "checks_total": 31,
    "checks_passed": 31,
    "guardrails": {"block": 0, "repair": 0, "warn": 0},
    "per_agent": [{"agent_id": "01_market_research", "tokens": 2900, "cost_usd": 0.001214, "latency_ms": 14, "attempts": 1}]
  },
  "steps": [
    {
      "agent_id": "03_listing_creative",
      "status": "succeeded",
      "checks_total": 8,
      "checks_passed": 8,
      "guardrails": [],
      "output_preview": {"title": {"text": "Patinete Elétrico 500W Adulto - ...", "chars": 82, "limit": 120}}
    }
  ],
  "artifacts": [{"id": "art_...", "kind": "listing_pack", "version": 1, "status": "draft"}]
}
```

`stage` 取值：`queued` → `01_market_research` → `02_product_strategy` → `creative_cluster`
→ `creative_ready` → `published`（或 `diagnose` / `iterate_creative` / `retest`）。

---

## 3. 审批与发布包

```http
POST /api/runs/{run_id}/approve
```

审批后生成 `kind=publish_pack` 的产物：包含市场结论、策略、Listing、9 图、视频与发布自检清单。

```json
{
  "ok": true,
  "run_id": "run_9f2c1a7b3d10"
}
```

对非 `awaiting_approval` 的运行调用 → `409`。

```http
POST /api/runs/{run_id}/reject
{"reason": "主图卖点与标题不一致"}
```

---

## 4. 闭环迭代

```http
POST /api/runs/{run_id}/iterate
Content-Type: application/json

{
  "ad_rows": [ ... 诊断窗口数据 ... ],
  "reviews": [ ... 评论样本 ... ],
  "ad_rows_after": [ ... 第二窗口复测数据（可选） ... ]
}
```

响应同"发起运行"。运行完成后 `metrics.retest` 给出改前/改后对比：

```json
{
  "before": {"roas": 2.9632, "cpa": 888.89, "cvr": 0.0031, "ctr": 0.013},
  "after":  {"roas": 6.8986, "cpa": 381.82, "cvr": 0.0051, "ctr": 0.018},
  "roas_delta": 3.9354,
  "cpa_delta": -507.07,
  "verdict": "达标"
}
```

`GET /api/runs/{id}/feedback` 返回驱动这次改版的回流条目：

```json
{
  "items": [
    {
      "id": "fbk_...",
      "source_agent": "06_advertising",
      "target_agent": "03_listing_creative",
      "severity": "P0",
      "issue": "进店后转化不足（low_cvr）",
      "action": "详情页补尺寸/兼容性/售后三张图，评价区置顶安装视频",
      "evidence": {"expected_impact": "CVR 提升 0.5–1.2 个百分点"}
    }
  ],
  "count": 5
}
```

---

## 5. 单 Agent 预览

```http
POST /api/agents/03_listing_creative/preview
Content-Type: application/json

{
  "brief": { "...商品事实..." },
  "platform": "shopee",
  "artifacts": {"product_strategy": {"main_selling_point": "Motor 500W sobe ladeira de 15° com 90 kg"}}
}
```

```json
{
  "agent_id": "03_listing_creative",
  "output": {"title": {"text": "...", "chars": 82, "limit": 120}, "bullets": ["..."]},
  "checks": [{"id": "title:length", "name": "标题长度", "passed": true, "detail": "82/120"}],
  "guardrails": [{"code": "overlay_too_long", "severity": "repair", "message": "..."}],
  "blocked": false,
  "metrics": {"tokens_in": 2100, "tokens_out": 1800, "latency_ms": 12, "attempts": 1, "tool_rounds": 1},
  "trace": [{"name": "llm.03_listing_creative", "kind": "llm", "status": "ok", "tokens": 3900}]
}
```

契约不满足时返回 `422`：

```json
{"detail": {"agent_id": "01_market_research", "stage": "input_contract",
            "message": "输入契约校验失败: competitor_rows: List should have at least 3 items"}}
```

---

## 6. 直接调工具（口径可复核）

```http
POST /api/tools/unit_economics/invoke
Content-Type: application/json

{"args": {"cogs": 1250, "freight": 120, "commission_rate": 0.14,
          "payment_rate": 0.02, "ad_rate": 0.12, "coupon_budget": 0.05,
          "target_margin": 0.15, "currency": "BRL"}}
```

```json
{
  "tool": "unit_economics",
  "result": {
    "cost_base": 1370.0,
    "rate_sum": 0.28,
    "breakeven_price": 2044.78,
    "target_price": 2634.62,
    "net_margin_at_target": 0.15,
    "net_profit_at_target": 395.19,
    "coupon_headroom": 395.19,
    "allowed_discount_rate": 0.15,
    "coupon_budget_is_safe": true,
    "price_ladder": [{"price": 2107.7, "net_margin": 0.0, "position": "亏损区"}]
  },
  "attrs": {"tool": "unit_economics", "latency_ms": 0}
}
```

参数多传字段 → `422`：

```json
{"detail": {"tool": "unit_economics", "message": "参数校验失败: made_up_field: Extra inputs are not permitted"}}
```

---

## 7. Trace 与指标

```http
GET /api/runs/{run_id}/trace
```

```json
{
  "spans": [
    {"name": "tool.market_metrics", "kind": "tool", "status": "ok", "latency_ms": 1, "tokens": 0, "cost_usd": 0},
    {"name": "llm.01_market_research", "kind": "llm", "status": "ok", "latency_ms": 12, "tokens": 2900, "cost_usd": 0.001214},
    {"name": "gate.01_market_research", "kind": "guardrail", "status": "ok"}
  ],
  "summary": {"count": 7, "tokens": 13873, "cost_usd": 0.0063, "latency_ms": 176, "errors": 0}
}
```

```http
GET /api/metrics/overview
```

返回：运行成功率、总 token / 成本、checks 通过率、护栏分布（block/repair/warn）、按 Agent 的成本与延迟。

---

## 8. 评测

```http
POST /api/eval/run?suite=golden
```

```json
{
  "id": "evl_...",
  "total": 10,
  "passed": 10,
  "score": 1.0,
  "duration_ms": 108,
  "results": [
    {"case": "listing_pet_mercadolivre_title_limit", "agent": "03_listing_creative", "passed": true, "detail": "全部通过"},
    {"case": "market_missing_data_must_fail", "agent": "01_market_research", "passed": true,
     "detail": "按预期失败：[01_market_research/input_contract] ..."}
  ]
}
```

评测结果落 `eval_runs` 表，可对比不同 Prompt / 模型版本的通过率。
