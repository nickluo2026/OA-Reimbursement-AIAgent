"""Agent 编排层集成测试

V1.4 重构后工具引用迁移至 ``skill/orchestrator/nodes/`` 各节点模块，
patch 路径相应调整；测试用例语义与原版保持一致。
"""

from unittest.mock import patch

from skill.agent import run_reimbursement_skill
from skill.orchestrator.graph import route_after_anomaly, route_after_ocr
from skill.orchestrator.state import CheckStatus


@patch("skill.orchestrator.nodes.ocr_node.ocr_extract_invoice")
@patch("skill.orchestrator.nodes.anomaly_node.detect_anomaly")
@patch("skill.orchestrator.nodes.classify_node.classify_and_check_limit")
class TestRunReimbursementSkill:
    """主编排函数测试"""

    def test_full_pipeline_pass(
        self, mock_classify, mock_anomaly, mock_ocr, sample_invoice_data, sample_classify_result
    ):
        """完整流程：通过"""
        mock_ocr.return_value = sample_invoice_data
        mock_anomaly.return_value = {
            "总体结论": "通过",
            "异常明细": [],
            "检查摘要": "无异常",
        }
        mock_classify.return_value = sample_classify_result

        result = run_reimbursement_skill(
            pdf_path="test.pdf",
            apply_amount=500,
            apply_date="2026-06-10",
        )

        assert result["status"] == "预警"  # 分类超限 → 预警
        assert result["ocr_result"] is not None
        assert result["anomaly_result"] is not None
        assert result["classify_result"] is not None

    def test_ocr_error_returns_early(self, mock_classify, mock_anomaly, mock_ocr):
        """OCR 失败时立即返回"""
        mock_ocr.return_value = {"_error": "文件不存在"}

        result = run_reimbursement_skill(pdf_path="bad.pdf")

        assert result["status"] == "错误"
        mock_anomaly.assert_not_called()
        mock_classify.assert_not_called()

    def test_anomaly_block_discards_classify_result(
        self, mock_classify, mock_anomaly, mock_ocr, sample_invoice_data
    ):
        """异常拦截时分类限额被并行投机触发，但结果被丢弃，最终状态仍为拦截。"""
        mock_ocr.return_value = sample_invoice_data
        mock_anomaly.return_value = {
            "总体结论": "拦截",
            "异常明细": [{"异常类型": "金额异常", "异常描述": "测试", "严重程度": "严重"}],
            "检查摘要": "拦截",
        }

        result = run_reimbursement_skill(
            pdf_path="test.pdf",
            apply_amount=200,
            apply_date="2026-06-10",
        )

        # 方案A 并行化：分类限额会被投机调用（与串行版不同），但其结论不生效
        assert mock_classify.called
        # 拦截优先：最终状态为拦截，分类限额异常结论被忽略
        assert result["status"] == "拦截"

    def test_small_amount_skips_classify(self, mock_classify, mock_anomaly, mock_ocr):
        """小额发票跳过分类限额"""
        mock_ocr.return_value = {
            "发票号码": "12345678",
            "发票金额": 50,
            "开票日期": "2026-06-01",
            "购买方名称": "XX公司",
            "销售方名称": "YY公司",
        }
        mock_anomaly.return_value = {
            "总体结论": "通过",
            "异常明细": [],
            "检查摘要": "无异常",
        }

        result = run_reimbursement_skill(
            pdf_path="test.pdf",
            apply_amount=50,
        )

        assert result["status"] == "通过"
        assert "小额免审" in result["classify_result"]["费用分类"]
        mock_classify.assert_not_called()

    def test_parallel_anomaly_classify_for_large_amount(
        self, mock_classify, mock_anomaly, mock_ocr, sample_invoice_data
    ):
        """方案A验收：金额>100 时异常检测与分类限额并行执行（而非串行）。

        用带 sleep 的桩函数模拟两次 LLM 调用；若二者并行，重叠区间应接近单次耗时
        （~0.30s）而非两次串行之和（~0.60s）。
        """
        import threading
        import time

        events: dict = {}
        lock = threading.Lock()

        def _slow_anomaly(invoice, apply_amount=None, apply_date=None):
            with lock:
                events["anomaly_start"] = time.time()
            time.sleep(0.30)
            with lock:
                events["anomaly_end"] = time.time()
            return {"总体结论": "通过", "异常明细": [], "检查摘要": "无异常"}

        def _slow_classify(invoice=None):
            with lock:
                events["classify_start"] = time.time()
            time.sleep(0.30)
            with lock:
                events["classify_end"] = time.time()
            return {
                "是否超限": False,
                "费用分类": "差旅",
                "发票金额": 300,
                "分类限额": 1000,
                "校验结果": "通过",
            }

        mock_ocr.return_value = sample_invoice_data  # 发票金额=300 > 100 → 并行分支
        mock_anomaly.side_effect = _slow_anomaly
        mock_classify.side_effect = _slow_classify
        with patch("skill.orchestrator.nodes.verify_node.verify_invoice", return_value={}):
            run_reimbursement_skill(
                pdf_path="test.pdf", apply_amount=500, apply_date="2026-06-10"
            )

        # 两节点几乎同时启动
        assert events["classify_start"] - events["anomaly_start"] < 0.15
        # 重叠区间（关键路径）远小于两次串行之和 0.60s，证明并行
        span = max(events["anomaly_end"], events["classify_end"]) - min(
            events["anomaly_start"], events["classify_start"]
        )
        assert span < 0.5

    @patch("skill.orchestrator.nodes.classify_node.update_ai_status")
    @patch("skill.orchestrator.nodes.classify_node.save_ai_check_result")
    @patch("skill.orchestrator.nodes.anomaly_node.save_ai_check_result")
    @patch("skill.orchestrator.nodes.ocr_node.save_ai_check_result")
    @patch("skill.orchestrator.nodes.ocr_node.save_invoice")
    def test_persistence_on_request_id(
        self,
        mock_invoice,
        mock_save_ocr,
        mock_save_anomaly,
        mock_save_classify,
        mock_update,
        mock_classify,
        mock_anomaly,
        mock_ocr,
        sample_invoice_data,
        sample_classify_result,
    ):
        """有 request_id 时应持久化数据"""
        mock_ocr.return_value = sample_invoice_data
        mock_anomaly.return_value = {
            "总体结论": "通过",
            "异常明细": [],
            "检查摘要": "无异常",
        }
        mock_classify.return_value = {
            "费用分类": "差旅",
            "分类依据": "住宿费",
            "发票金额": 300,
            "分类限额": 1000,
            "是否超限": False,
            "校验结果": "通过",
        }

        _ = run_reimbursement_skill(
            pdf_path="test.pdf",
            apply_amount=500,
            apply_date="2026-06-10",
            request_id="REQ-001",
            employee_id="E001",
        )

        # 运行期不应预建报销单（统一在「提交审批」时由 workflow.create_reimbursement_on_submit 建单）
        mock_invoice.assert_called_once()
        # save_ai_check_result 分布在 OCR/异常检测/分类限额 三处节点
        total_save = (
            mock_save_ocr.call_count + mock_save_anomaly.call_count + mock_save_classify.call_count
        )
        assert total_save >= 2

    @patch("skill.orchestrator.nodes.classify_node.update_ai_status")
    @patch("skill.orchestrator.nodes.classify_node.save_ai_check_result")
    @patch("skill.orchestrator.nodes.anomaly_node.save_ai_check_result")
    @patch("skill.orchestrator.nodes.ocr_node.save_ai_check_result")
    @patch("skill.orchestrator.nodes.ocr_node.save_invoice")
    def test_persistence_error_non_fatal(
        self,
        mock_invoice,
        mock_save_ocr,
        mock_save_anomaly,
        mock_save_classify,
        mock_update,
        mock_classify,
        mock_anomaly,
        mock_ocr,
        sample_invoice_data,
    ):
        """持久化异常不应影响主流程"""
        mock_ocr.return_value = sample_invoice_data
        mock_anomaly.return_value = {
            "总体结论": "通过",
            "异常明细": [],
            "检查摘要": "无异常",
        }
        mock_classify.return_value = {
            "费用分类": "差旅",
            "发票金额": 300,
            "分类限额": 1000,
            "是否超限": False,
            "校验结果": "通过",
        }
        # 运行期不再调用 save_reimbursement；改为让 update_ai_status 抛异常，验证持久化异常不影响主流程
        mock_update.side_effect = Exception("DB error")

        result = run_reimbursement_skill(
            pdf_path="test.pdf",
            apply_amount=500,
            request_id="REQ-001",
        )

        # 主流程不应受影响
        assert result["status"] == "通过"


