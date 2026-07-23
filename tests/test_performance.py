"""性能测试用例

覆盖性能关键路径，验证各核心操作的响应时间符合 design.md §8.1 性能基线：
  - 单张票据 AI 识别与校验用户感知响应时间 ≤ 10 秒
  - 本地计算（规则/脱敏/路由）≤ 100ms
  - 数据库操作 ≤ 100ms
  - API 响应（不含 AI 调用）≤ 500ms

性能测试通过 mock 外部依赖（DeepSeek API），聚焦本地计算与 I/O 性能。
"""

from __future__ import annotations

import os
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

# ── 性能阈值（毫秒）──
THRESHOLD_LOCAL_CALC_MS = 100  # 本地纯计算（规则/脱敏/路由）
THRESHOLD_BATCH_PER_CALL_MS = 20  # 批量检查单次要（含 runner 波动余量）
THRESHOLD_DB_OP_MS = 100  # 单次数据库操作
THRESHOLD_DB_BATCH_MS = 3000  # 批量数据库操作（100条）
THRESHOLD_API_MS = 500  # API 响应（不含 AI）
THRESHOLD_GRAPH_MS = 1000  # StateGraph 执行（mock 工具）
THRESHOLD_GRAPH_BUILD_MS = 500  # 图构建
THRESHOLD_CONCURRENT_MS = 3000  # 并发操作


# ═══════════════════════════════════════════════
# 辅助：生成测试数据
# ═══════════════════════════════════════════════
def _generate_test_pdf(num_pages: int = 5) -> str:
    """用 PyMuPDF 生成多页测试 PDF"""
    import fitz

    doc = fitz.open()
    for i in range(num_pages):
        page = doc.new_page()
        page.insert_text(
            (50, 72),
            (
                f"发票测试页面 {i + 1}\n"
                f"发票号码: {12345678 + i}\n"
                f"开票日期: 2026-06-01\n"
                f"发票金额: 300.00\n"
                f"销售方名称: 测试酒店管理有限公司\n"
                f"购买方名称: 测试科技有限公司\n"
            ),
        )
    path = tempfile.mktemp(suffix=".pdf")
    doc.save(path)
    doc.close()
    return path


def _generate_test_image(size_kb: int = 50) -> str:
    """生成测试图片文件（用于 base64 编码性能测试）"""
    path = tempfile.mktemp(suffix=".png")
    with open(path, "wb") as f:
        f.write(os.urandom(size_kb * 1024))
    return path


def _make_large_itinerary(trip_count: int = 50) -> dict:
    """生成大量行程明细的行程单（用于大数据量校验性能测试）"""
    trips = []
    for i in range(trip_count):
        trips.append(
            {
                "序号": i + 1,
                "车型": "经济型",
                "上车时间": f"2026-06-08 {10 + i % 12:02d}:{i % 60:02d}",
                "城市": "北京",
                "起点": f"地点{i}",
                "终点": f"地点{i + 1}",
                "里程_公里": str(5 + i),
                "金额_元": f"{20 + i}.00",
            }
        )
    total = sum(20 + i for i in range(trip_count))
    return {
        "申请日期": "2026-06-10",
        "行程开始日期": "2026-06-08",
        "行程结束日期": "2026-06-09",
        "总金额_元": str(total),
        "行程详情": trips,
    }


def _elapsed_ms(start: float) -> float:
    """计算耗时（毫秒）"""
    return (time.perf_counter() - start) * 1000


