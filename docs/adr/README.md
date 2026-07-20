# 架构决策记录（ADR）索引

> 本目录存放企业报销智能化系统的架构决策记录（ADR）。根据 `constitution.md` §4.1 要求，重大技术决策须以
> ADR 形式记录，采用「背景 / 方案 / 决策 / 影响」四段式。

## ADR 列表

| ADR 编号 | 标题 | 文件 | 状态 | 日期 |
|----------|------|------|------|------|
| ADR-001 | 选择 DeepSeek 作为 AI 引擎 | [ADR-001-choose-deepseek.md](ADR-001-choose-deepseek.md) | 已采纳 | 2025-07 |
| ADR-002 | 使用 Function Call 结构化输出 | [ADR-002-function-call-structured-output.md](ADR-002-function-call-structured-output.md) | 已采纳 | 2025-07 |
| ADR-003 | 三态校验模型（通过/预警/拦截） | [ADR-003-three-state-validation.md](ADR-003-three-state-validation.md) | 已采纳 | 2025-07 |
| ADR-004 | YAML 配置驱动规则引擎 | [ADR-004-yaml-driven-rules.md](ADR-004-yaml-driven-rules.md) | 已采纳 | 2025-07 |
| ADR-005 | 数据库选型策略（SQLite → MySQL） | [ADR-005-database-strategy.md](ADR-005-database-strategy.md) | 已采纳 | 2025-07 |
| ADR-006 | 票据文件存储策略（本地 → OSS） | [ADR-006-file-storage-strategy.md](ADR-006-file-storage-strategy.md) | 已采纳 | 2025-07 |
| ADR-007 | 引入 LangGraph 智能体编排平台 | [ADR-007-adopt-langgraph.md](ADR-007-adopt-langgraph.md) | 已采纳 | 2026-07 |
| ADR-008 | Agent 注册中心设计 | [ADR-008-agent-registry.md](ADR-008-agent-registry.md) | 已采纳 | 2026-07 |

## 关联文档

- 项目宪章：`../constitution.md` §4.1
- 设计文档：`../design.md` §10（ADR 索引与引用）
- 需求文档：`../requirement.md`
