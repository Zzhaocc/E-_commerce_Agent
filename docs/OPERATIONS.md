# 运维手册

面向部署与值班：怎么起、怎么探活、怎么看日志、怎么备份、出问题先看哪里。

---

## 1. 部署形态

### 1.1 单机（最快）

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

数据落在 `./data/studio.db`（WAL 模式）。适合内网试用与联调。

### 1.2 容器

```bash
docker build -t crossborderops-agent-platform:0.7.0 .
docker run -d --name crossborderops \
  -p 8000:8000 \
  -v crossborderops-data:/app/data \
  -e LLM_PROVIDER=mock \
  -e LOG_FORMAT=json \
  crossborderops-agent-platform:0.7.0
```

镜像以非 root 用户（uid 10001）运行，内置 `HEALTHCHECK` 指向 `/api/health`。

### 1.3 Compose

```bash
docker compose up -d --build
docker compose logs -f api
docker compose down
```

`docker-compose.yml` 已把 SQLite 文件挂到具名卷 `app-data`，容器重建不丢数据。

### 1.4 Kubernetes 要点

- **单副本 + RWO 卷**：SQLite 是单写者，多副本共享同一数据库文件会损坏数据；
- `livenessProbe` → `GET /api/health`，`readinessProbe` → `GET /api/ready`；
- 密钥用 Secret 注入 `LLM_API_KEY`，不要写进镜像；
- 要横向扩容时，先按第 7 节换成 PostgreSQL 与外部队列。

> 内置接口**不带鉴权**，对外暴露前必须置于网关 / Ingress 之后。

---

## 2. 环境变量

完整清单见 [`.env.example`](../.env.example)，关键项：

| 变量 | 默认 | 说明 |
|---|---|---|
| `APP_ENV` | `development` | 环境标识，写入启动日志与 `/api/health` |
| `DATABASE_URL` | `sqlite:///./data/studio.db` | 换 PG 时改成 `postgresql+psycopg://...` |
| `DATA_DIR` | `./data` | SQLite 文件与临时产物目录 |
| `LLM_PROVIDER` | `mock` | `mock` / `openai_compatible` |
| `LLM_BASE_URL` `LLM_MODEL` `LLM_API_KEY` | — | 真实模型接入参数 |
| `MAX_TOOL_STEPS` | `4` | 单 Agent 工具轮次上限 |
| `MAX_ATTEMPTS` | `2` | 输出契约失败后的最大尝试次数 |
| `HUMAN_APPROVAL` | `true` | 素材发布前是否必须人工审批 |
| `COST_PER_1K_IN` `COST_PER_1K_OUT` | `0.00027` `0.0011` | 成本看板单价（美元 / 千 token） |
| `CORS_ORIGINS` | `*` | 逗号分隔；生产填具体域名 |
| `LOG_LEVEL` `LOG_FORMAT` | `INFO` `text` | `LOG_FORMAT=json` 用于日志采集 |
| `REQUEST_ID_HEADER` | `X-Request-ID` | 关联 ID 的头部名 |
| `DOCS_ENABLED` | `true` | 生产建议设为 `false`，关闭 `/docs` |

配置在进程启动时解析一次（`get_settings` 带缓存），**改完必须重启**。

---

## 3. 探针

| 探针 | 端点 | 期望 | 用途 |
|---|---|---|---|
| 存活 | `GET /api/health` | `200` + `{"status":"ok"}` | 进程是否还能响应；**不查数据库**，避免数据库抖动导致容器被误杀 |
| 就绪 | `GET /api/ready` | `200` + `{"status":"ready"}` | 数据库、工具注册表、Agent 与契约注册表是否可用；`503` 时应先摘流量 |

`/api/health` 还会返回**实际生效**的 `provider` / `model`。若配置写错（例如指向真实模型但没有 API Key），
探针仍返回 `200`，但会带上 `provider_error` 字段说明原因——这样告警能看到问题，而不会误判进程死亡。

```bash
curl -s localhost:8000/api/ready | jq
```

---

## 4. 日志与排障

### 4.1 结构

- `LOG_FORMAT=text`：`时间 级别 [request_id] logger: 事件 key=value`，适合本地；
- `LOG_FORMAT=json`：一行一个 JSON，字段固定为 `ts` / `level` / `logger` / `msg` / `request_id` + 业务字段。

### 4.2 关键事件

| 事件 | 级别 | 关键字段 |
|---|---|---|
| `startup` | INFO | 配置摘要（不含密钥） |
| `request` | INFO | `method` `path` `status` `duration_ms` `request_id` |
| `run_created` | INFO | `run_id` `project_id` `mode` |
| `run_started` | INFO | `run_id` `mode` |
| `step_succeeded` | INFO | `agent_id` `artifact_version` `tokens` `checks` `guardrails` `blocked` |
| `run_finished` | INFO | `status` `wall_ms` `tokens` `cost_usd` |
| `run_failed` | WARNING | `run_id` `error` |
| `unhandled_error` | ERROR | 带堆栈 + `request_id` |

### 4.3 定位一次报错

错误响应里带 `request_id`，与响应头 `X-Request-ID` 相同：

```bash
# 前端或用户反馈的报错 {"detail": "...", "request_id": "9f2c1a7b3d10"}
grep '"request_id": "9f2c1a7b3d10"' /var/log/crossborderops.log | jq
```