# ═══════════════════════════════════════════════
# 1. OCR 提取性能
# ═══════════════════════════════════════════════
class TestOcrPerformance:
    """OCR 提取相关性能测试"""

    def test_pdf_text_extraction_performance(self):
        """PDF 文本提取性能（5页 PDF，PyMuPDF）"""
        from skill.utils.pdf_extractor import extract_pdf_text

        pdf_path = _generate_test_pdf(num_pages=5)
        try:
            # 预热
            extract_pdf_text(pdf_path)

            start = time.perf_counter()
            text = extract_pdf_text(pdf_path)
            elapsed = _elapsed_ms(start)
        finally:
            os.unlink(pdf_path)

        assert len(text) > 0
        assert (
            elapsed < THRESHOLD_LOCAL_CALC_MS
        ), f"PDF 提取耗时 {elapsed:.1f}ms > {THRESHOLD_LOCAL_CALC_MS}ms"

    def test_large_pdf_text_extraction_performance(self):
        """大 PDF 文本提取性能（20页）"""
        from skill.utils.pdf_extractor import extract_pdf_text

        pdf_path = _generate_test_pdf(num_pages=20)
        try:
            start = time.perf_counter()
            text = extract_pdf_text(pdf_path)
            elapsed = _elapsed_ms(start)
        finally:
            os.unlink(pdf_path)

        assert len(text) > 0
        assert elapsed < 500, f"大 PDF 提取耗时 {elapsed:.1f}ms > 500ms"

    def test_image_base64_encoding_performance(self):
        """图片 base64 编码性能（50KB 图片）"""
        from skill.tools.tool_ocr_extract import _encode_image_base64

        img_path = _generate_test_image(size_kb=50)
        try:
            start = time.perf_counter()
            data_uri = _encode_image_base64(img_path)
            elapsed = _elapsed_ms(start)
        finally:
            os.unlink(img_path)

        assert data_uri.startswith("data:image/png;base64,")
        assert (
            elapsed < THRESHOLD_LOCAL_CALC_MS
        ), f"base64 编码耗时 {elapsed:.1f}ms > {THRESHOLD_LOCAL_CALC_MS}ms"

    def test_large_image_base64_encoding_performance(self):
        """大图片 base64 编码性能（500KB 图片）"""
        from skill.tools.tool_ocr_extract import _encode_image_base64

        img_path = _generate_test_image(size_kb=500)
        try:
            start = time.perf_counter()
            _encode_image_base64(img_path)
            elapsed = _elapsed_ms(start)
        finally:
            os.unlink(img_path)

        assert elapsed < 500, f"大图片 base64 编码耗时 {elapsed:.1f}ms > 500ms"


# ═══════════════════════════════════════════════
# 2. 异常检测性能
# ═══════════════════════════════════════════════
class TestAnomalyCheckPerformance:
    """异常检测相关性能测试"""

    def test_rule_based_check_performance(self, sample_invoice_data):
        """规则引擎检查性能（单张发票，应 < 100ms）"""
        from skill.tools.tool_anomaly_check import _rule_based_check

        # 预热（首次调用会加载 YAML）
        _rule_based_check(sample_invoice_data, apply_amount=500, apply_date="2026-06-10")

        start = time.perf_counter()
        anomalies = _rule_based_check(
            sample_invoice_data, apply_amount=500, apply_date="2026-06-10"
        )
        elapsed = _elapsed_ms(start)

        assert isinstance(anomalies, list)
        assert (
            elapsed < THRESHOLD_LOCAL_CALC_MS
        ), f"规则检查耗时 {elapsed:.1f}ms > {THRESHOLD_LOCAL_CALC_MS}ms"

    def test_rule_based_check_batch_performance(self, sample_invoice_data):
        """规则引擎批量检查性能（100次调用，平均应 < 20ms/次）"""
        from skill.tools.tool_anomaly_check import _rule_based_check

        count = 100
        # 预热
        _rule_based_check(sample_invoice_data, apply_amount=500, apply_date="2026-06-10")

        start = time.perf_counter()
        for _ in range(count):
            _rule_based_check(sample_invoice_data, apply_amount=500, apply_date="2026-06-10")
        elapsed = _elapsed_ms(start)

        avg_ms = elapsed / count
        assert (
            avg_ms < THRESHOLD_BATCH_PER_CALL_MS
        ), f"批量检查平均耗时 {avg_ms:.2f}ms/次 > {THRESHOLD_BATCH_PER_CALL_MS}ms"

    def test_duplicate_check_performance(self, fresh_db):
        """重复报销查重性能（数据库查询）"""
        from skill.utils.db_store import check_duplicate_invoice, save_invoice

        # 预置 100 条发票
        for i in range(100):
            save_invoice(
                {"发票号码": f"DUP-{i:04d}", "发票金额": 100 + i, "销售方名称": "X"},
                f"REQ-DUP-{i}",
                "",
            )

        start = time.perf_counter()
        result = check_duplicate_invoice("DUP-0050", 30)
        elapsed = _elapsed_ms(start)

        assert result is True
        assert elapsed < THRESHOLD_DB_OP_MS, f"查重耗时 {elapsed:.1f}ms > {THRESHOLD_DB_OP_MS}ms"

    def test_duplicate_check_miss_performance(self, fresh_db):
        """查重未命中性能（查询不存在的发票号码）"""
        from skill.utils.db_store import check_duplicate_invoice, save_invoice

        for i in range(100):
            save_invoice(
                {"发票号码": f"EXIST-{i:04d}", "发票金额": 100, "销售方名称": "X"},
                f"REQ-EXIST-{i}",
                "",
            )

        start = time.perf_counter()
        result = check_duplicate_invoice("NOT-EXIST-9999", 30)
        elapsed = _elapsed_ms(start)

        assert result is False
        assert (
            elapsed < THRESHOLD_DB_OP_MS
        ), f"查重未命中耗时 {elapsed:.1f}ms > {THRESHOLD_DB_OP_MS}ms"


