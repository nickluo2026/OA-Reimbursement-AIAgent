# OA报销AI智能体系统 
— OA Reimbursement AI Agent System

企业日常报销流程依赖人工录入发票、行程单信息，效率低、易出错。本项目基于开源智能体编排平台LangGraph和DeepSeek大模型，使用多个智能体对发票和行程单票据进行智能识别、异常检测、分类限额、合规性校验、审批路由等，提升报销处理效率与合规性。

## 功能架构

| 功能 | 模块 | 说明 |
|------|------|------|
| 编排层 | `orchestrator/graph.py` | LangGraph StateGraph 工作流编排（票据路由 → 发票/行程单双 Agent 分支 → END） |
| **发票智能体** | | |
| 发票 OCR | `tools/tool_ocr_extract.py` | OCR 提取发票全部内容（PyMuPDF + Vision API + DeepSeek Function Call） |
| 发票异常检测 | `tools/tool_anomaly_check.py` | 异常输入检查（规则引擎 + DeepSeek，前置拦截，含金额对比校验） |
| 发票分类限额 | `tools/tool_classify_limit.py` | 费用分类与限额校验（仅金额 > 100 元时执行） |
| 发票查验 | `orchestrator/nodes/verify_node.py` | 发票真伪查验（P1 占位） |
| **行程单智能体** | | |
| 行程单 OCR | `tools/tool_itinerary_ocr.py` | 提取行程汇总信息与明细列表（DeepSeek Vision API） |
| 行程单异常检测 | `tools/tool_itinerary_anomaly.py` | 字段/日期/金额异常检查（规则引擎） |
| 行程合理性校验 | `tools/tool_itinerary_verify.py` | 金额匹配/天数/连续性校验 |
| **通用** | | |
| 审批路由 | `tools/tool_approval_routing.py` | 金额阶梯审批权限路由 |

### 执行顺序（StateGraph 条件边路由）

```
                    ┌─ 行程单 → itinerary_node ────────────────────────→ END
                    │              (OCR → 异常检测 → 合理性校验)
START → 票据类型路由 ┤
                    │              ┌─(OCR失败)─→ END
                    └─ 发票 → ocr_node ┤
                                   │    └─(成功)─→ anomaly_node ─(拦截)─→ END
                                   │                     │
                                   │               ┌─(金额>100)─→ classify_node ─┐
                                   │               │                              ↓
                                   │               └─(小额免审)─→ skip_node ──→ verify_node → END
```

