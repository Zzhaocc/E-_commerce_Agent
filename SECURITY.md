# 安全策略

## 支持的版本

| 版本 | 是否接收安全修复 |
|---|---|
| 0.7.x | 是 |
| ≤ 0.6.x | 否（请先升级） |

## 报告漏洞

**不要**通过公开 issue 报告安全问题。请使用仓库的
[Security Advisory](https://github.com/Zzhaocc/E-_commerce_Agent/security/advisories/new)
私下提交，或在 issue 里只描述"需要私下沟通"，由维护者建立私密通道。

请在报告里包含：影响版本、复现步骤、影响面（是否可读到他人数据 / 是否可触发未授权运行）、
以及期望的修复方向。我们会在 7 天内确认，并在修复发布后致谢（除非要求匿名）。

## 安全设计说明

本仓库是**单租户部署形态**的应用，安全边界如下：

| 项目 | 现状 | 部署建议 |
|---|---|---|
| 身份认证 | 内置接口**不带鉴权**，默认只监听本机 | 对外暴露前置于网关 / Ingress，接入 OIDC 或 API Key |
| 密钥管理 | 只从环境变量读取 `LLM_API_KEY`，不落库、不写日志 | 用 K8s Secret / Vault 注入，不要提交 `.env` |
| 传输安全 | 由部署层负责 | 网关终止 TLS，强制 HTTPS |
| 输入校验 | Pydantic 双契约 + 工具参数 `extra="forbid"` | 网关再加一层请求体大小限制 |
| 输出安全 | 三级护栏拦截禁用词、参数冲突、像素/时序违规 | 发布包生成前强制人工审批（`HUMAN_APPROVAL=true`） |
| 数据存储 | SQLite 文件默认落在 `data/`，已加入 `.gitignore` | 生产换 PostgreSQL 并开启备份与行级权限 |
| 日志 | 不打印密钥；请求级 `request_id` 便于审计 | 日志采集后限制访问权限，按合规要求设定保留期 |

## 依赖与供应链

- 依赖在 `requirements.txt` / `requirements-dev.txt` 中约束了主版本上限，避免不兼容升级；
- CI 在 Python 3.11 / 3.13 上跑测试与评测，升级依赖必须先过 CI；
- 建议在仓库设置里开启 Dependabot alerts 与 secret scanning。

## 不在范围内

- 平台（Shopee / Mercado Livre 等）账号凭据的保管与轮换 —— 本项目不接收、不存储凭据；
- 第三方模型服务方的数据处理条款 —— 请按实际合规要求选择供应商与区域。