# ═══════════════════════════════════════════════
# 3. 分类限额性能
# ═══════════════════════════════════════════════
class TestClassifyPerformance:
    """分类限额校验性能测试"""

    @patch("skill.tools.tool_classify_limit.call_deepseek_function")
    def test_classify_with_mock_performance(self, mock_ds, sample_invoice_data):
        """分类限额校验性能（mock DeepSeek，聚焦本地计算）"""
        from skill.tools.tool_classify_limit import classify_and_check_limit

        mock_ds.return_value = {
            "费用分类": "差旅",
            "分类依据": "住宿费",
            "发票金额": 300,
            "分类限额": 1000,
            "是否超限": False,
            "校验结果": "通过",
        }

        # 预热
        classify_and_check_limit(sample_invoice_data)

        start = time.perf_counter()
        result = classify_and_check_limit(sample_invoice_data)
        elapsed = _elapsed_ms(start)

        assert result["费用分类"] == "差旅"
        assert (
            elapsed < THRESHOLD_LOCAL_CALC_MS
        ), f"分类限额耗时 {elapsed:.1f}ms > {THRESHOLD_LOCAL_CALC_MS}ms"

    def test_small_amount_skip_performance(self):
        """小额免审性能（≤100元直接跳过，无 AI 调用）"""
        from skill.tools.tool_classify_limit import classify_and_check_limit

        invoice = {"发票金额": 50}

        start = time.perf_counter()
        result = classify_and_check_limit(invoice)
        elapsed = _elapsed_ms(start)

        assert "小额免审" in result["分类依据"]
        assert elapsed < 10, f"小额免审耗时 {elapsed:.1f}ms > 10ms"


# ═══════════════════════════════════════════════
# 4. 行程单校验性能
# ═══════════════════════════════════════════════
class TestItineraryPerformance:
    """行程单校验性能测试（大数据量）"""

    def test_verify_large_itinerary_performance(self):
        """行程单合理性校验性能（50 条行程明细）"""
        from skill.tools.tool_itinerary_verify import verify_itinerary

        itinerary = _make_large_itinerary(trip_count=50)

        # 预热
        verify_itinerary(itinerary, apply_amount=10000)

        start = time.perf_counter()
        result = verify_itinerary(itinerary, apply_amount=10000)
        elapsed = _elapsed_ms(start)

        assert "校验结论" in result
        assert (
            elapsed < THRESHOLD_LOCAL_CALC_MS
        ), f"50条行程校验耗时 {elapsed:.1f}ms > {THRESHOLD_LOCAL_CALC_MS}ms"

    def test_anomaly_large_itinerary_performance(self):
        """行程单异常检测性能（50 条行程明细）"""
        from skill.tools.tool_itinerary_anomaly import detect_itinerary_anomaly

        itinerary = _make_large_itinerary(trip_count=50)

        start = time.perf_counter()
        result = detect_itinerary_anomaly(itinerary, apply_amount=10000, apply_date="2026-06-10")
        elapsed = _elapsed_ms(start)

        assert "总体结论" in result
        assert (
            elapsed < THRESHOLD_LOCAL_CALC_MS
        ), f"50条行程异常检测耗时 {elapsed:.1f}ms > {THRESHOLD_LOCAL_CALC_MS}ms"

    def test_verify_normal_itinerary_performance(self, sample_itinerary_data):
        """正常行程单（3条）合理性校验性能"""
        from skill.tools.tool_itinerary_verify import verify_itinerary

        start = time.perf_counter()
        result = verify_itinerary(sample_itinerary_data, apply_amount=100)
        elapsed = _elapsed_ms(start)

        assert result["校验结论"] == "通过"
        assert elapsed < 50, f"3条行程校验耗时 {elapsed:.1f}ms > 50ms"