一条 `request` 日志给出状态码与耗时；如果是运行失败，再按 `run_id` 查 `/api/runs/{id}` 与 `/api/runs/{id}/trace`。

### 4.4 上游链路

网关支持时可通过 `X-Request-ID` 透传整条链路；服务会沿用上游的值而不是另生成。

---

## 5. 数据与备份

### 5.1 SQLite

```bash
# 一致性备份（不要直接 cp 正在写入的库）
sqlite3 data/studio.db ".backup '/backup/studio-$(date +%F).db'"

# 恢复
docker compose down
cp /backup/studio-2026-11-26.db data/studio.db
docker compose up -d
```

- 已开启 WAL：`data/` 下会有 `-wal` / `-shm` 文件，**备份时一并考虑**或用 `.backup`；
- 保留策略按合规要求设定；本仓库不内置自动备份，交给宿主机 cron 或云盘快照。

### 5.2 迁移到 PostgreSQL

```bash
pip install "psycopg[binary]"
export DATABASE_URL="postgresql+psycopg://user:pass@host:5432/crossborderops"
```

- 表由 SQLAlchemy `create_all` 自动创建，适合全新库；
- 已有数据的搬迁需要 `pgloader` 或自写脚本；生产环境建议引入 Alembic 管理变更（Roadmap）；
- 换库后并发写不再需要全局锁，但仍建议单写路径，避免运行状态竞争。

---

## 6. 升级与回滚

1. 打镜像标签：`docker build -t crossborderops-agent-platform:<版本> .`；
2. 备份数据库（见上）；
3. 滚动更新，观察 `/api/ready` 与错误率；
4. 出问题回滚到上一个镜像标签与备份的数据库。

**版本兼容**：改动只增表 / 增列，不做破坏性变更；`/api/meta/catalog` 与 `/api/health` 的 `version` 可用于确认线上版本。

启动时会自动把上次未结束的 `running` / `queued` 运行标记为 `failed`（`stage=interrupted`），
这是刻意设计：宁可让用户重试，也不让运行永远卡在进行中。

---

## 7. 容量与扩容

| 瓶颈 | 现状 | 扩容方向 |
|---|---|---|
| 运行并发 | 每次运行一个线程，受 `--workers` 与机器限制 | 把 `engine.launch` 换成队列（Celery / RQ / SQS），运行状态已落库，改造面小 |
| 数据库写入 | SQLite 单写者 + 全局锁 | 换 PostgreSQL，去掉全局锁 |
| 模型时延 | 串行调用 + 工具轮次 | 03/04/05 已并行；可对同类目做结果缓存 |
| 单机内存 | 运行上下文在内存中 | 上下文已落库（`runs.context`），可做大对象外部化 |

压测建议：先用 `python scripts/smoke_http.py` 验证功能，再用 `hey` / `k6` 对 `/api/projects/{id}/runs` 施加并发，
观察 `/api/metrics/overview` 的成功率与 `wall_ms`。

---

## 8. 常见故障

| 现象 | 先看什么 | 处理 |
|---|---|---|
| `/api/ready` 返回 503 | `checks` 里哪一项为 `false` | `database=false` 检查 `DATABASE_URL` 与文件权限；`tools/agents=false` 说明注册表异常，看启动日志 |
| `/api/health` 有 `provider_error` | 该字段原文 | 通常是 `LLM_API_KEY` 未注入，或 `LLM_PROVIDER` 拼写错误 |
| 运行一直 `queued` | 进程是否重启过 | 启动恢复会把 stale 运行标记 `failed`，重新发起即可 |
| 运行 `failed` 且错误含 `input_contract` | 输入数据 | 竞品少于 3 条、广告数据为空等，属于**预期拒绝**，补数据即可 |
| 运行 `failed` 且错误含 `output_contract` | `/api/runs/{id}` 的步骤 | 模型未按 schema 输出；调 Prompt 或换模型后重试 |
| 卡片显示"运行失败"但日志无异常 | 护栏 `block` | 查 `/api/runs/{id}` 的 `guardrails`，修复禁用词后重跑 |
| 控制台接口 403 / CORS 报错 | `CORS_ORIGINS` | 生产必须填具体域名，不能留 `*` 又开凭证 |
| 磁盘持续增长 | `trace_spans` 与 `step_runs` | 按运行归档或加保留策略；trace 可按 `run_id` 批量删除 |
| 容器反复重启 | `docker logs` + `HEALTHCHECK` | 若健康检查一直失败而应用正常，检查探针路径与端口 |

---

## 9. 上线前检查清单

- [ ] `APP_ENV=production`，`DOCS_ENABLED=false`
- [ ] `LLM_API_KEY` 由 Secret 注入，未写入镜像或仓库
- [ ] `CORS_ORIGINS` 为具体域名，未使用 `*`
- [ ] 网关已终止 TLS 并接入鉴权
- [ ] `HUMAN_APPROVAL=true`（素材发布必须人工过闸）
- [ ] `liveness` / `readiness` 探针已配置，告警接到人
- [ ] 日志已接入采集平台，保留期符合合规要求
- [ ] 数据库备份任务已就绪，并做过一次恢复演练
- [ ] `python -m pytest` 与 `python -m app.eval.runner` 在发布分支全绿