- **票据类型路由**：条件边 `route_by_ticket_type` 根据票据类型分发到发票 Agent 或行程单 Agent
- **OCR 失败**：条件边 `route_after_ocr` 直接路由到 END，提前结束
- **异常检测**为前置拦截：若检出严重异常（字段缺失/重复报销/金额异常/**申请金额不足**等），条件边 `route_after_anomaly` 路由到 END
- **分类限额**仅对发票金额超过 `SMALL_AMOUNT_THRESHOLD`（默认 100 元）的发票执行，小额免审走 `skip_node`
- **行程单 Agent**：`itinerary_node` 内部封装完整的行程单处理流程（OCR → 异常检测 → 合理性校验），拦截后提前结束

## 目录结构

```
.
├── run_web.py                   # Web 服务启动脚本（端口 5001）
├── run_tests.sh                 # 单元测试运行脚本
├── pyproject.toml               # 项目元数据与依赖（PEP 621）
├── pyrightconfig.json           # Pyright 类型检查配置
├── requirements.txt             # 依赖锁定
├── .env.example                 # 环境变量示例（DEEPSEEK_API_KEY 等）
├── LICENSE
│
├── docs/                        # 项目文档
│   ├── constitution.md              # 项目宪章（核心原则与治理规范）
│   ├── design.md                    # 设计文档（架构图与 §16 设计要点）
│   └── requirement.md               # 需求文档（R1.x / R2.x 需求清单）
│
├── skill/                       # 技能包（核心业务逻辑）
│   ├── __init__.py                  # 包入口（导出 run_reimbursement_skill）
│   ├── skill_manifest.yaml          # 技能清单
│   ├── config.py                    # 配置加载（环境变量 + YAML 规则 + SMALL_AMOUNT_THRESHOLD）
│   ├── agent.py                     # 编排入口：构造 State → 委托 graph.run_graph → 转回旧返回结构
│   ├── database.py                  # SQLAlchemy ORM 模型（6张表）与会话管理
│   ├── orchestrator/                # LangGraph 编排层（V1.4 重构）
│   │   ├── __init__.py                  # 导出 build_reimbursement_graph / run_graph / State
│   │   ├── state.py                     # ReimbursementState（TypedDict）+ CheckStatus 枚举
│   │   ├── graph.py                     # StateGraph 构建：节点注册 + 条件边路由 + compile
│   │   ├── registry.py                  # Agent 注册中心（插件化扩展，V1.5+）
│   │   └── nodes/                       # 工作流节点（每个节点封装一个功能工具）
│   │       ├── ocr_node.py                  # 功能1：OCR 提取
│   │       ├── anomaly_node.py              # 功能3：异常检查
│   │       ├── classify_node.py             # 功能2：分类限额
│   │       ├── skip_node.py                 # 小额免审跳过分类
│   │       ├── itinerary_node.py            # 行程单提取（票据类型路由分支）
│   │       └── verify_node.py               # 发票查验（P1 占位）
│   ├── agents/                      # Agent 抽象层（插件化扩展基础）
│   │   ├── __init__.py
│   │   └── base_agent.py                # BaseAgent 抽象基类 + AgentMeta 元信息
│   ├── tools/                       # 功能工具
│   │   ├── tool_ocr_extract.py          # 发票 OCR 提取（PDF文本 + 图片Vision）
│   │   ├── tool_anomaly_check.py        # 发票异常检查（含金额对比）
│   │   ├── tool_classify_limit.py       # 发票分类限额
│   │   ├── tool_itinerary_ocr.py        # 行程单 OCR 提取（汇总 + 明细列表）
│   │   ├── tool_itinerary_anomaly.py    # 行程单异常检测（字段/日期/金额）
│   │   ├── tool_itinerary_verify.py     # 行程合理性校验（金额匹配/天数/连续性）
│   │   └── tool_approval_routing.py     # 审批权限路由
│   ├── schemas/                     # Function Call Schema
│   │   ├── invoice_schema.py
│   │   ├── itinerary_schema.py          # 行程单提取Schema
│   │   ├── classify_schema.py
│   │   └── anomaly_schema.py
│   ├── rules/                       # YAML 规则配置
│   │   ├── category_limits.yaml         # 费用分类限额
│   │   ├── anomaly_rules.yaml           # 异常检测规则
│   │   └── approval_authority.yaml      # 金额阶梯审批规则
│   └── utils/                       # 工具
│       ├── pdf_extractor.py             # PyMuPDF 封装
│       ├── http_client.py               # DeepSeek API 客户端
│       ├── db_store.py                  # 数据库 CRUD 操作
│       └── structured_log.py            # 结构化日志（request_id 追踪）
│
├── web/                          # Flask Web 服务
│   ├── __init__.py
│   ├── app.py                       # Flask 应用（GET / 上传页 + POST /upload 校验）
│   ├── templates/
│   │   ├── index.html                   # 上传页面（支持多文件）
│   │   └── result.html                  # 结果页面
│   └── static/
│       ├── style.css
│       └── upload.js
│
└── tests/                        # 测试
    ├── __init__.py
    ├── conftest.py                  # fixtures & mock 工具
    ├── test_ocr_extract.py          # 发票 OCR 提取测试
    ├── test_anomaly_check.py        # 发票异常检查测试
    ├── test_classify_limit.py       # 发票分类限额测试
    ├── test_itinerary_agent.py      # 行程单 Agent 集成测试
    ├── test_itinerary_verify.py     # 行程合理性校验测试
    ├── test_agent.py                # 发票 Agent 编排集成测试
    └── uat.sh                       # UAT 验收脚本（依赖检查 + 单元测试 + Git 安全检查）
```

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置 API Key

```bash
cp .env.example .env
# 编辑 .env 填入 DEEPSEEK_API_KEY
```

### 3. Web 服务

```bash
python3 run_web.py
# 访问 http://127.0.0.1:5001
```

### 4. 命令行使用

```bash
# 发票校验
python -m skill.agent invoice.pdf 900 2026-06-25 发票

# 行程单校验
python -m skill.agent itinerary.pdf 350 2026-06-25 行程单
```

### 5. 运行测试

```bash
# 方式一：直接 pytest
pytest tests/ -v

# 方式二：使用脚本
./run_tests.sh

# 方式三：UAT 验收（含依赖检查、单元测试、Git 安全检查）
bash tests/uat.sh
```

### 6. 代码调用

```python
from skill import run_reimbursement_skill

# 发票校验
result = run_reimbursement_skill(
    pdf_path="invoice.pdf",
    apply_amount=900,
    apply_date="2026-06-25",
    ticket_type="发票",
    request_id="REQ-001",
    employee_id="E001",
)
print(result["status"])  # "通过" | "预警" | "拦截" | "错误"

# 行程单校验
result = run_reimbursement_skill(
    pdf_path="itinerary.pdf",
    apply_amount=350,
    apply_date="2026-06-25",
    ticket_type="行程单",
)
print(result["status"])
```

## 费用分类限额

| 分类 | 限额（元） |
|------|-----------|
| 餐饮 | 1000 |
| 交通 | 300 |
| 住宿 | 800 |
| 办公 | 500 |
| 差旅 | 1000 |
| 其他 | 200 |

> 限额可在 `rules/category_limits.yaml` 中调整。

## 异常检测规则

| 检测项 | 规则 | 严重程度 |
|--------|------|----------|
| 字段缺失 | 必填字段为空 | 严重（拦截） |
| 格式错误 | 发票号码长度/日期格式不符 | 严重（拦截） |
| 重复报销 | 同一发票号码 30 天内已报销 | 严重（拦截） |
| 票据过期 | 开票日期距申请日超 180 天 | 严重（拦截） |
| 金额异常 | 发票金额超 10000 元 | 严重（拦截） |
| **申请金额不足** | **发票金额 > 申请金额** | **严重（拦截）** |
| 日期异常 | 开票日期晚于申请日 | 严重（拦截） |
| 即将过期 | 票据剩余有效期 < 30 天 | 警告（预警） |

> 规则可在 `rules/anomaly_rules.yaml` 中调整。

## 审批金额阶梯

| 级别 | 金额范围 | 审批人 |
|------|---------|--------|
| 1 | ≤ 5,000 元 | 直属领导 |
| 2 | 5,000 – 20,000 元 | 部门总监 |
| 3 | 20,000 – 100,000 元 | VP/分管副总 |
| 4 | > 100,000 元 | CEO |

> 会签规则：金额 ≥ 50,000 元时需两人会签。配置见 `rules/approval_authority.yaml`。

## 数据库模型

基于 SQLite + SQLAlchemy ORM（当前 V1.4 验证环境），6 张核心表：

| 表名 | 用途 |
|------|------|
| `employee` | 员工信息 |
| `reimbursement` | 报销单主表 |
| `invoice_record` | 发票数据 + OCR 原始 JSON |
| `invoice_history` | 已报销发票历史（防重） |
| `approval_record` | 审批记录（审批人/节点/动作/意见） |
| `ai_check_result` | AI 校验结果（OCR/异常检测/分类限额） |

> 当前使用 SQLite 用于功能验证，目标迁移至 MySQL 8.0 支撑生产级并发与主从架构（见设计文档 §1.3、ADR-005）。

## 设计要点

### 架构设计

- **LangGraph StateGraph 声明式编排**：V1.4 重构后，工作流由 `orchestrator/graph.py` 中的 StateGraph 声明式定义，以条件边路由替代硬编码的线性串联（§16.4）
- **全局共享状态管理**：采用 `ReimbursementState`（TypedDict）作为节点间数据传递载体，由框架自动管理状态合并，消除手工传参的繁琐（§16.3）
- **插件化 Agent 扩展机制**：基于 `agents/base_agent.py` 抽象基类与 `orchestrator/registry.py` 注册中心，新增票据类型仅需注册新 Agent 并扩展路由即可（§16.5）
- **新旧 API 向下兼容**：`graph.py` 通过 `try/except` 机制兼容 langgraph 新版（`add_conditional_edges(START, ...)`）与旧版（`set_conditional_entry_point`）两种调用方式
- **架构决策记录（ADR）**：重大技术决策以 ADR 形式记录，已采纳 ADR-001 至 ADR-008 共 8 条（见设计文档 §10）

### AI 与算法

- **确定性输出**：所有提取任务均设置 Temperature 为 0.0，确保结果可复现（遵循宪章 §3.2）
- **Function Call 优先**：结构化数据通过 `tools` 机制获取，避免依赖正则解析（§3.2）
- **规则引擎 + AI 双重校验**：异常检查优先执行本地确定性规则，再由 DeepSeek 进行语义层面的补充校验
- **申请金额校验**：当发票金额大于申请金额时直接拦截（设计文档 §2.3.1，P0 已实现）

### 工程实践

- **快速失败机制**：OCR 失败或异常拦截时，通过条件边直接路由至 END 节点，避免不必要的 API 调用开销（§2.5）
- **结果可解释性**：每个校验结果均附带明确的文字说明，而非简单的布尔值返回（§2.1）
- **结构化日志追踪**：每个请求携带 `request_id` 贯穿全链路，确保审计可追溯（P2 已实现）
- **OA 适配器层**：目标集成 OA 系统，通过适配器模式封装（`OAAdapter` 抽象接口），切换 OA 系统仅需新增适配器实现（设计文档 §14）

### 功能特性

- **图片 OCR 支持**：JPG/PNG 文件通过 DeepSeek Vision API（base64 编码）进行识别（P1 已实现）
- **行程单智能体**：独立封装行程单处理 Agent（OCR → 异常检测 → 合理性校验），通过票据类型路由分发，支持汇总信息与明细列表的提取
- **多文件并发上传**：前端支持拖拽多选上传，后端并发处理多文件校验（P1 已实现）
- **执行流水线可视化**：前端在校验提交后展示 LangGraph 节点逐步执行的动画（路由 → OCR → 异常检测 → 分类/校验），包含节点名称、调用工具及执行详情