# ═══════════════════════════════════════════════
# 5. 数据库 CRUD 性能
# ═══════════════════════════════════════════════
class TestDatabasePerformance:
    """数据库操作性能测试"""

    def test_save_reimbursement_performance(self, fresh_db):
        """单条报销单写入性能"""
        from skill.utils.db_store import save_reimbursement

        start = time.perf_counter()
        save_reimbursement(
            request_id="REQ-PERF-001",
            employee_id="EMP-2026",
            apply_amount=358.50,
            apply_date="2026-07-16",
            reason="性能测试",
            expense_category="差旅",
        )
        elapsed = _elapsed_ms(start)

        assert (
            elapsed < THRESHOLD_DB_OP_MS
        ), f"单条写入耗时 {elapsed:.1f}ms > {THRESHOLD_DB_OP_MS}ms"

    def test_batch_save_reimbursement_performance(self, fresh_db):
        """批量写入报销单性能（100条，平均 < 30ms/条）"""
        from skill.utils.db_store import save_reimbursement

        count = 100
        start = time.perf_counter()
        for i in range(count):
            save_reimbursement(
                request_id=f"REQ-BATCH-{i:04d}",
                employee_id="EMP-2026",
                apply_amount=100.0 + i,
                apply_date="2026-07-16",
                reason="批量性能测试",
            )
        elapsed = _elapsed_ms(start)

        avg_ms = elapsed / count
        assert avg_ms < 30, f"批量写入平均耗时 {avg_ms:.2f}ms/条 > 30ms"
        assert (
            elapsed < THRESHOLD_DB_BATCH_MS
        ), f"批量写入总耗时 {elapsed:.1f}ms > {THRESHOLD_DB_BATCH_MS}ms"

    def test_query_single_reimbursement_performance(self, fresh_db):
        """单条报销单查询性能（按主键）"""
        from skill.utils.db_store import get_reimbursement, save_reimbursement

        for i in range(100):
            save_reimbursement(
                request_id=f"REQ-Q-{i:04d}",
                employee_id="EMP-2026",
                apply_amount=100 + i,
                apply_date="2026-07-16",
            )

        start = time.perf_counter()
        result = get_reimbursement("REQ-Q-0050")
        elapsed = _elapsed_ms(start)

        assert result is not None
        assert (
            elapsed < THRESHOLD_DB_OP_MS
        ), f"主键查询耗时 {elapsed:.1f}ms > {THRESHOLD_DB_OP_MS}ms"

    def test_list_pending_performance(self, fresh_db):
        """待审列表查询性能（100条数据中筛选待审批）"""
        from skill import workflow as wf
        from skill.utils.db_store import save_invoice, save_reimbursement

        for i in range(100):
            save_reimbursement(
                request_id=f"REQ-LIST-{i:04d}",
                employee_id="EMP-2026",
                apply_amount=100 + i,
                apply_date="2026-07-16",
            )
            save_invoice(
                {"发票号码": f"INV-LIST-{i:04d}", "发票金额": 100 + i, "销售方名称": "X"},
                f"REQ-LIST-{i:04d}",
                "",
            )

        start = time.perf_counter()
        items = wf.list_pending()
        elapsed = _elapsed_ms(start)

        assert len(items) == 100
        assert (
            elapsed < THRESHOLD_DB_OP_MS
        ), f"待审列表查询耗时 {elapsed:.1f}ms > {THRESHOLD_DB_OP_MS}ms"

    def test_list_by_employee_performance(self, fresh_db):
        """按员工查询报销单性能"""
        from skill import workflow as wf
        from skill.utils.db_store import save_reimbursement

        for i in range(50):
            save_reimbursement(
                request_id=f"REQ-EMP-{i:04d}",
                employee_id="EMP-2026",
                apply_amount=100 + i,
                apply_date="2026-07-16",
            )

        start = time.perf_counter()
        items = wf.list_by_employee("EMP-2026")
        elapsed = _elapsed_ms(start)

        assert len(items) == 50
        assert (
            elapsed < THRESHOLD_DB_OP_MS
        ), f"按员工查询耗时 {elapsed:.1f}ms > {THRESHOLD_DB_OP_MS}ms"

    def test_get_detail_performance(self, sample_reimbursement):
        """报销单明细查询性能（含发票/AI结果/审批记录）"""
        from skill import workflow as wf

        start = time.perf_counter()
        detail = wf.get_detail(sample_reimbursement)
        elapsed = _elapsed_ms(start)

        assert detail is not None
        assert (
            elapsed < THRESHOLD_DB_OP_MS
        ), f"明细查询耗时 {elapsed:.1f}ms > {THRESHOLD_DB_OP_MS}ms"

    def test_invoice_save_performance(self, fresh_db):
        """发票记录写入性能"""
        from skill.utils.db_store import save_invoice

        ocr = {
            "发票类型": "增值税普通发票",
            "发票号码": "INV-PERF-001",
            "发票金额": 358.50,
            "销售方名称": "测试酒店",
            "开票日期": "2026-07-10",
        }

        start = time.perf_counter()
        save_invoice(ocr, "REQ-INV-PERF", "")
        elapsed = _elapsed_ms(start)

        assert (
            elapsed < THRESHOLD_DB_OP_MS
        ), f"发票写入耗时 {elapsed:.1f}ms > {THRESHOLD_DB_OP_MS}ms"


