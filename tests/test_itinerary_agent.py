"""行程单 Agent 集成测试

覆盖：
    - 完整流程通过
    - OCR 失败早退
    - 异常拦截跳过合理性校验
    - 金额不匹配 → 预警
    - 票据类型路由
"""

from unittest.mock import patch

from skill.agent import run_reimbursement_skill
from skill.orchestrator.graph import route_by_ticket_type
from skill.orchestrator.state import CheckStatus


@patch("skill.orchestrator.nodes.itinerary_node.verify_itinerary")
@patch("skill.orchestrator.nodes.itinerary_node.detect_itinerary_anomaly")
@patch("skill.orchestrator.nodes.itinerary_node.ocr_extract_itinerary")
class TestItineraryAgent:
    """行程单 Agent 编排测试"""

    def test_full_pipeline_pass(self, mock_ocr, mock_anomaly, mock_verify,
                                 sample_itinerary_data,
                                 sample_itinerary_anomaly_pass,
                                 sample_itinerary_verify_pass):
        """完整流程：通过"""
        mock_ocr.return_value = sample_itinerary_data
        mock_anomaly.return_value = sample_itinerary_anomaly_pass
        mock_verify.return_value = sample_itinerary_verify_pass

        result = run_reimbursement_skill(
            pdf_path="itinerary.pdf",
            apply_amount=100,
            apply_date="2026-06-10",
            ticket_type="行程单",
        )

        assert result["status"] == "通过"
        assert result["ocr_result"] is not None
        assert result["anomaly_result"] is not None
        assert result["itinerary_result"] is not None
        assert result["classify_result"] is None  # 行程单无分类限额

    def test_ocr_error_returns_early(self, mock_ocr, mock_anomaly, mock_verify):
        """OCR 失败时立即返回"""
        mock_ocr.return_value = {"_error": "文件不存在"}

        result = run_reimbursement_skill(
            pdf_path="bad.pdf",
            ticket_type="行程单",
        )

        assert result["status"] == "错误"
        mock_anomaly.assert_not_called()
        mock_verify.assert_not_called()

    def test_anomaly_block_skips_verify(self, mock_ocr, mock_anomaly, mock_verify,
                                         sample_itinerary_data,
                                         sample_itinerary_anomaly_block):
        """异常拦截时跳过合理性校验"""
        mock_ocr.return_value = sample_itinerary_data
        mock_anomaly.return_value = sample_itinerary_anomaly_block

        result = run_reimbursement_skill(
            pdf_path="itinerary.pdf",
            apply_amount=200,
            apply_date="2026-06-10",
            ticket_type="行程单",
        )

        assert result["status"] == "拦截"
        mock_verify.assert_not_called()
        assert result["itinerary_result"] is None

    def test_verify_warning_returns_warning(self, mock_ocr, mock_anomaly, mock_verify,
                                             sample_itinerary_data,
                                             sample_itinerary_anomaly_pass):
        """合理性校验预警 → 最终预警"""
        mock_ocr.return_value = sample_itinerary_data
        mock_anomaly.return_value = sample_itinerary_anomaly_pass
        mock_verify.return_value = {
            "校验结论": "预警",
            "总金额校验": "单笔金额偏高",
            "行程天数": 2,
            "单笔最高金额": "单笔最高金额 600 元超过阈值 500 元",
            "日期合理性": "通过",
            "行程连续性": "通过",
            "校验明细": [
                {"校验项目": "单笔最高金额", "校验结果": "预警", "说明": "超阈值"},
            ],
        }

        result = run_reimbursement_skill(
            pdf_path="itinerary.pdf",
            apply_amount=1000,
            ticket_type="行程单",
        )

        assert result["status"] == "预警"
        assert result["itinerary_result"]["校验结论"] == "预警"

    @patch("skill.orchestrator.nodes.itinerary_node.update_ai_status")
    @patch("skill.orchestrator.nodes.itinerary_node.save_ai_check_result")
    @patch("skill.orchestrator.nodes.itinerary_node.save_invoice")
    @patch("skill.orchestrator.nodes.itinerary_node.save_reimbursement")
    def test_persistence_on_request_id(
        self, mock_reimb, mock_invoice, mock_save, mock_update,
        mock_ocr, mock_anomaly, mock_verify,
        sample_itinerary_data,
        sample_itinerary_anomaly_pass,
        sample_itinerary_verify_pass,
    ):
        """有 request_id 时应持久化数据"""
        mock_ocr.return_value = sample_itinerary_data
        mock_anomaly.return_value = sample_itinerary_anomaly_pass
        mock_verify.return_value = sample_itinerary_verify_pass

        _ = run_reimbursement_skill(
            pdf_path="itinerary.pdf",
            apply_amount=100,
            apply_date="2026-06-10",
            request_id="REQ-ITN-001",
            employee_id="E001",
            ticket_type="行程单",
        )

        mock_reimb.assert_called_once()
        mock_invoice.assert_called_once()
        # save_ai_check_result 在 OCR/异常检测/合理性校验三处
        assert mock_save.call_count >= 2


class TestItineraryRouting:
    """行程单路由条件边测试"""

    def test_route_invoice(self):
        """发票类型 → ocr 节点"""
        assert route_by_ticket_type({"ticket_type": "发票"}) == "发票"

    def test_route_itinerary(self):
        """行程单类型 → itinerary 节点"""
        assert route_by_ticket_type({"ticket_type": "行程单"}) == "行程单"

    def test_route_default(self):
        """默认 → 发票"""
        assert route_by_ticket_type({}) == "发票"
