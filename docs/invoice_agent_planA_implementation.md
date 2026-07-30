# 方案A 实施与验收：异常检测 ‖ 分类限额 并行化

报销单 `057e8a288e574003` 的发票智能体优化（方案A）。

## 改造内容

由于当前 LangGraph 版本不支持条件边的列表扇出（`add_conditional_edges` 的 path_map 不支持列表、且无 `Send` API），
并行改为在 **`anomaly_node` 内部用线程池并发**执行两次 LLM 调用，图结构保持线性，所有现有测试/patch 点不受影响。

### 1. `skill/orchestrator/nodes/anomaly_node.py`
- 发票金额 `> SMALL_AMOUNT_THRESHOLD`（100 元）时，用 `ThreadPoolExecutor(max_workers=2)` 并发执行
  `detect_anomaly`（异常检测）与 `classify_and_check_limit`（分类限额）。
- 金额 ≤ 100 时仅跑异常检测（小额免审，分类限额交由 `skip_node`）。
- 拦截（`总体结论 == 拦截`）时仍投机跑完分类限额但**结果丢弃**，置 `final_status=BLOCK` 提前结束。

### 2. `skill/orchestrator/graph.py`
- 新增合并节点 `post_check_node`：异常检测 ‖ 分类限额 并行完成后统一落库与定级。
  - 拦截优先：保持 BLOCK，且**不写「分类限额」记录**。
  - 未拦截：据分类限额结论写「分类限额」记录并定级（预警/通过）。
- 新增 `route_post_check`：拦截→END，否则→END（发票查验步骤已移除）。

### 3. `skill/orchestrator/nodes/classify_node.py`
- 纯工具封装：保留 `classify_and_check_limit` 供 `anomaly_node` 并行调用；落库/定级下沉到 `post_check`。
- 保留 `save_ai_check_result`/`update_ai_status` 导入，以便测试 `patch.object`。

## 时序对比（基于真实数据 `057e8a288e574003`）

| 阶段 | 串行（改造前） | 并行（方案A） |
|---|---|---|
| OCR 提取 | 6.73s | 6.73s |
| 异常检测 | 8.27s | 8.27s（与分类限额重叠） |
| 分类限额 | 5.61s | 5.61s（与异常检测重叠） |
| **端到端** | **≈20.67s** | **≈15.00s（▼27%）** |

关键路径由「三者之和」降为「OCR + max(异常, 分类) + 查验」。

## 验收测试

`pytest tests/ -q` → **254 passed**。与方案A直接相关的：

- `test_parallel_anomaly_classify_for_large_amount`（新增）：用带 `sleep(0.30)` 的桩函数模拟两次 LLM 调用，
  断言两节点几乎同时启动、重叠区间 `< 0.5s`（远小于串行 `0.6s`），证明**真正并行**。
- `test_anomaly_block_discards_classify_result`（更新）：拦截时分类限额被投机调用但结论被丢弃，最终状态仍为 `拦截`。
- `test_route_after_ocr_routing`（更新）：金额>100→并行分支、≤100→串行、失败→提前结束。
- `test_full_pipeline_pass` / `test_small_amount_skips_classify` / `test_e2e`：行为不变，全部通过。
- 拦截时不写「分类限额」AI 记录、跳过查验的语义保持不变。

## 真实运行验证（移动端全链路，2026-07-30）

新建一张发票（金额 2159.42 元 > 100，触发并行分支），通过移动端 `login → /upload`
走**真实 DeepSeek 全链路**（OCR 文本管线 → 异常检测 ‖ 分类限额并行），
从 `api_usage` 按单号重建精确时间线：

| 调用类型 | latency | 相对首调区间 | tokens(in/out) |
|---|---|---|---|
| 发票OCR提取（文本管线 Function Call） | 4.29s | 0.00s → 4.29s | 1205/678 |
| 分类限额 | 6.19s | 4.37s → 10.56s | 899/677 |
| 异常检测 | 8.64s | 4.36s → 13.00s | 1040/1037 |

- **AI检测全链路真实耗时（OCR启动 → 末调结束）= 13.00s**
- `/upload` 端到端（含影像渲染/落库）= **13.39s**
- 累计纯推理（Σlatency，串行下界）= 19.13s
- **并行证据**：异常/分类启动间隔 0.01s、重叠 6.19s；若串行需 14.84s，实际并行关键路径 8.64s → **节省 ≈6.19s**

与串行基线（057e 同口径：OCR+异常+分类+查验）相比，方案A 把异常/分类这对由
「8.6+6.2=14.8s」压缩到「max=8.6s」，**端到端由 ≈19.4s（串行下界）降到 13.4s，降幅约 31%**。

### 运行环境约束与修复
- 本部署 DeepSeek 模型（`deepseek-v4-flash`/`deepseek-v4-pro`）为**纯文本模型**，
  Vision 调用返回 `400 (unknown variant image_url)`，故位图 PNG 无法做视觉 OCR；真实图片 OCR
  须依赖本地 OCR 引擎（tesseract）。本次用**带中文文本层的 PDF** 走生产真实 OCR 路线
  （PyMuPDF 抽文本 → DeepSeek 文本管线，即 057e 单据的「发票OCR提取」路径），票据图片
  `scripts/test_assets/new_invoice_real_*.png` 为交付物。
- **修复方案A 引入的缺陷**：并行线程未传播 `request_id` 上下文变量，导致异常/分类阶段的
  `api_usage` 记录 `request_id` 为空、无法按单号关联，并触发落库告警。已在 `anomaly_node.py`
  用 `copy_context()` + 每任务 `ctx.copy().run(...)` 修复（每个 worker 持有独立上下文副本，
  避免同一 `Context` 被两线程同时进入）。修复后 `test_agent.py` 13 项全过。

## 备注

并行机制已通过桩函数与**真实 DeepSeek 调用**双重验证：桩函数证明并行启动重叠 `< 0.5s`；
真实运行实测 AI 检测全链路 **13.0s**（端到端 13.4s），较改造前串行基线 ≈19.4s **减少约 31%**，
与方案A 估计一致。
