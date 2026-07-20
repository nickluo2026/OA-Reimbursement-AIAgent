# ADR-002：使用 Function Call 结构化输出

- 状态：已采纳
- 日期：2025-07
- 关联：constitution.md §3.2；design.md §3.2–3.5、§8.2
- 作者：架构评审（EA）

## 背景

票据 OCR 提取、异常检测、分类限额校验均需要可被规则引擎与自动填单可靠消费的结构化数据。
若采用自由格式 Prompt（模型返回自然语言段落），下游必须用正则/启发式解析，存在字段错位、
格式漂移、边界解析失败等风险，违背宪章 §2.1「金额校验零容忍」原则。

## 方案

- **A. 自由格式 Prompt + 正则解析**：实现简单，但解析脆弱、维护成本高、不可控。
- **B. Function Call / Tool Calling 结构化输出**（强制 JSON Schema 约束）：输出契约由 Schema 定义，
  模型按 `tools` 返回结构化对象，字段缺失可由 `required` 强制校验。
- **C. 微调专用抽取模型**：准确率高但训练/标注成本高、迭代慢、与现有 DeepSeek 体系割裂。

## 决策

选择 **方案 B**。理由：

1. DeepSeek 原生支持 Function Call，输出稳定 JSON，准确率可控；
2. 配合 `temperature=0.0` 实现确定性输出，消除随机性（宪章 §3.2）；
3. Schema 即契约，与 LangGraph 节点入参/出参天然契合（ADR-007/008）；
4. 必填字段由 `required` 强制，缺失即失败，转人工录入兜底。

## 影响

- 新增 `skill/schemas/` 下各票据 Schema 文件（invoice / itinerary / anomaly / classify）；
- 所有提取类任务 `temperature` 固定 0.0；
- 解析层消失，校验逻辑直接消费结构体；
- 风险：Schema 变更需同步代码与 Prompt → 通过 Schema 版本化管控。