# ═══════════════════════════════════════════════
# 6. 审批路由性能
# ═══════════════════════════════════════════════
class TestApprovalRoutePerformance:
    """审批路由计算性能测试"""

    def test_route_calculation_performance(self):
        """审批路由计算性能（单次）"""
        from skill import workflow as wf

        # 预热（首次加载 YAML）
        wf.compute_route(3000)

        amounts = [100, 3000, 15000, 50000, 80000, 120000]
        start = time.perf_counter()
        for amt in amounts:
            wf.compute_route(amt)
        elapsed = _elapsed_ms(start)

        avg_ms = elapsed / len(amounts)
        assert avg_ms < 50, f"路由计算平均耗时 {avg_ms:.2f}ms > 50ms"

    def test_route_batch_performance(self):
        """审批路由批量计算性能（1000次）"""
        from skill.tools.tool_approval_routing import determine_approval_route

        determine_approval_route(1000)  # 预热

        count = 1000
        start = time.perf_counter()
        for i in range(count):
            determine_approval_route(100 + i * 100)
        elapsed = _elapsed_ms(start)

        avg_ms = elapsed / count
        assert avg_ms < 15, f"批量路由平均耗时 {avg_ms:.3f}ms/次 > 15ms"


# ═══════════════════════════════════════════════
# 7. 脱敏处理性能
# ═══════════════════════════════════════════════
class TestMaskPerformance:
    """敏感数据脱敏性能测试"""

    def test_mask_ocr_result_performance(self):
        """OCR 结果脱敏性能（单次）"""
        from skill.utils.mask_sensitive import mask_ocr_result

        ocr = {
            "发票号码": "12345678",
            "发票金额": 300.00,
            "手机号": "13812345678",
            "购买方税号": "91110108MA01ABCD23",
            "销售方税号": "91110108MA02YYYYY",
            "商品明细": [{"项目名称": "住宿费", "金额": "283.02"} for _ in range(20)],
        }

        start = time.perf_counter()
        masked = mask_ocr_result(ocr)
        elapsed = _elapsed_ms(start)

        assert masked["手机号"] == "138****5678"
        assert elapsed < 10, f"脱敏耗时 {elapsed:.1f}ms > 10ms"

    def test_mask_batch_performance(self):
        """批量脱敏性能（1000次）"""
        from skill.utils.mask_sensitive import mask_ocr_result

        ocr = {
            "手机号": "13812345678",
            "购买方税号": "91110108MA01ABCD23",
            "发票金额": 300,
        }

        count = 1000
        start = time.perf_counter()
        for _ in range(count):
            mask_ocr_result(ocr)
        elapsed = _elapsed_ms(start)

        avg_ms = elapsed / count
        assert avg_ms < 1, f"批量脱敏平均耗时 {avg_ms:.3f}ms/次 > 1ms"

    def test_mask_large_ocr_performance(self):
        """大 OCR 结果脱敏性能（含 100 条商品明细）"""
        from skill.utils.mask_sensitive import mask_ocr_result

        ocr = {
            "手机号": "13812345678",
            "商品明细": [
                {"项目名称": f"项目{i}", "金额": f"{i}.00", "税率": "6%"} for i in range(100)
            ],
        }

        start = time.perf_counter()
        mask_ocr_result(ocr)
        elapsed = _elapsed_ms(start)

        assert elapsed < 20, f"大 OCR 脱敏耗时 {elapsed:.1f}ms > 20ms"


