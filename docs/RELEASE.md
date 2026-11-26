# 版本与发布

本文定义 CrossBorderOps 的版本号规则、分支策略、发布检查清单与回滚方式。

## 版本号规则

采用语义化版本 `MAJOR.MINOR.PATCH`：

| 位 | 递增条件 | 例子 |
|---|---|---|
| `MAJOR` | 破坏性变更：接口契约、配置项语义、数据库结构 | 移除字段、改默认审批行为 |
| `MINOR` | 向下兼容的新能力 | 新增 Agent、新增工具、新增端点 |
| `PATCH` | 向下兼容的修复 | 修 ffprobe 解析、修日志字段 |

预发布用 `-rc.N` 后缀（如 `0.8.0-rc.1`），用于灰度验证；`main` 上的正式版本不带后缀。

## 分支与合并

- `main` 始终可发布，任何提交都必须能通过 CI；
- 功能与修复走短分支 `feat/*`、`fix/*`，通过 PR 合入，使用 squash merge 保持线性历史；
- 每个 PR 必须满足：[PR 模板](../.github/PULL_REQUEST_TEMPLATE.md) 中的自检项、CI 全绿、至少一次 review；
- 面向用户的变更必须在 `CHANGELOG.md` 的 `Unreleased` 段落有对应条目。

## 发布检查清单

- [ ] `CHANGELOG.md`：把 `Unreleased` 收敛为版本号 + 日期
- [ ] 版本号同步：`pyproject.toml`、`app/__init__.py`、镜像 tag（`Dockerfile` / `docker-compose.yml` / `Makefile`）
- [ ] `python -m ruff check app tests scripts` 通过
- [ ] `python -m pytest` 全绿（含真实 ffmpeg 相关用例）
- [ ] `python -m app.eval.runner` 得分 ≥ 0.9（改过 Prompt 或工具时必跑）
- [ ] 构建镜像并按 [运维手册](OPERATIONS.md) 完成一次冒烟
- [ ] 数据库结构有变更时：备份 + 回滚演练已完成
- [ ] 发布说明写明：兼容性、配置项变化、升级步骤

## 兼容性承诺

- `PATCH`：不改接口路径、字段、错误码与配置项语义；
- `MINOR`：只新增；需要废弃的字段或配置会先标记 `deprecated`，跨 `MAJOR` 才移除；
- HTTP 状态码与错误响应体（`detail` / `request_id`）视为对外契约，变更走 `MAJOR`；
- 环境变量只增不改语义；新增项必须同时写进 `.env.example` 与运维手册。

## 回滚

1. 镜像回滚到上一个 tag（`docker compose up -d` 指定镜像即可）；
2. 数据库结构不兼容时，用发布前的备份恢复（见运维手册「数据与备份」）；
3. 回滚后在 `/api/ready` 与指标页确认服务恢复，再排查原因。

启动时的状态收敛保证回滚后不会留下「永远 running」的任务：进程启动会把中断的运行标记为 failed，可重试。
