# 变更记录

遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 与 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### 变更

- 文档体系按产品文档重写：README 增加产品定位、能力矩阵、运行指标、版本与支持
- 新增 [版本与发布](docs/RELEASE.md)，定义版本号规则、分支策略、发布检查清单与回滚
- 新增 PR 模板与 Issue 模板（缺陷 / 需求），统一变更的提交口径

## [0.7.0] - 2026-11-26

生产化加固版本：补齐部署、可观测性与安全基线，业务链路与 0.6.0 保持一致。

### 新增

- **可观测性**：结构化日志（`text` / `json` 两种格式），每条日志自动带 `request_id`，
  可在一次运行的 LLM 调用、工具调用、护栏事件之间串联；日志级别与格式均由环境变量控制。
- **请求中间件**：请求关联 ID（可透传上游 `X-Request-ID`）、统一访问日志、基础安全响应头
  （`X-Content-Type-Options` / `X-Frame-Options` / `Referrer-Policy` / `Permissions-Policy`）。
- **统一错误契约**：所有错误响应带 `request_id`；`422` 结构化返回逐字段校验原因；未预期异常只回 `request_id`，不回显堆栈。
- **探针分离**：`/api/health` 为存活探针（不依赖数据库），`/api/ready` 为就绪探针
  （校验数据库、工具注册表、Agent 与契约注册表），`/api/health` 同时给出实际生效的 provider 与配置错误原因。
- **部署资产**：多阶段 `Dockerfile`（非 root 用户、内置健康检查）、`docker-compose.yml`、`.dockerignore`、`Makefile`。
- **工程规范**：`pyproject.toml` 补全元数据与 ruff / pytest / coverage 配置，拆分 `requirements-dev.txt`，
  新增 `.editorconfig`、`SECURITY.md`、`CHANGELOG.md`。
- **测试**：新增平台级用例，覆盖健康/就绪探针、request_id 透传与回写、安全响应头、错误响应契约、时间戳格式。

### 变更

- 时间字段统一输出 UTC ISO8601（`Z` 结尾），不再依赖数据库返回的 naive datetime。
- CORS 来源改为可配置（`CORS_ORIGINS`），默认 `*` 仅用于本地开发，并修正通配来源与
  凭证同时开启的不兼容配置。
- `/api/demo/curl` 按请求实际地址生成命令，新增 `base_url` 字段。
- `/api/meta/catalog` 增加 `version` 字段。
- 工作流引擎在创建、开始、单步成功、结束、失败时输出结构化日志，并移除一处无用查询。
- 文档中不再出现面向特定场景的表述，全部改为面向部署与运维的说明。

### 修复

- README 的 CI 徽章指向错误的仓库地址。
- 示例中的克隆目录名与仓库名不一致。
- `Prompt` 注释引用了不存在的设计文档路径。
- 评测运行器的异常捕获存在冗余分支。

## [0.6.0] - 2026-01-18

首个可运行版本。

### 新增

- 7 个 SOP Agent（市场分析 / 产品策略 / Listing 创意 / 主图与详情图 / 短视频 / 广告优化 / 评论分析）。
- 13 个确定性工具：CR4/HHI/价格带波谷、单位经济与 SKU 阶梯、标题与参数校验、
  图片像素校验、视频时序校验、广告漏斗与预算再分配、评论八类归因。
- 双契约（输入 `extra="allow"` + 输出 `extra="forbid"`）与三级护栏（repair / block / warn）。
- 工作流引擎：`full_pipeline` / `creative_only` / `single` / `diagnose` / `closed_loop` 五条路径，
  人工审批闸门、崩溃恢复、版本化产物与 FeedbackEvent 回流。
- 统一 trace（llm / tool / guardrail）、成本归因、平台指标、10 条 golden 评测（含 2 条负向用例）。
- 零依赖 Web 控制台与 CLI 端到端脚本。