# ═══════════════════════════════════════════════
# 8. StateGraph 编排性能
# ═══════════════════════════════════════════════
class TestGraphPerformance:
    """LangGraph StateGraph 编排性能测试"""

    def test_graph_build_performance(self):
        """StateGraph 构建与编译性能"""
        from skill.orchestrator.graph import build_reimbursement_graph

        # 预热
        build_reimbursement_graph()

        start = time.perf_counter()
        app = build_reimbursement_graph()
        elapsed = _elapsed_ms(start)

        assert app is not None
        assert (
            elapsed < THRESHOLD_GRAPH_BUILD_MS
        ), f"图构建耗时 {elapsed:.1f}ms > {THRESHOLD_GRAPH_BUILD_MS}ms"

    @patch("skill.orchestrator.nodes.classify_node.classify_and_check_limit")
    @patch("skill.orchestrator.nodes.anomaly_node.detect_anomaly")
    @patch("skill.orchestrator.nodes.ocr_node.ocr_extract_invoice")
    def test_graph_invoke_performance(self, mock_ocr, mock_anomaly, mock_classify):
        """StateGraph 执行性能（mock 工具，聚焦编排开销）"""
        from skill.agent import run_reimbursement_skill

        mock_ocr.return_value = {
            "发票号码": "12345678",
            "发票金额": 300,
            "开票日期": "2026-06-01",
            "购买方名称": "XX公司",
            "销售方名称": "YY公司",
        }
        mock_anomaly.return_value = {"总体结论": "通过", "异常明细": [], "检查摘要": "无异常"}
        mock_classify.return_value = {
            "费用分类": "差旅",
            "分类依据": "住宿费",
            "发票金额": 300,
            "分类限额": 1000,
            "是否超限": False,
            "校验结果": "通过",
        }

        start = time.perf_counter()
        result = run_reimbursement_skill(
            pdf_path="test.pdf",
            apply_amount=500,
            apply_date="2026-06-10",
        )
        elapsed = _elapsed_ms(start)

        assert result["status"] in ("通过", "预警")
        assert elapsed < THRESHOLD_GRAPH_MS, f"图执行耗时 {elapsed:.1f}ms > {THRESHOLD_GRAPH_MS}ms"

    @patch("skill.orchestrator.nodes.classify_node.classify_and_check_limit")
    @patch("skill.orchestrator.nodes.anomaly_node.detect_anomaly")
    @patch("skill.orchestrator.nodes.ocr_node.ocr_extract_invoice")
    def test_graph_invoke_batch_performance(self, mock_ocr, mock_anomaly, mock_classify):
        """StateGraph 批量执行性能（50次，平均 < 50ms/次）"""
        from skill.agent import run_reimbursement_skill

        mock_ocr.return_value = {
            "发票号码": "12345678",
            "发票金额": 300,
            "开票日期": "2026-06-01",
            "购买方名称": "XX公司",
            "销售方名称": "YY公司",
        }
        mock_anomaly.return_value = {"总体结论": "通过", "异常明细": [], "检查摘要": "无异常"}
        mock_classify.return_value = {
            "费用分类": "差旅",
            "发票金额": 300,
            "分类限额": 1000,
            "是否超限": False,
            "校验结果": "通过",
        }

        count = 50
        # 预热
        run_reimbursement_skill(pdf_path="test.pdf", apply_amount=500, apply_date="2026-06-10")

        start = time.perf_counter()
        for _ in range(count):
            run_reimbursement_skill(pdf_path="test.pdf", apply_amount=500, apply_date="2026-06-10")
        elapsed = _elapsed_ms(start)

        avg_ms = elapsed / count
        assert avg_ms < 50, f"批量执行平均耗时 {avg_ms:.2f}ms/次 > 50ms"

    @patch("skill.agents.itinerary_agent.verify_itinerary")
    @patch("skill.agents.itinerary_agent.detect_itinerary_anomaly")
    @patch("skill.agents.itinerary_agent.ocr_extract_itinerary")
    def test_itinerary_graph_invoke_performance(
        self,
        mock_ocr,
        mock_anomaly,
        mock_verify,
        sample_itinerary_data,
        sample_itinerary_anomaly_pass,
        sample_itinerary_verify_pass,
    ):
        """行程单 Agent 图执行性能"""
        from skill.agent import run_reimbursement_skill

        mock_ocr.return_value = sample_itinerary_data
        mock_anomaly.return_value = sample_itinerary_anomaly_pass
        mock_verify.return_value = sample_itinerary_verify_pass

        start = time.perf_counter()
        result = run_reimbursement_skill(
            pdf_path="itinerary.pdf",
            apply_amount=100,
            apply_date="2026-06-10",
            ticket_type="行程单",
        )
        elapsed = _elapsed_ms(start)

        assert result["status"] == "通过"
        assert (
            elapsed < THRESHOLD_GRAPH_MS
        ), f"行程单图执行耗时 {elapsed:.1f}ms > {THRESHOLD_GRAPH_MS}ms"


