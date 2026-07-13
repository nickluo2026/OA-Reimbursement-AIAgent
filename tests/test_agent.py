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

    def test_full_pipeline_pass(self, mock_classify, mock_anomaly, mock_ocr,
                                 sample_invoice_data, sample_classify_result):
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

    def test_anomaly_block_skips_classify(self, mock_classify, mock_anomaly, mock_ocr,
                                           sample_invoice_data):
        """异常拦截时跳过分类限额"""
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

        assert result["status"] == "拦截"
        mock_classify.assert_not_called()  # 被拦截，不执行分类

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

    @patch("skill.orchestrator.nodes.classify_node.update_ai_status")
    @patch("skill.orchestrator.nodes.classify_node.save_ai_check_result")
    @patch("skill.orchestrator.nodes.anomaly_node.save_ai_check_result")
    @patch("skill.orchestrator.nodes.ocr_node.save_ai_check_result")
    @patch("skill.orchestrator.nodes.ocr_node.save_invoice")
    @patch("skill.orchestrator.nodes.ocr_node.save_reimbursement")
    def test_persistence_on_request_id(
        self, mock_reimb, mock_invoice, mock_save_ocr, mock_save_anomaly,
        mock_save_classify, mock_update,
        mock_classify, mock_anomaly, mock_ocr,
        sample_invoice_data, sample_classify_result,
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

        # 应调用持久化
        mock_reimb.assert_called_once()
        mock_invoice.assert_called_once()
        # save_ai_check_result 分布在 OCR/异常检测/分类限额 三处节点
        total_save = (
            mock_save_ocr.call_count
            + mock_save_anomaly.call_count
            + mock_save_classify.call_count
        )
        assert total_save >= 2

    @patch("skill.orchestrator.nodes.classify_node.update_ai_status")
    @patch("skill.orchestrator.nodes.classify_node.save_ai_check_result")
    @patch("skill.orchestrator.nodes.anomaly_node.save_ai_check_result")
    @patch("skill.orchestrator.nodes.ocr_node.save_ai_check_result")
    @patch("skill.orchestrator.nodes.ocr_node.save_invoice")
    @patch("skill.orchestrator.nodes.ocr_node.save_reimbursement")
    def test_persistence_error_non_fatal(
        self, mock_reimb, mock_invoice, mock_save_ocr, mock_save_anomaly,
        mock_save_classify, mock_update,
        mock_classify, mock_anomaly, mock_ocr,
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
        mock_reimb.side_effect = Exception("DB error")

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

    def test_route_after_ocr_ok(self):
        """OCR 成功 → ok（进入异常检测）"""
        assert route_after_ocr({"final_status": CheckStatus.PASS}) == "ok"

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
