# 贡献指南

这个项目的目标是：**把跨境运营的经验，变成可校验、可回流的 Agent 工作流**。
欢迎补类目、补平台规则、补真实案例，以及修工具口径的 bug。

## 本地开发

```bash
pip install -r requirements.txt
python -m pytest -q            # 57 个测试
python -m app.eval.runner      # 10 条 golden 评测
python -m uvicorn app.main:app --port 8000
```

默认 `LLM_PROVIDER=mock`，不需要 API key。接入真实模型见 `.env.example`。

## 提 PR 前必须过

1. `python -m pytest -q` 全绿；
2. `python -m app.eval.runner` 得分 ≥ 0.9（不允许为了过新用例而放宽旧断言）；
3. 改了 Prompt / 工具口径，在 PR 描述里写清「改前 vs 改后」的评测差异；
4. 新增工具必须有单测，且参数模型保持 `extra="forbid"`。

## 新增一个 Agent（四步）

1. `app/agents/contracts.py`：定义输入/输出模型，注册进 `CONTRACTS`；
2. `app/agents/prompts.py`：写五段式系统 Prompt（角色 / 任务 / 输入契约 / 输出契约 / 自检）；
3. `app/agents/registry.py`：加 `build_input` + `evidence` + 工具白名单（必要时加 `gates`）；
4. 需要新算法时，在 `app/tools/` 加一个 `@tool` 纯函数并补单测。

引擎、API、控制台、评测都按注册表驱动，不需要改。

## 数据与合规

- 提交案例必须**脱敏**：不含真实店铺名、账号、买家信息、未授权品牌词；
- 不实现刷单、诱导评价、参数虚标等平台禁止的做法；
- 平台费率、字符上限等规则以平台后台实时规则为准，本仓库内置的是演示快照。

## 提交信息

用 `feat:` / `fix:` / `docs:` / `test:` / `refactor:` 前缀，一句话说清改了什么、为什么。