# ═══════════════════════════════════════════════
# 9. Web API 性能
# ═══════════════════════════════════════════════
class TestWebApiPerformance:
    """Web API 响应时间性能测试"""

    def test_approve_list_api_performance(self, client, fresh_db):
        """待审列表 API 响应时间"""
        from skill.utils.db_store import save_invoice, save_reimbursement

        for i in range(20):
            save_reimbursement(
                request_id=f"REQ-API-{i:04d}",
                employee_id="EMP-2026",
                apply_amount=100 + i,
                apply_date="2026-07-16",
            )
            save_invoice(
                {"发票号码": f"INV-API-{i:04d}", "发票金额": 100 + i, "销售方名称": "X"},
                f"REQ-API-{i:04d}",
                "",
            )

        with client.session_transaction() as sess:
            sess["account"] = "APR-001"
            sess["role"] = "approver"
            sess["name"] = "李总"

        start = time.perf_counter()
        resp = client.get("/api/approve/list")
        elapsed = _elapsed_ms(start)

        assert resp.status_code == 200
        assert elapsed < 800, f"待审列表 API 耗时 {elapsed:.1f}ms > 800ms"

    def test_reimbursement_detail_api_performance(self, client, fresh_db):
        """报销明细 API 响应时间"""
        from skill.utils.db_store import save_invoice, save_reimbursement

        save_reimbursement(
            request_id="REQ-DETAIL-PERF",
            employee_id="EMP-2026",
            apply_amount=358.50,
            apply_date="2026-07-16",
            reason="API性能测试",
            expense_category="差旅",
        )
        save_invoice(
            {"发票号码": "INV-DETAIL-PERF", "发票金额": 358.50, "销售方名称": "X"},
            "REQ-DETAIL-PERF",
            "",
        )

        with client.session_transaction() as sess:
            sess["account"] = "APR-001"
            sess["role"] = "approver"
            sess["name"] = "李总"

        start = time.perf_counter()
        resp = client.get("/api/reimbursement/REQ-DETAIL-PERF")
        elapsed = _elapsed_ms(start)

        assert resp.status_code == 200
        assert elapsed < THRESHOLD_API_MS, f"明细 API 耗时 {elapsed:.1f}ms > {THRESHOLD_API_MS}ms"

    def test_my_api_performance(self, client, fresh_db):
        """我的报销 API 响应时间"""
        from skill.utils.db_store import save_reimbursement

        for i in range(20):
            save_reimbursement(
                request_id=f"REQ-MY-{i:04d}",
                employee_id="EMP-2026",
                apply_amount=100 + i,
                apply_date="2026-07-16",
            )

        with client.session_transaction() as sess:
            sess["account"] = "EMP-2026"
            sess["role"] = "employee"
            sess["name"] = "张三"

        start = time.perf_counter()
        resp = client.get("/api/my")
        elapsed = _elapsed_ms(start)

        assert resp.status_code == 200
        assert (
            elapsed < THRESHOLD_API_MS
        ), f"我的报销 API 耗时 {elapsed:.1f}ms > {THRESHOLD_API_MS}ms"

    def test_login_page_render_performance(self, client):
        """登录页渲染性能"""
        start = time.perf_counter()
        resp = client.get("/login")
        elapsed = _elapsed_ms(start)

        assert resp.status_code == 200
        assert elapsed < THRESHOLD_API_MS, f"登录页渲染耗时 {elapsed:.1f}ms > {THRESHOLD_API_MS}ms"

    def test_approve_page_render_performance(self, client, fresh_db):
        """审批页渲染性能"""
        with client.session_transaction() as sess:
            sess["account"] = "APR-001"
            sess["role"] = "approver"
            sess["name"] = "李总"

        start = time.perf_counter()
        resp = client.get("/approve")
        elapsed = _elapsed_ms(start)

        assert resp.status_code == 200
        assert elapsed < THRESHOLD_API_MS, f"审批页渲染耗时 {elapsed:.1f}ms > {THRESHOLD_API_MS}ms"


# ═══════════════════════════════════════════════
# 10. 管理后台聚合查询性能
# ═══════════════════════════════════════════════
class TestAdminQueryPerformance:
    """管理后台聚合查询性能测试"""

    def test_audit_log_list_performance(self, client, fresh_db):
        """审计日志列表查询性能（100条）"""
        from skill.utils import admin_store

        for i in range(100):
            admin_store.add_audit_log(
                user="测试用户",
                role="员工",
                action="SUBMIT",
                target=f"REQ-AUDIT-{i}",
                result="成功",
                ip="10.0.0.1",
            )

        with client.session_transaction() as sess:
            sess["account"] = "ADM-001"
            sess["role"] = "admin"
            sess["name"] = "赵管理"

        start = time.perf_counter()
        resp = client.get("/api/admin/audit")
        elapsed = _elapsed_ms(start)

        assert resp.status_code == 200
        assert (
            elapsed < THRESHOLD_API_MS
        ), f"审计日志查询耗时 {elapsed:.1f}ms > {THRESHOLD_API_MS}ms"

    def test_usage_overview_performance(self, client, fresh_db):
        """用量统计概览查询性能（100条调用记录）"""
        from skill.utils import admin_store

        for i in range(100):
            admin_store.record_api_usage(
                call_type="发票OCR提取",
                model="deepseek-v4-flash",
                prompt_tokens=3000,
                completion_tokens=1500,
                latency_ms=2000,
                status="成功",
            )

        with client.session_transaction() as sess:
            sess["account"] = "ADM-001"
            sess["role"] = "admin"
            sess["name"] = "赵管理"

        start = time.perf_counter()
        resp = client.get("/api/admin/usage")
        elapsed = _elapsed_ms(start)

        assert resp.status_code == 200
        assert (
            elapsed < THRESHOLD_API_MS
        ), f"用量统计查询耗时 {elapsed:.1f}ms > {THRESHOLD_API_MS}ms"

    def test_usage_filter_performance(self, client, fresh_db):
        """用量明细筛选性能"""
        from skill.utils import admin_store

        for i in range(50):
            admin_store.record_api_usage(
                call_type="异常检测" if i % 2 == 0 else "分类限额",
                model="deepseek-v4-flash",
                prompt_tokens=1000,
                completion_tokens=500,
                latency_ms=1000 + i,
                status="成功",
            )

        with client.session_transaction() as sess:
            sess["account"] = "ADM-001"
            sess["role"] = "admin"
            sess["name"] = "赵管理"

        start = time.perf_counter()
        resp = client.get("/api/admin/usage?call_type=异常检测")
        elapsed = _elapsed_ms(start)

        assert resp.status_code == 200
        assert elapsed < THRESHOLD_API_MS, f"用量筛选耗时 {elapsed:.1f}ms > {THRESHOLD_API_MS}ms"

    def test_config_read_performance(self, client, fresh_db):
        """系统配置读取性能"""
        with client.session_transaction() as sess:
            sess["account"] = "ADM-001"
            sess["role"] = "admin"
            sess["name"] = "赵管理"

        start = time.perf_counter()
        resp = client.get("/api/admin/config")
        elapsed = _elapsed_ms(start)

        assert resp.status_code == 200
        assert elapsed < THRESHOLD_API_MS, f"配置读取耗时 {elapsed:.1f}ms > {THRESHOLD_API_MS}ms"