class TestGraphRouting:
    """StateGraph 条件路由单元测试"""

    def test_route_after_ocr_error(self):
        """OCR 失败 → error（提前结束）"""
        assert route_after_ocr({"final_status": CheckStatus.ERROR}) == "error"

    def test_route_after_ocr_routing(self):
        """OCR 后路由（方案A）：金额>100 并行、≤100 串行、失败提前结束"""
        # 金额 > 100 → 异常检测 ‖ 分类限额 并行分支
        assert (
            route_after_ocr({"final_status": CheckStatus.PASS, "ocr_result": {"发票金额": 300}})
            == "parallel"
        )
        # 金额 ≤ 100 → 仅异常检测（小额免审，串行）
        assert (
            route_after_ocr({"final_status": CheckStatus.PASS, "ocr_result": {"发票金额": 80}})
            == "anomaly_only"
        )
        # OCR 失败 → 提前结束
        assert route_after_ocr({"final_status": CheckStatus.ERROR}) == "error"

    def test_route_after_anomaly_block(self):
        """异常拦截 → block（提前结束）"""
        state = {"final_status": CheckStatus.BLOCK, "ocr_result": {}}
        assert route_after_anomaly(state) == "block"

    def test_route_after_anomaly_classify(self):
        """金额 > 100 → classify（执行限额校验）"""
        state = {"final_status": CheckStatus.PASS, "ocr_result": {"发票金额": 300}}
        assert route_after_anomaly(state) == "classify"

    def test_route_after_anomaly_skip(self):
        """金额 ≤ 100 → skip（小额免审）"""
        state = {"final_status": CheckStatus.PASS, "ocr_result": {"发票金额": 50}}
        assert route_after_anomaly(state) == "skip"

    def test_route_after_anomaly_boundary(self):
        """金额恰好 100 → skip（边界值，> 100 才分类）"""
        state = {"final_status": CheckStatus.PASS, "ocr_result": {"发票金额": 100}}
        assert route_after_anomaly(state) == "skip"
