# ADR-001：选择 DeepSeek 作为 AI 引擎

- 状态：已采纳
- 日期：2025-07
- 关联：constitution.md §3.2；design.md §1.3、§3.1、§8.2；README
- 作者：架构评审（EA）

## 背景

报销系统核心能力为票据 OCR 字段提取 + 费用语义分类（NLP），需要稳定的 AI 引擎支撑。
需在 DeepSeek / 通义千问 / 文心一言 / 本地 OCR（PaddleOCR）等候选间选型，权衡 OCR 能力、
结构化输出稳定性、数据合规与成本。

## 方案

| 选项 | 优势 | 劣势 |
|------|------|------|
| DeepSeek | Function Call 原生支持；OCR + NLP 一体化；成本低 | API 限流风险；合规审查 |
| 通义千问 | 阿里生态；结构化输出 | OCR 专项能力弱于 DeepSeek |
| 本地 OCR（PaddleOCR） | 无外部依赖；数据不出域 | 准确率低；需额外 NLP 引擎 |
| GPT-4o | OCR 能力强 | 成本高；数据出境风险 |

## 决策

选择 **DeepSeek**，理由：

1. Function Call 输出稳定 JSON Schema，准确率可控；
2. OCR + NLP 一体化，减少串行调用延迟；
3. 成本可控。

## 影响

- 需管理 API Key 轮询与多 Key 容灾（design.md §7.2、§12.4）；
- 需设计降级人工录入方案（constitution.md §2.2）；
- 后续可评估多模型混合架构以分散限流风险；
- 风险：API 限流/不可用 → 多 Key 轮询 + 本地 OCR 备选 + 人工录入兜底（design.md §9.1）。