# ═══════════════════════════════════════════════
# 11. 并发性能
# ═══════════════════════════════════════════════
class TestConcurrentPerformance:
    """并发操作性能测试"""

    def test_concurrent_query_performance(self, fresh_db):
        """并发查询报销单性能（10线程 × 50查询）"""
        from skill.utils.db_store import get_reimbursement, save_reimbursement

        for i in range(100):
            save_reimbursement(
                request_id=f"REQ-CONC-{i:04d}",
                employee_id="EMP-2026",
                apply_amount=100 + i,
                apply_date="2026-07-16",
            )

        def query_one(idx):
            return get_reimbursement(f"REQ-CONC-{idx:04d}")

        count = 50
        start = time.perf_counter()
        with ThreadPoolExecutor(max_workers=10) as executor:
            results = list(executor.map(query_one, range(count)))
        elapsed = _elapsed_ms(start)

        assert all(r is not None for r in results)
        assert (
            elapsed < THRESHOLD_CONCURRENT_MS
        ), f"并发查询耗时 {elapsed:.1f}ms > {THRESHOLD_CONCURRENT_MS}ms"

    def test_concurrent_list_pending_performance(self, fresh_db):
        """并发查询待审列表性能（5线程并发）"""
        from skill import workflow as wf
        from skill.utils.db_store import save_invoice, save_reimbursement

        for i in range(50):
            save_reimbursement(
                request_id=f"REQ-CPEND-{i:04d}",
                employee_id="EMP-2026",
                apply_amount=100 + i,
                apply_date="2026-07-16",
            )
            save_invoice(
                {"发票号码": f"INV-CPEND-{i:04d}", "发票金额": 100 + i, "销售方名称": "X"},
                f"REQ-CPEND-{i:04d}",
                "",
            )

        start = time.perf_counter()
        with ThreadPoolExecutor(max_workers=5) as executor:
            results = list(executor.map(lambda _: wf.list_pending(), range(10)))
        elapsed = _elapsed_ms(start)

        assert all(len(r) == 50 for r in results)
        assert (
            elapsed < THRESHOLD_CONCURRENT_MS
        ), f"并发列表查询耗时 {elapsed:.1f}ms > {THRESHOLD_CONCURRENT_MS}ms"

    def test_concurrent_mask_performance(self):
        """并发脱敏性能（10线程 × 100次）"""
        from skill.utils.mask_sensitive import mask_ocr_result

        ocr = {
            "手机号": "13812345678",
            "购买方税号": "91110108MA01ABCD23",
            "发票金额": 300,
        }

        def mask_one(_):
            return mask_ocr_result(ocr)

        count = 100
        start = time.perf_counter()
        with ThreadPoolExecutor(max_workers=10) as executor:
            results = list(executor.map(mask_one, range(count)))
        elapsed = _elapsed_ms(start)

        assert all(r["手机号"] == "138****5678" for r in results)
        assert elapsed < 2000, f"并发脱敏耗时 {elapsed:.1f}ms > 2000ms"
