# OA-Reimbursement-AIAgent 测试用例全集

| 项目 | OA-Reimbursement-AIAgent（报销 AI 智能体系统） |
|------|------|
| 版本 | V1.4 |
| 测试框架 | pytest 8.3.4 |
| Python 版本 | 3.10.4 |
| 执行命令 | `python3 -m pytest tests/ -v --tb=short` |
| 用例总数 | 179 |

> 本文档汇总项目全部 12 个测试文件、179 项测试用例，覆盖功能测试、集成测试、端到端全链路测试、安全测试、数据隐私保护测试、性能测试六大维度。

---

## 目录

- [一、功能测试用例（60 项）](#一功能测试用例60-项)
  - [1.1 OCR 提取（test_ocr_extract.py · 4 项）](#11-ocr-提取test_ocr_extractpy--4-项)
  - [1.2 异常检测（test_anomaly_check.py · 16 项）](#12-异常检测test_anomaly_checkpy--16-项)
  - [1.3 分类限额（test_classify_limit.py · 3 项）](#13-分类限额test_classify_limitpy--3-项)
  - [1.4 行程单工具（test_itinerary_verify.py · 14 项）](#14-行程单工具test_itinerary_verifypy--14-项)
  - [1.5 审批工作流（test_workflow.py · 23 项）](#15-审批工作流test_workflowpy--23-项)
- [二、集成测试用例（54 项）](#二集成测试用例54-项)
  - [2.1 Agent 编排（test_agent.py · 12 项）](#21-agent-编排test_agentpy--12-项)
  - [2.2 行程单 Agent（test_itinerary_agent.py · 8 项）](#22-行程单-agenttest_itinerary_agentpy--8-项)
  - [2.3 审批/财务 API（test_api_approve_finance.py · 18 项）](#23-审批财务-apitest_api_approve_financepy--18-项)
  - [2.4 管理员后台（test_admin.py · 16 项）](#24-管理员后台test_adminpy--16-项)
- [三、端到端全链路测试用例（2 项）](#三端到端全链路测试用例2-项)
- [四、安全测试用例（22 项）](#四安全测试用例22-项)
- [五、数据隐私保护测试用例（22 项）](#五数据隐私保护测试用例22-项)
- [六、性能测试用例（41 项）](#六性能测试用例41-项)

---

## 测试总览

| 测试类型 | 测试文件 | 用例数 |
|---------|---------|-------|
| 功能测试 | test_ocr_extract / test_anomaly_check / test_classify_limit / test_itinerary_verify / test_workflow | 60 |
| 集成测试 | test_agent / test_itinerary_agent / test_api_approve_finance / test_admin | 54 |
| 端到端测试 | test_e2e | 2 |
| 安全测试 | test_admin / test_api_approve_finance（认证授权用例） + 静态检查 | 22 |
| 数据隐私保护 | test_mask_sensitive | 22 |
| 性能测试 | test_performance | 41 |
| **合计** | **12 个文件** | **179** |

---

## 一、功能测试用例（60 项）

> 覆盖三大核心工具（OCR提取/异常检测/分类限额）、行程单工具、审批工作流纯逻辑。

### 1.1 OCR 提取（test_ocr_extract.py · 4 项）

**被测模块**：`skill/tools/tool_ocr_extract.py` → `ocr_extract_invoice()`

| # | 测试类 | 测试方法 | 用例说明 | 预期结果 |
|---|--------|---------|---------|---------|
| 1 | TestOcrExtractInvoice | test_file_not_found | 文件不存在时应返回错误 | 返回字典包含 `_error` 键 |
| 2 | TestOcrExtractInvoice | test_successful_extraction | 正常提取流程（mock PyMuPDF 提取文本 + DeepSeek Function Call） | 返回发票号码 `12345678`、金额 `300.00`，无 `_error` |
| 3 | TestOcrExtractInvoice | test_pdf_read_error | PDF 无文本层（扫描件）时抛 RuntimeError | 返回字典包含 `_error` 键 |
| 4 | TestOcrExtractInvoice | test_deepseek_failure_returns_error | DeepSeek 调用失败（超时/API错误）时返回错误 | 返回字典包含 `_error` 键 |

### 1.2 异常检测（test_anomaly_check.py · 16 项）

**被测模块**：`skill/tools/tool_anomaly_check.py` → `_rule_based_check()` / `_summarize()` / `detect_anomaly()`

#### TestRuleBasedCheck（规则引擎本地检查 · 12 项）

| # | 测试方法 | 用例说明 | 预期结果 |
|---|---------|---------|---------|
| 5 | test_pass_with_normal_data | 正常发票数据（金额300，申请500）应无异常 | `len(anomalies) == 0` |
| 6 | test_field_missing | 缺失必填字段（发票号码/日期/金额/销售方为空）应检测到 | 异常数 ≥ 4，含「字段缺失」 |
| 7 | test_invoice_number_format | 发票号码长度异常（3位，不在8-20范围）应检测到 | 异常类型含「格式错误」 |
| 8 | test_expired_invoice | 过期发票（2025-01-01，距申请日超180天）应检测到 | 异常类型含「过期」 |
| 9 | test_future_invoice_date | 开票日期（07-15）晚于申请日（07-01）应检测到 | 异常类型含「日期异常」 |
| 10 | test_amount_exceeds_threshold | 高金额发票（20000元，超10000阈值）应检测到 | 异常类型含「金额异常」 |
| 11 | test_amount_exceeds_apply_amount | 发票金额300 > 申请金额200应拦截 | 异常类型含「金额异常」，描述含「超过申请金额」 |
| 12 | test_amount_within_apply_amount | 发票金额300 ≤ 申请金额500应通过 | 无「金额异常」类型异常 |
| 13 | test_apply_amount_none_skips_check | 申请金额为空时跳过金额对比 | 无「超过申请金额」异常 |
| 14 | test_duplicate_invoice | 重复发票号码（mock check_duplicate_invoice 返回 True）应检测到 | 异常类型含「重复报销」 |

#### TestSummarize（异常结论判定 · 3 项）

| # | 测试方法 | 用例说明 | 预期结果 |
|---|---------|---------|---------|
| 15 | test_no_anomalies | 无异常时结论为「通过」 | `conclusion == "通过"` |
| 16 | test_severe_anomaly | 严重异常时结论为「拦截」 | `conclusion == "拦截"` |
| 17 | test_warning_only | 仅警告级异常时结论为「预警」 | `conclusion == "预警"` |

#### TestDetectAnomaly（完整异常检测流程 · 3 项）

| # | 测试方法 | 用例说明 | 预期结果 |
|---|---------|---------|---------|
| 18 | test_return_block_on_rule_engine_severe | 规则引擎发现严重异常时直接返回拦截，不调用 DeepSeek | `总体结论 == "拦截"`，DeepSeek 未被调用 |
| 19 | test_call_deepseek_when_rules_pass | 规则检查通过时调用 DeepSeek 做语义补充 | DeepSeek 被调用1次，`总体结论 == "通过"` |
| 20 | test_merge_rule_and_deepseek_results | 规则引擎与 DeepSeek 结果应合并，取更严格结论 | 异常数 ≥ 1，结论为「拦截」或「预警」 |

### 1.3 分类限额（test_classify_limit.py · 3 项）

**被测模块**：`skill/tools/tool_classify_limit.py` → `classify_and_check_limit()`

| # | 测试类 | 测试方法 | 用例说明 | 预期结果 |
|---|--------|---------|---------|---------|
| 21 | TestClassifyAndCheckLimit | test_normal_classify | 正常分类限额校验（差旅类，金额300 ≤ 限额1000） | `费用分类 == "差旅"`，`是否超限 is False` |
| 22 | TestClassifyAndCheckLimit | test_over_limit | 超限发票（餐饮类，金额1200 > 限额300）应预警 | `是否超限 is True`，校验结果含「超出」 |
| 23 | TestClassifyAndCheckLimit | test_deepseek_failure | DeepSeek 调用失败时返回兜底错误信息 | 结果含 `_error` 或 `校验结果` |

### 1.4 行程单工具（test_itinerary_verify.py · 14 项）

**被测模块**：`skill/tools/tool_itinerary_anomaly.py` → `detect_itinerary_anomaly()` + `skill/tools/tool_itinerary_verify.py` → `verify_itinerary()`

#### TestDetectItineraryAnomaly（行程单异常检测 · 6 项）

| # | 测试方法 | 用例说明 | 预期结果 |
|---|---------|---------|---------|
| 24 | test_pass_normal | 正常行程单（3段行程，总金额85.50，申请100）→ 通过 | `总体结论 == "通过"`，`异常明细 == []` |
| 25 | test_missing_fields_block | 字段缺失（开始/结束日期/总金额/行程详情为空）→ 拦截 | `总体结论 == "拦截"`，含「字段缺失」 |
| 26 | test_date_logic_block | 开始日期晚于结束日期（06-09 > 06-08）→ 拦截 | `总体结论 == "拦截"`，描述含「晚于结束日期」 |
| 27 | test_total_amount_exceeds_apply_block | 总金额85.50 > 申请金额50 → 拦截 | `总体结论 == "拦截"`，描述含「超过申请金额」 |
| 28 | test_single_amount_warning | 单笔金额600 > 阈值500 → 预警 | `总体结论 == "预警"`，描述含「超过单笔阈值」 |
| 29 | test_trip_date_after_apply_block | 行程日期（06-15）晚于申请日（06-10）→ 拦截 | `总体结论 == "拦截"` |

#### TestVerifyItinerary（行程单合理性校验 · 8 项）

| # | 测试方法 | 用例说明 | 预期结果 |
|---|---------|---------|---------|
| 30 | test_pass_normal | 正常行程单 → 通过，行程天数2天 | `校验结论 == "通过"`，`行程天数 == 2` |
| 31 | test_amount_mismatch_block | 总金额100与明细合计55不一致 → 拦截 | `校验结论 == "拦截"`，含「不一致」 |
| 32 | test_amount_exceeds_apply_block | 总金额85.50 > 申请金额50 → 拦截 | `校验结论 == "拦截"`，含「超过申请金额」 |
| 33 | test_single_amount_warning | 单笔金额600 > 阈值500 → 预警 | `校验结论 == "预警"`，含「超过阈值」 |
| 34 | test_date_out_of_range_block | 上车时间06-11超出行程日期范围[06-08,06-09] → 拦截 | `校验结论 == "拦截"`，含「不在行程日期范围内」 |
| 35 | test_days_calculation | 行程天数计算（06-01至06-05 = 5天） | `行程天数 == 5` |
| 36 | test_continuity_warning | 行程间隔9天 > 72小时 → 预警 | `校验结论 == "预警"`，含「间隔」 |
| 37 | test_missing_dates_block | 日期缺失 → 拦截 | `校验结论 == "拦截"` |

### 1.5 审批工作流（test_workflow.py · 23 项）

**被测模块**：`skill/workflow.py` → `compute_route()` / `submit_approval()` / `submit_finance()` / 列表查询 / 统计

#### TestComputeRoute（审批路由 · 6 项）

| # | 测试方法 | 用例说明 | 预期结果 |
|---|---------|---------|---------|
| 38 | test_small_amount_level1 | 金额3000 → 1级审批（直属领导） | `审批级别 == 1`，`审批人 == "直属领导"`，`需要会签 is False` |
| 39 | test_mid_amount_level2 | 金额15000 → 2级审批（部门总监） | `审批级别 == 2`，`审批人 == "部门总监"` |
| 40 | test_high_amount_level3 | 金额80000 → 3级审批（VP/分管副总） | `审批级别 == 3`，`审批人 == "VP/分管副总"` |
| 41 | test_countersign_threshold | 金额50000（恰好阈值）→ 触发会签 | `需要会签 is True`，`最少签核人数 == 2` |
| 42 | test_no_countersign_below_threshold | 金额49999（低于阈值）→ 不需会签 | `需要会签 is False` |
| 43 | test_ceo_level4 | 金额120000 → 4级审批（CEO） | `审批级别 == 4`，`审批人 == "CEO"` |

#### TestSubmitApproval（审批决策 · 6 项）

| # | 测试方法 | 用例说明 | 预期结果 |
|---|---------|---------|---------|
| 44 | test_pass | 审批通过 | `workflow_status == "已通过"`，`transferred is False` |
| 45 | test_reject | 审批驳回 | `workflow_status == "已驳回"` |
| 46 | test_transfer_keeps_status | 转审不改变工作流状态，仅留痕 | `workflow_status == "待审批"`，`transferred is True` |
| 47 | test_reject_then_approve_raises | 驳回后不可再审批 | 抛出 `ValueError` |
| 48 | test_unknown_action_raises | 未知审批动作 | 抛出 `ValueError` |
| 49 | test_missing_request_raises | 报销单不存在时审批 | 抛出 `ValueError` |

#### TestCountersign（会签流程 · 2 项）

| # | 测试方法 | 用例说明 | 预期结果 |
|---|---------|---------|---------|
| 50 | test_two_signers_required | 金额60000需两人会签，第一人通过后仍「审批中」，第二人通过后「已通过」 | 第一人：`WS_IN_REVIEW`，`countersign_passed == 1`；第二人：`WS_APPROVED`，`countersign_passed == 2` |
| 51 | test_single_signer_stays_in_review | 金额80000仅一人签核，仍在审批中，不在财务列表 | `list_for_finance() == []` |

#### TestSubmitFinance（财务终审与发放 · 4 项）

| # | 测试方法 | 用例说明 | 预期结果 |
|---|---------|---------|---------|
| 52 | test_archive_requires_approved | 未审批通过不可归档 | 抛出 `ValueError` |
| 53 | test_pay_requires_archived | 未归档不可打款 | 抛出 `ValueError` |
| 54 | test_archive_then_pay | 归档后打款，发票标记已报销（防重） | 归档→`WS_ARCHIVED`，打款→`WS_PAID`，`check_duplicate_invoice("88886666") is True` |
| 55 | test_pay_idempotent_invoice | 重复打款应报错（不可重复打款） | 第二次打款抛出 `ValueError` |

#### TestListQueries（列表查询 · 3 项）

| # | 测试方法 | 用例说明 | 预期结果 |
|---|---------|---------|---------|
| 56 | test_pending_excludes_approved | 待审列表排除已通过，财务列表含已通过 | 审批前 `list_pending()` 有1项；审批后为空，`list_for_finance()` 有1项 |
| 57 | test_list_by_employee | 按员工查询报销单 | EMP-2026 有1项，EMP-OTHER 为空 |
| 58 | test_get_detail | 报销单明细含发票与审批记录 | 明细含1张发票（号码88886666），路由1级；审批后含1条审批记录 |

#### TestStats（统计 · 2 项）

| # | 测试方法 | 用例说明 | 预期结果 |
|---|---------|---------|---------|
| 59 | test_count_decisions_this_month | 本月审批数统计 | 审批后 `after == before + 1` |
| 60 | test_count_by_status | 按状态统计报销单数量 | 待审批1项；审批+归档+打款后已发放1项 |

---

## 二、集成测试用例（54 项）

> 通过 Flask `test_client` 与 mock AI 工具，验证 Web 层路由、StateGraph 编排、API、数据库持久化的端到端协同。

### 2.1 Agent 编排（test_agent.py · 12 项）

**被测模块**：`skill/agent.py` → `run_reimbursement_skill()` + `skill/orchestrator/graph.py` 路由条件

#### TestRunReimbursementSkill（主编排函数 · 7 项）

| # | 测试方法 | 用例说明 | 预期结果 |
|---|---------|---------|---------|
| 61 | test_full_pipeline_pass | 完整流程通过（OCR→异常→分类），分类超限→预警 | `status == "预警"`，ocr/anomaly/classify 结果非 None |
| 62 | test_ocr_error_returns_early | OCR 失败时立即返回错误，不执行后续 | `status == "错误"`，anomaly/classify 未被调用 |
| 63 | test_anomaly_block_skips_classify | 异常拦截时跳过分类限额 | `status == "拦截"`，classify 未被调用 |
| 64 | test_small_amount_skips_classify | 小额发票（50元 ≤ 100）跳过分类限额 | `status == "通过"`，classify_result 含「小额免审」，classify 未被调用 |
| 65 | test_persistence_on_request_id | 有 request_id 时持久化报销单/发票/AI校验结果 | `save_reimbursement` 调用1次，`save_invoice` 调用1次，AI结果保存 ≥ 2次 |
| 66 | test_persistence_error_non_fatal | 持久化异常不影响主流程（mock DB error） | `status == "通过"`（主流程不受影响） |

#### TestGraphRouting（StateGraph 条件路由 · 6 项）

| # | 测试方法 | 用例说明 | 预期结果 |
|---|---------|---------|---------|
| 67 | test_route_after_ocr_error | OCR 失败 → error（提前结束） | `route_after_ocr({final_status: ERROR}) == "error"` |
| 68 | test_route_after_ocr_ok | OCR 成功 → ok（进入异常检测） | `route_after_ocr({final_status: PASS}) == "ok"` |
| 69 | test_route_after_anomaly_block | 异常拦截 → block（提前结束） | `route_after_anomaly({final_status: BLOCK}) == "block"` |
| 70 | test_route_after_anomaly_classify | 金额300 > 100 → classify（执行限额校验） | `route_after_anomaly({ocr_result: {发票金额: 300}}) == "classify"` |
| 71 | test_route_after_anomaly_skip | 金额50 ≤ 100 → skip（小额免审） | `route_after_anomaly({ocr_result: {发票金额: 50}}) == "skip"` |
| 72 | test_route_after_anomaly_boundary | 金额恰好100 → skip（边界值，> 100 才分类） | `route_after_anomaly({ocr_result: {发票金额: 100}}) == "skip"` |

### 2.2 行程单 Agent（test_itinerary_agent.py · 8 项）

**被测模块**：`skill/agents/itinerary_agent.py` → `ItineraryAgent.run()` + `skill/orchestrator/graph.py` → `route_by_ticket_type()`

#### TestItineraryAgent（行程单 Agent 编排 · 7 项）

| # | 测试方法 | 用例说明 | 预期结果 |
|---|---------|---------|---------|
| 73 | test_full_pipeline_pass | 行程单完整流程通过（OCR→异常→合理性校验） | `status == "通过"`，ocr/anomaly/itinerary 结果非 None，classify 为 None |
| 74 | test_ocr_error_returns_early | 行程单 OCR 失败时立即返回错误 | `status == "错误"`，anomaly/verify 未被调用 |
| 75 | test_anomaly_block_skips_verify | 异常拦截时跳过合理性校验 | `status == "拦截"`，verify 未被调用，itinerary_result 为 None |
| 76 | test_verify_warning_returns_warning | 合理性校验预警 → 最终预警 | `status == "预警"`，`itinerary_result["校验结论"] == "预警"` |
| 77 | test_persistence_on_request_id | 有 request_id 时持久化行程单数据 | `save_reimbursement` 调用1次，`save_invoice` 调用1次，AI结果保存 ≥ 2次 |

#### TestItineraryRouting（行程单路由 · 3 项）

| # | 测试方法 | 用例说明 | 预期结果 |
|---|---------|---------|---------|
| 78 | test_route_invoice | 发票类型 → ocr 节点 | `route_by_ticket_type({ticket_type: "发票"}) == "发票"` |
| 79 | test_route_itinerary | 行程单类型 → itinerary 节点 | `route_by_ticket_type({ticket_type: "行程单"}) == "行程单"` |
| 80 | test_route_default | 默认 → 发票 | `route_by_ticket_type({}) == "发票"` |

### 2.3 审批/财务 API（test_api_approve_finance.py · 18 项）

**被测模块**：`web/app.py` 审批/财务路由 + `skill/workflow.py`

#### TestPages（页面渲染 · 4 项）

| # | 测试方法 | 用例说明 | 预期结果 |
|---|---------|---------|---------|
| 81 | test_approve_page_requires_login | 审批页未登录 → 重定向登录 | `status_code == 302` |
| 82 | test_approve_page_renders_for_approver | 审批人登录 → 页面渲染含「待审报销单」 | `status_code == 200`，含「待审报销单」 |
| 83 | test_approve_page_forbidden_for_employee | 员工访问审批页 → 提示无权限 | `status_code == 200`，含「无审批权限」 |
| 84 | test_finance_page_renders_for_finance | 财务登录 → 页面渲染含「待终审报销单」 | `status_code == 200`，含「待终审报销单」 |

#### TestListAndDetail（列表/明细 API · 5 项）

| # | 测试方法 | 用例说明 | 预期结果 |
|---|---------|---------|---------|
| 85 | test_approve_list_requires_login | 待审列表 API 未登录 → 401 | `status_code == 401` |
| 86 | test_approve_list_returns_items | 待审列表返回报销单（含员工姓名映射） | `count == 1`，`employee_name == "张三"` |
| 87 | test_reimbursement_detail | 报销明细含发票信息 | `request_id == "REQ-API-001"`，发票号码 `INV-REQ-API-001` |
| 88 | test_reimbursement_detail_404 | 不存在的报销单 → 404 | `status_code == 404` |
| 89 | test_my_endpoint | 我的报销 API 返回当前用户报销单 | `count == 1` |

#### TestApproveAPI（审批 API · 5 项）

| # | 测试方法 | 用例说明 | 预期结果 |
|---|---------|---------|---------|
| 90 | test_approve_pass | 审批通过 | `workflow_status == "已通过"` |
| 91 | test_approve_reject | 审批驳回 | `workflow_status == "已驳回"` |
| 92 | test_approve_forbidden_for_employee | 员工无权审批 → 403 | `status_code == 403` |
| 93 | test_approve_invalid_action | 非法审批动作 → 400 | `status_code == 400` |
| 94 | test_approve_missing_request | 缺少 request_id → 400 | `status_code == 400` |

#### TestFinanceAPI（财务 API · 4 项）

| # | 测试方法 | 用例说明 | 预期结果 |
|---|---------|---------|---------|
| 95 | test_finance_list_after_approve | 审批通过后财务列表出现该单 | `pending_archive == 1` |
| 96 | test_finance_archive_and_pay | 归档后打款 | 归档→`已归档`，打款→`已发放` |
| 97 | test_finance_pay_before_archive | 未归档直接打款 → 400 | `status_code == 400`，错误含「归档」 |
| 98 | test_finance_forbidden_for_approver | 审批人无权财务操作 → 403 | `status_code == 403` |

### 2.4 管理员后台（test_admin.py · 16 项）

**被测模块**：`web/app.py` 管理员路由 + `skill/utils/admin_store.py`

#### TestAdminPage（页面渲染 · 3 项）

| # | 测试方法 | 用例说明 | 预期结果 |
|---|---------|---------|---------|
| 99 | test_admin_page_requires_login | 管理页未登录 → 重定向 | `status_code == 302` |
| 100 | test_admin_page_renders_for_admin | 管理员登录 → 页面含系统配置/审计日志/用量统计 | `status_code == 200`，含三个 Tab |
| 101 | test_admin_page_forbidden_for_employee | 员工访问管理页 → 提示无权限 | 含「无系统管理权限」 |

#### TestAdminConfig（系统配置 · 4 项）

| # | 测试方法 | 用例说明 | 预期结果 |
|---|---------|---------|---------|
| 102 | test_config_requires_login | 配置 API 未登录 → 401 | `status_code == 401` |
| 103 | test_config_forbidden_for_employee | 员工无权访问配置 → 403 | `status_code == 403` |
| 104 | test_config_returns_schema_and_defaults | 配置 GET 返回 schema + 默认值 | 含 schema/config，`limit_travel_hotel == 1000`，3 个分组 |
| 105 | test_config_save_persists_and_audits | 配置 POST 持久化并写审计日志 | 配置更新成功，审计日志含 `CONFIG_UPDATE` |
| 106 | test_config_reset | 配置重置为默认值 | `limit_travel_hotel == 1000`（恢复默认） |

#### TestAdminAudit（审计日志 · 4 项）

| # | 测试方法 | 用例说明 | 预期结果 |
|---|---------|---------|---------|
| 107 | test_audit_requires_login | 审计 API 未登录 → 401 | `status_code == 401` |
| 108 | test_audit_forbidden_for_employee | 员工无权查看审计 → 403 | `status_code == 403` |
| 109 | test_audit_returns_seeded_logs | 审计日志含预置演示数据 | 日志数 > 0，含 `LOGIN` / `APPROVE` 动作 |
| 110 | test_audit_records_approve_action | 审批动作写入审计日志 | 审计日志含 `APPROVE` 且 target 含报销单号 |

#### TestAdminUsage（用量统计 · 5 项）

| # | 测试方法 | 用例说明 | 预期结果 |
|---|---------|---------|---------|
| 111 | test_usage_requires_login | 用量 API 未登录 → 401 | `status_code == 401` |
| 112 | test_usage_forbidden_for_employee | 员工无权查看用量 → 403 | `status_code == 403` |
| 113 | test_usage_returns_aggregates | 用量返回概览/每日/按类型/明细 | 含 overview/daily/by_type/records，总数一致，含失败记录 |
| 114 | test_usage_filter_by_type | 按调用类型筛选明细 | 所有记录 `call_type == "异常检测"` |

---

## 三、端到端全链路测试用例（2 项）

**被测模块**：`web/app.py /upload` → `skill/agent.py` → `skill/workflow.py` 全链路

> 通过 mock 底层 AI 工具（OCR/异常检测/分类限额），让真实 LangGraph 流水线执行并持久化到数据库，完整验证 `/upload → /api/approve → /api/finance` 端到端链路。

| # | 测试类 | 测试方法 | 用例说明 | 预期结果 |
|---|--------|---------|---------|---------|
| 115 | TestEndToEndFlow | test_employee_to_finance_full_flow | **员工→审批→财务完整流程**：① 员工 EMP-2026 上传发票（358.50元）→ AI 校验通过 → 报销单落库（待审批+AI通过）② 审批领导 APR-001 通过 → 待审列表清空，财务列表出现 ③ 财务 FIN-001 归档 → 已归档 ④ 财务打款 → 已发放，发票标记已报销（防重生效），财务列表清空 | 全链路状态流转正确，报销单落库、发票防重、列表过滤均符合预期 |
| 116 | TestEndToEndFlow | test_employee_reject_flow | **审批驳回流程**：① 员工提交报销 → 待审批 ② 审批领导驳回 → 已驳回 ③ 驳回单不出现在财务待处理列表 | 驳回后工作流终止，不可进入财务流程 |

---

## 四、安全测试用例（22 项）

> 安全测试覆盖认证、授权、越权防护、CSRF、注入防护、RCE防护、文件上传安全、会话安全等。

### 4.1 认证与会话安全（6 项）

| # | 测试方法 | 来源文件 | 用例说明 | 预期结果 |
|---|---------|---------|---------|---------|
| 117 | test_approve_list_requires_login | test_api_approve_finance.py | 待审列表 API 未登录 → 401 | `status_code == 401` |
| 118 | test_config_requires_login | test_admin.py | 系统配置 API 未登录 → 401 | `status_code == 401` |
| 119 | test_audit_requires_login | test_admin.py | 审计日志 API 未登录 → 401 | `status_code == 401` |
| 120 | test_usage_requires_login | test_admin.py | 用量统计 API 未登录 → 401 | `status_code == 401` |
| 121 | test_admin_page_requires_login | test_admin.py | 管理页未登录 → 重定向登录 | `status_code == 302` |
| 122 | test_approve_page_requires_login | test_api_approve_finance.py | 审批页未登录 → 重定向登录 | `status_code == 302` |

> **静态验证**：密码使用 werkzeug `generate_password_hash` 哈希存储（实测算法 scrypt:32768:8:1），非明文；Flask Secret Key 通过环境变量配置，未设置时生成随机临时密钥并警告；Session Cookie HTTPONLY=True / SAMESITE=Lax / SECURE 按 OA_ENV=production 启用。

### 4.2 授权与越权防护（8 项）

| # | 测试方法 | 来源文件 | 用例说明 | 预期结果 |
|---|---------|---------|---------|---------|
| 123 | test_approve_forbidden_for_employee | test_api_approve_finance.py | 员工无权审批 → 403 | `status_code == 403` |
| 124 | test_finance_forbidden_for_approver | test_api_approve_finance.py | 审批人无权财务操作 → 403 | `status_code == 403` |
| 125 | test_config_forbidden_for_employee | test_admin.py | 员工无权访问配置 → 403 | `status_code == 403` |
| 126 | test_audit_forbidden_for_employee | test_admin.py | 员工无权查看审计 → 403 | `status_code == 403` |
| 127 | test_usage_forbidden_for_employee | test_admin.py | 员工无权查看用量 → 403 | `status_code == 403` |
| 128 | test_approve_page_forbidden_for_employee | test_api_approve_finance.py | 员工访问审批页 → 提示无权限 | 含「无审批权限」 |
| 129 | test_admin_page_forbidden_for_employee | test_admin.py | 员工访问管理页 → 提示无权限 | 含「无系统管理权限」 |
| 130 | test_approve_invalid_action | test_api_approve_finance.py | 非法审批动作 → 400 | `status_code == 400` |

> **静态验证**：普通员工数据归属校验 [S-004]（`reb.employee_id != session["account"]` → 403）；审批/财务状态机约束（已驳回不可重复审批、未归档不可打款、已发放不可重复打款）。

### 4.3 CSRF 防护（1 项动态 + 静态验证）

| # | 测试方法 | 来源文件 | 用例说明 | 预期结果 |
|---|---------|---------|---------|---------|
| 131 | test_approve_pass | test_api_approve_finance.py | 审批 API POST 携带正确 CSRF token（TESTING 模式跳过校验） | `workflow_status == "已通过"` |

> **静态验证**：`_csrf_protect()` 拦截所有 POST/PUT/DELETE/PATCH，校验 session token 与表单/请求头一致性；CSRF Token 通过 `secrets.token_hex(32)` 生成 256 位随机值；登录表单单独校验 CSRF。

### 4.4 注入与 RCE 防护（静态扫描 · 4 项）

| # | 检查项 | 扫描方式 | 用例说明 | 预期结果 |
|---|--------|---------|---------|---------|
| 132 | SQL 注入防护 | 代码扫描 `text()/execute()/.raw()` | 全量使用 SQLAlchemy ORM 参数化查询 | 唯一 `text()` 为 ALTER TABLE 迁移 DDL，无用户输入拼接 |
| 133 | XSS 防护 | Jinja2 autoescape 验证 | Flask Jinja2 默认开启自动转义 | `.html` 模板输出自动 HTML 转义 |
| 134 | RCE 防护 | 代码扫描 `eval()/exec()/os.system()/subprocess/` | 无任意代码执行风险 | 0 处命中 |
| 135 | 命令注入防护 | 代码扫描 `shell=True/pickle.load` | 无命令注入风险 | 0 处命中 |

### 4.5 文件上传与调试安全（3 项静态验证）

| # | 检查项 | 验证方式 | 用例说明 | 预期结果 |
|---|--------|---------|---------|---------|
| 136 | 文件类型白名单 | 代码审查 `allowed_file()` | 仅允许 .pdf/.jpg/.jpeg/.png | 扩展名校验生效 |
| 137 | 文件大小限制 | 代码审查 `MAX_CONTENT_LENGTH` | 超过 10MB 返回 413 | `MAX_CONTENT_LENGTH == 10485760` |
| 138 | Debug 模式默认关闭 | 代码审查 `run_web.py` | Werkzeug debugger RCE 风险规避 | `FLASK_DEBUG` 默认 0（关闭） |

> **附加静态验证**：上传文件重命名为 `uuid4().hex + ext`，消除路径遍历风险；AI 校验后 `save_path.unlink()` 删除临时文件；API Key 通过 `os.getenv` 读取，全量扫描硬编码密钥 0 命中。

---

## 五、数据隐私保护测试用例（22 项）

**被测模块**：`skill/utils/mask_sensitive.py` → `mask_phone()` / `mask_tax_id()` / `mask_ip()` / `mask_ocr_result()`

### 5.1 手机号脱敏（TestMaskPhone · 4 项）

| # | 测试方法 | 用例说明 | 预期结果 |
|---|---------|---------|---------|
| 139 | test_normal_phone | 11位手机号脱敏 | `mask_phone("13812345678") == "138****5678"` |
| 140 | test_preserves_prefix3_suffix4 | 保留前3后4，中间星号 | 以 `159` 开头，`1111` 结尾，含 `*` |
| 141 | test_short_string_untouched | 短字符串（<7位）原样返回 | `mask_phone("12345") == "12345"` |
| 142 | test_empty | 空值/None 安全处理 | `mask_phone("") == ""`，`mask_phone(None) == ""` |

### 5.2 税号脱敏（TestMaskTaxId · 4 项）

| # | 测试方法 | 用例说明 | 预期结果 |
|---|---------|---------|---------|
| 143 | test_normal_18digit | 18位统一社会信用代码脱敏 | `mask_tax_id("91110108MA01ABCD23") == "9111**********CD23"`，长度18 |
| 144 | test_preserves_prefix4_suffix4 | 保留前4后4，中间星号 | 以 `9131` 开头，`567X` 结尾，长度18 |
| 145 | test_short_code_untouched | 短代码（<8位）原样返回 | `mask_tax_id("1234567") == "1234567"` |
| 146 | test_empty | 空值/None 安全处理 | `mask_tax_id("") == ""`，`mask_tax_id(None) == ""` |

### 5.3 IP 地址脱敏（TestMaskIp · 7 项）

| # | 测试方法 | 用例说明 | 预期结果 |
|---|---------|---------|---------|
| 147 | test_normal_ipv4 | 普通IPv4脱敏 | `mask_ip("192.168.1.100") == "192.168.***.***"` |
| 148 | test_loopback | 回环地址脱敏 | `mask_ip("127.0.0.1") == "127.0.***.***"` |
| 149 | test_preserves_prefix2 | 保留前两段 | 以 `10.0.` 开头，含 `***` |
| 150 | test_ipv6_masked | IPv6 非IPv4格式统一返回 *** | `mask_ip("2001:db8::1") == "***"` |
| 151 | test_short_string | 短字符串返回 *** | `mask_ip("1.2.3") == "***"` |
| 152 | test_non_numeric_octets | 非数字段返回 *** | `mask_ip("192.168.abc.def") == "***"` |
| 153 | test_empty | 空值/None 安全处理 | `mask_ip("") == ""`，`mask_ip(None) == ""` |

### 5.4 OCR 结果脱敏（TestMaskOcrResult · 7 项）

| # | 测试方法 | 用例说明 | 预期结果 |
|---|---------|---------|---------|
| 154 | test_invoice_tax_ids_not_masked | 发票税号（企业信息）完整展示，不脱敏 | 购买方/销售方税号完整保留，非敏感字段保持原值 |
| 155 | test_itinerary_phone_masked | 行程单手机号脱敏，行程字段不脱敏（审批必需） | `masked["手机号"] == "138****1234"`，行程详情不变 |
| 156 | test_original_data_not_mutated | 脱敏不修改原始数据（数据库完整性） | 原始手机号/税号脱敏后仍完整保留 |
| 157 | test_none_input | None 输入安全处理 | `mask_ocr_result(None) is None` |
| 158 | test_empty_dict | 空字典安全处理 | `mask_ocr_result({}) == {}` |
| 159 | test_no_sensitive_fields | 无敏感字段时原样返回（深拷贝） | `masked == ocr` 且 `masked is not ocr` |
| 160 | test_non_string_sensitive_field_ignored | 敏感字段非字符串时不报错（跳过） | 非字符串手机号保持原样 |

> **附加验证**：API 响应层脱敏（`web/app.py` 返回前 `mask_ocr_result()`），数据库 `ocr_raw` 字段保留完整数据用于审计；审计日志 IP 脱敏（`admin_store._audit_to_dict` 调用 `mask_ip()`）；审计日志仅追加不可删（`AuditLog` 表无 DELETE 接口）。

---

## 六、性能测试用例（41 项）

**被测模块**：全量核心模块 · `tests/test_performance.py`

> 性能测试覆盖 OCR 提取、异常检测、分类限额、行程单校验、数据库 CRUD、审批路由、脱敏处理、StateGraph 编排、Web API、管理后台聚合查询、并发操作等性能关键路径。通过 mock 外部依赖（DeepSeek API），聚焦本地计算与 I/O 性能。
>
> **性能基线**（design.md §8.1）：
> - 单张票据 AI 识别与校验用户感知响应时间 ≤ 10 秒
> - 本地计算（规则/脱敏/路由）≤ 100ms
> - 数据库操作 ≤ 100ms
> - API 响应（不含 AI 调用）≤ 500ms

### 6.1 OCR 提取性能（TestOcrPerformance · 4 项）

**被测模块**：`skill/utils/pdf_extractor.py` + `skill/tools/tool_ocr_extract.py`

| # | 测试方法 | 用例说明 | 预期结果 |
|---|---------|---------|---------|
| 161 | test_pdf_text_extraction_performance | PDF 文本提取性能（5页 PDF，PyMuPDF） | 耗时 ≤ 100ms |
| 162 | test_large_pdf_text_extraction_performance | 大 PDF 文本提取性能（20页） | 耗时 ≤ 500ms |
| 163 | test_image_base64_encoding_performance | 图片 base64 编码性能（50KB 图片） | 耗时 ≤ 100ms |
| 164 | test_large_image_base64_encoding_performance | 大图片 base64 编码性能（500KB 图片） | 耗时 ≤ 500ms |

### 6.2 异常检测性能（TestAnomalyCheckPerformance · 4 项）

**被测模块**：`skill/tools/tool_anomaly_check.py` + `skill/utils/db_store.py`

| # | 测试方法 | 用例说明 | 预期结果 |
|---|---------|---------|---------|
| 165 | test_rule_based_check_performance | 规则引擎检查性能（单张发票，含 YAML 加载） | 耗时 ≤ 100ms |
| 166 | test_rule_based_check_batch_performance | 规则引擎批量检查性能（100次调用，平均 ≤ 10ms/次） | 平均 ≤ 10ms/次 |
| 167 | test_duplicate_check_performance | 重复报销查重性能（100条数据中命中查询） | 耗时 ≤ 100ms |
| 168 | test_duplicate_check_miss_performance | 查重未命中性能（查询不存在的发票号码） | 耗时 ≤ 100ms |

### 6.3 分类限额性能（TestClassifyPerformance · 2 项）

**被测模块**：`skill/tools/tool_classify_limit.py`

| # | 测试方法 | 用例说明 | 预期结果 |
|---|---------|---------|---------|
| 169 | test_classify_with_mock_performance | 分类限额校验性能（mock DeepSeek，聚焦本地计算） | 耗时 ≤ 100ms |
| 170 | test_small_amount_skip_performance | 小额免审性能（≤100元直接跳过，无 AI 调用） | 耗时 ≤ 10ms，`分类依据` 含「小额免审」 |

### 6.4 行程单校验性能（TestItineraryPerformance · 3 项）

**被测模块**：`skill/tools/tool_itinerary_verify.py` + `skill/tools/tool_itinerary_anomaly.py`

| # | 测试方法 | 用例说明 | 预期结果 |
|---|---------|---------|---------|
| 171 | test_verify_large_itinerary_performance | 行程单合理性校验性能（50 条行程明细大数据量） | 耗时 ≤ 100ms |
| 172 | test_anomaly_large_itinerary_performance | 行程单异常检测性能（50 条行程明细） | 耗时 ≤ 100ms |
| 173 | test_verify_normal_itinerary_performance | 正常行程单（3条）合理性校验性能 | 耗时 ≤ 50ms |

### 6.5 数据库 CRUD 性能（TestDatabasePerformance · 7 项）

**被测模块**：`skill/utils/db_store.py` + `skill/workflow.py`

| # | 测试方法 | 用例说明 | 预期结果 |
|---|---------|---------|---------|
| 174 | test_save_reimbursement_performance | 单条报销单写入性能 | 耗时 ≤ 100ms |
| 175 | test_batch_save_reimbursement_performance | 批量写入报销单性能（100条，平均 ≤ 30ms/条） | 总耗时 ≤ 3000ms，平均 ≤ 30ms/条 |
| 176 | test_query_single_reimbursement_performance | 单条报销单查询性能（按主键，100条数据中查询） | 耗时 ≤ 100ms |
| 177 | test_list_pending_performance | 待审列表查询性能（100条数据中筛选待审批） | 耗时 ≤ 100ms，返回100条 |
| 178 | test_list_by_employee_performance | 按员工查询报销单性能（50条数据） | 耗时 ≤ 100ms，返回50条 |
| 179 | test_get_detail_performance | 报销单明细查询性能（含发票/AI结果/审批记录） | 耗时 ≤ 100ms |
| 180 | test_invoice_save_performance | 发票记录写入性能 | 耗时 ≤ 100ms |

### 6.6 审批路由性能（TestApprovalRoutePerformance · 2 项）

**被测模块**：`skill/workflow.py` → `compute_route()` + `skill/tools/tool_approval_routing.py`

| # | 测试方法 | 用例说明 | 预期结果 |
|---|---------|---------|---------|
| 181 | test_route_calculation_performance | 审批路由计算性能（6档金额，含 YAML 加载） | 平均 ≤ 10ms/次 |
| 182 | test_route_batch_performance | 审批路由批量计算性能（1000次） | 平均 ≤ 15ms/次 |

### 6.7 脱敏处理性能（TestMaskPerformance · 3 项）

**被测模块**：`skill/utils/mask_sensitive.py`

| # | 测试方法 | 用例说明 | 预期结果 |
|---|---------|---------|---------|
| 183 | test_mask_ocr_result_performance | OCR 结果脱敏性能（含20条商品明细 + 手机号/税号） | 耗时 ≤ 10ms，手机号正确脱敏 |
| 184 | test_mask_batch_performance | 批量脱敏性能（1000次） | 平均 ≤ 1ms/次 |
| 185 | test_mask_large_ocr_performance | 大 OCR 结果脱敏性能（含100条商品明细） | 耗时 ≤ 20ms |

### 6.8 StateGraph 编排性能（TestGraphPerformance · 4 项）

**被测模块**：`skill/orchestrator/graph.py` + `skill/agent.py` + `skill/agents/itinerary_agent.py`

| # | 测试方法 | 用例说明 | 预期结果 |
|---|---------|---------|---------|
| 186 | test_graph_build_performance | StateGraph 构建与编译性能 | 耗时 ≤ 500ms |
| 187 | test_graph_invoke_performance | StateGraph 执行性能（mock 工具，聚焦编排开销） | 耗时 ≤ 1000ms |
| 188 | test_graph_invoke_batch_performance | StateGraph 批量执行性能（50次，平均 ≤ 50ms/次） | 平均 ≤ 50ms/次 |
| 189 | test_itinerary_graph_invoke_performance | 行程单 Agent 图执行性能（mock 工具） | 耗时 ≤ 1000ms |

### 6.9 Web API 性能（TestWebApiPerformance · 5 项）

**被测模块**：`web/app.py` 各路由

| # | 测试方法 | 用例说明 | 预期结果 |
|---|---------|---------|---------|
| 190 | test_approve_list_api_performance | 待审列表 API 响应时间（20条数据） | 耗时 ≤ 500ms |
| 191 | test_reimbursement_detail_api_performance | 报销明细 API 响应时间 | 耗时 ≤ 500ms |
| 192 | test_my_api_performance | 我的报销 API 响应时间（20条数据） | 耗时 ≤ 500ms |
| 193 | test_login_page_render_performance | 登录页渲染性能 | 耗时 ≤ 500ms |
| 194 | test_approve_page_render_performance | 审批页渲染性能 | 耗时 ≤ 500ms |

### 6.10 管理后台聚合查询性能（TestAdminQueryPerformance · 4 项）

**被测模块**：`web/app.py` 管理员路由 + `skill/utils/admin_store.py`

| # | 测试方法 | 用例说明 | 预期结果 |
|---|---------|---------|---------|
| 195 | test_audit_log_list_performance | 审计日志列表查询性能（100条日志） | 耗时 ≤ 500ms |
| 196 | test_usage_overview_performance | 用量统计概览查询性能（100条调用记录聚合） | 耗时 ≤ 500ms |
| 197 | test_usage_filter_performance | 用量明细筛选性能（按调用类型筛选） | 耗时 ≤ 500ms |
| 198 | test_config_read_performance | 系统配置读取性能 | 耗时 ≤ 500ms |

### 6.11 并发性能（TestConcurrentPerformance · 3 项）

**被测模块**：数据库并发读取 + 脱敏并发处理

| # | 测试方法 | 用例说明 | 预期结果 |
|---|---------|---------|---------|
| 199 | test_concurrent_query_performance | 并发查询报销单性能（10线程 × 50查询，100条数据） | 耗时 ≤ 3000ms，全部命中 |
| 200 | test_concurrent_list_pending_performance | 并发查询待审列表性能（5线程 × 10次，50条数据） | 耗时 ≤ 3000ms，每次返回50条 |
| 201 | test_concurrent_mask_performance | 并发脱敏性能（10线程 × 100次） | 耗时 ≤ 2000ms，全部正确脱敏 |

---

## 附录：测试环境与配置

### A.1 测试配置（pyproject.toml）

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = ["test_*.py"]
python_classes = ["Test*"]
python_functions = ["test_*"]
addopts = "-v --tb=short"
filterwarnings = [
    "ignore:builtin type .* has no __module__ attribute:DeprecationWarning",
]
```

### A.2 测试 Fixtures（conftest.py）

| Fixture | 用途 |
|---------|------|
| `sample_invoice_data` | 标准发票样本（正常数据） |
| `sample_invoice_missing_fields` | 缺失必填字段的发票数据 |
| `sample_invoice_high_amount` | 高金额发票（超异常阈值） |
| `sample_invoice_expired` | 过期发票 |
| `sample_anomaly_result_pass` | 异常检测通过结果 |
| `sample_anomaly_result_block` | 异常检测拦截结果 |
| `sample_classify_result` | 分类限额结果 |
| `sample_itinerary_data` | 标准行程单样本（3段行程） |
| `sample_itinerary_missing_fields` | 缺失必填字段的行程单 |
| `sample_itinerary_amount_mismatch` | 金额不匹配的行程单 |
| `sample_itinerary_anomaly_pass` | 行程单异常检测通过结果 |
| `sample_itinerary_anomaly_block` | 行程单异常检测拦截结果 |
| `sample_itinerary_verify_pass` | 行程单合理性校验通过结果 |
| `fresh_db` | 干净数据库（重建全部表） |
| `sample_reimbursement` | 创建一条待审批报销单（含发票） |
| `client` | Flask 测试客户端 |

### A.3 测试数据库隔离

```python
# tests/conftest.py
_TEST_DB_PATH = os.path.join(tempfile.gettempdir(), "oa_test_agent.db")
os.environ.setdefault("OA_DB_PATH", _TEST_DB_PATH)
if os.path.exists(_TEST_DB_PATH):
    os.unlink(_TEST_DB_PATH)  # 清理上次残留
```

> 测试统一使用独立临时数据库 `oa_test_agent.db`，避免污染真实 `oa_agent.db`。

---

## 附录：按测试文件汇总

| 测试文件 | 测试类型 | 用例数 |
|---------|---------|-------|
| test_ocr_extract.py | 功能 | 4 |
| test_anomaly_check.py | 功能 | 16 |
| test_classify_limit.py | 功能 | 3 |
| test_itinerary_verify.py | 功能 | 14 |
| test_workflow.py | 功能 | 23 |
| test_agent.py | 集成 | 12 |
| test_itinerary_agent.py | 集成 | 8 |
| test_api_approve_finance.py | 集成 | 18 |
| test_admin.py | 集成 | 16 |
| test_e2e.py | 端到端 | 2 |
| test_mask_sensitive.py | 隐私 | 22 |
| test_performance.py | 性能 | 41 |
| **合计** | — | **179** |

---

*文档生成于 2026-07-16 · pytest 8.3.4 · Python 3.10.4*
