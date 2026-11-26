<!-- 提交前请先阅读 CONTRIBUTING.md；标题建议使用 feat: / fix: / docs: / refactor: / test: / chore: 前缀 -->

## 变更内容

<!-- 一句话说明改了什么；为什么这样改 -->

## 变更类型

- [ ] feat：新增能力
- [ ] fix：缺陷修复
- [ ] docs：文档
- [ ] refactor：重构（不改变行为）
- [ ] test：测试
- [ ] chore：工程 / 依赖 / CI

## 自检

- [ ] `python -m ruff check app tests scripts` 通过
- [ ] `python -m pytest` 全绿
- [ ] 改了对外接口 → 同步 `docs/API.md`
- [ ] 改了配置项 → 同步 `.env.example` 与 `docs/OPERATIONS.md`
- [ ] 面向用户的变更 → `CHANGELOG.md` 已加条目
- [ ] 改了 Prompt 或工具口径 → 附 golden eval 改前 / 改后结果

## 影响面

<!-- 接口 / 数据库 / 配置 / 部署方式，若无请写「无」 -->

## 相关 Issue

<!-- Closes #123 -->
