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


@patch("skill.agents.itinerary_agent.verify_itinerary")
@patch("skill.agents.itinerary_agent.detect_itinerary_anomaly")
@patch("skill.agents.itinerary_agent.ocr_extract_itinerary")
class TestItineraryAgent:
    """行程单 Agent 编排测试"""

    def test_full_pipeline_pass(
        self,
        mock_ocr,
        mock_anomaly,
        mock_verify,
        sample_itinerary_data,
        sample_itinerary_anomaly_pass,
        sample_itinerary_verify_pass,
    ):
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
        # 透传：回写值应随响应返回，供 Web 层展示
        assert result["apply_amount"] == 85.5
        assert result["expense_category"] == "交通"

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

    def test_anomaly_block_skips_verify(
        self,
        mock_ocr,
        mock_anomaly,
        mock_verify,
        sample_itinerary_data,
        sample_itinerary_anomaly_block,
    ):
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

    def test_verify_warning_returns_warning(
        self,
        mock_ocr,
        mock_anomaly,
        mock_verify,
        sample_itinerary_data,
        sample_itinerary_anomaly_pass,
    ):
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

    @patch("skill.agents.itinerary_agent.update_ai_status")
    @patch("skill.agents.itinerary_agent.save_ai_check_result")
    @patch("skill.agents.itinerary_agent.save_invoice")
    @patch("skill.agents.itinerary_agent.save_reimbursement")
    def test_persistence_on_request_id(
        self,
        mock_reimb,
        mock_invoice,
        mock_save,
        mock_update,
        mock_ocr,
        mock_anomaly,
        mock_verify,
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

        # 修复验证：OCR 总金额应回写为申请金额，且费用类型推导为「交通」
        kw = mock_reimb.call_args.kwargs
        assert kw["apply_amount"] == 85.5
        assert kw["expense_category"] == "交通"

    @patch("skill.agents.itinerary_agent.update_ai_status")
    @patch("skill.agents.itinerary_agent.save_ai_check_result")
    @patch("skill.agents.itinerary_agent.save_invoice")
    @patch("skill.agents.itinerary_agent.save_reimbursement")
    def test_persistence_empty_ocr_amount_falls_back(
        self,
        mock_reimb,
        mock_invoice,
        mock_save,
        mock_update,
        mock_ocr,
        mock_anomaly,
        mock_verify,
        sample_itinerary_data,
        sample_itinerary_anomaly_pass,
        sample_itinerary_verify_pass,
    ):
        """OCR 总金额为空时，申请金额回退到 state 原值"""
        empty_total = dict(sample_itinerary_data)
        empty_total["总金额_元"] = ""
        mock_ocr.return_value = empty_total
        mock_anomaly.return_value = sample_itinerary_anomaly_pass
        mock_verify.return_value = sample_itinerary_verify_pass

        _ = run_reimbursement_skill(
            pdf_path="itinerary.pdf",
            apply_amount=200,
            apply_date="2026-06-10",
            request_id="REQ-ITN-002",
            employee_id="E001",
            ticket_type="行程单",
        )

        kw = mock_reimb.call_args.kwargs
        assert kw["apply_amount"] == 200  # 回退到 state 原值
        assert kw["expense_category"] == "交通"

    @patch("skill.agents.itinerary_agent.update_ai_status")
    @patch("skill.agents.itinerary_agent.save_ai_check_result")
    @patch("skill.agents.itinerary_agent.save_invoice")
    @patch("skill.agents.itinerary_agent.save_reimbursement")
    def test_persistence_respects_user_expense_category(
        self,
        mock_reimb,
        mock_invoice,
        mock_save,
        mock_update,
        mock_ocr,
        mock_anomaly,
        mock_verify,
        sample_itinerary_data,
        sample_itinerary_anomaly_pass,
        sample_itinerary_verify_pass,
    ):
        """用户已预选费用分类时，应尊重用户选择而非推导值"""
        mock_ocr.return_value = sample_itinerary_data
        mock_anomaly.return_value = sample_itinerary_anomaly_pass
        mock_verify.return_value = sample_itinerary_verify_pass

        _ = run_reimbursement_skill(
            pdf_path="itinerary.pdf",
            apply_amount=100,
            apply_date="2026-06-10",
            request_id="REQ-ITN-003",
            employee_id="E001",
            expense_category="差旅",
            ticket_type="行程单",
        )

        kw = mock_reimb.call_args.kwargs
        assert kw["apply_amount"] == 85.5  # OCR 总金额仍回写
        assert kw["expense_category"] == "差旅"  # 尊重用户预选


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
