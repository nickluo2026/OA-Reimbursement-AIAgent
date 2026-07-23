"""功能3：异常输入检查 — 单元测试"""

from unittest.mock import patch

from skill.tools.tool_anomaly_check import (
    _rule_based_check,
    _summarize,
    detect_anomaly,
)


class TestRuleBasedCheck:
    """规则引擎本地检查测试"""

    def test_pass_with_normal_data(self, sample_invoice_data):
        """正常发票数据应无异常"""
        anomalies = _rule_based_check(
            sample_invoice_data,
            apply_amount=500,
            apply_date="2026-06-10",
        )
        assert len(anomalies) == 0

    def test_field_missing(self, sample_invoice_missing_fields):
        """缺失必填字段应检测到"""
        anomalies = _rule_based_check(sample_invoice_missing_fields)
        assert len(anomalies) >= 4  # 发票号码、开票日期、发票金额、销售方名称
        types = [a["异常类型"] for a in anomalies]
        assert "字段缺失" in types

    def test_invoice_number_format(self):
        """发票号码长度异常应检测"""
        invoice = {
            "发票号码": "123",
            "开票日期": "2026-06-01",
            "发票金额": 100,
            "销售方名称": "XX公司",
            "购买方名称": "YY公司",
        }
        anomalies = _rule_based_check(invoice)
        types = [a["异常类型"] for a in anomalies]
        assert "格式错误" in types

    def test_expired_invoice(self, sample_invoice_expired):
        """过期发票应检测"""
        anomalies = _rule_based_check(
            sample_invoice_expired,
            apply_amount=500,
            apply_date="2026-07-01",
        )
        types = [a["异常类型"] for a in anomalies]
        assert any("过期" in t for t in types)

    def test_future_invoice_date(self):
        """开票日期晚于申请日应检测"""
        invoice = {
            "发票号码": "12345678",
            "开票日期": "2026-07-15",
            "发票金额": 500,
            "销售方名称": "XX公司",
            "购买方名称": "YY公司",
        }
        anomalies = _rule_based_check(
            invoice,
            apply_amount=500,
            apply_date="2026-07-01",
        )
        types = [a["异常类型"] for a in anomalies]
        assert "日期异常" in types

    def test_amount_exceeds_threshold(self, sample_invoice_high_amount):
        """超过异常阈值的发票应检测"""
        anomalies = _rule_based_check(
            sample_invoice_high_amount,
            apply_amount=30000,
        )
        types = [a["异常类型"] for a in anomalies]
        assert "金额异常" in types

    def test_amount_exceeds_apply_amount(self, sample_invoice_data):
        """发票金额超过申请金额应拦截"""
        anomalies = _rule_based_check(
            sample_invoice_data,  # 发票金额 300
            apply_amount=200,  # 申请金额 200
            apply_date="2026-06-10",
        )
        types = [a["异常类型"] for a in anomalies]
        assert "金额异常" in types
        descs = " ".join(a["异常描述"] for a in anomalies)
        assert "超过申请金额" in descs

    def test_amount_within_apply_amount(self, sample_invoice_data):
        """发票金额 ≤ 申请金额 应通过"""
        anomalies = _rule_based_check(
            sample_invoice_data,  # 发票金额 300
            apply_amount=500,  # 申请金额 500
            apply_date="2026-06-10",
        )
        # 不应有金额异常
        amount_anomalies = [a for a in anomalies if a["异常类型"] == "金额异常"]
        assert len(amount_anomalies) == 0

    def test_apply_amount_none_skips_check(self, sample_invoice_data):
        """申请金额为空时跳过金额对比"""
        anomalies = _rule_based_check(
            sample_invoice_data,
            apply_amount=None,
            apply_date="2026-06-10",
        )
        amount_checks = [a for a in anomalies if "超过申请金额" in a.get("异常描述", "")]
        assert len(amount_checks) == 0

    @patch("skill.tools.tool_anomaly_check.check_duplicate_invoice")
    def test_duplicate_invoice_blocked(self, mock_check, sample_invoice_data):
        """重复发票号码应检测到重复报销并标记为严重"""
        mock_check.return_value = True
        anomalies = _rule_based_check(
            sample_invoice_data,
            apply_amount=500,
            apply_date="2026-06-10",
        )
        types = [a["异常类型"] for a in anomalies]
        assert "重复报销" in types
        dup = next(a for a in anomalies if a["异常类型"] == "重复报销")
        assert dup["严重程度"] == "严重"
        assert "12345678" in dup["异常描述"]

    @patch("skill.tools.tool_anomaly_check.check_duplicate_invoice")
    def test_duplicate_invoice_absent_not_blocked(self, mock_check, sample_invoice_data):
        """未重复发票号码应通过"""
        mock_check.return_value = False
        anomalies = _rule_based_check(
            sample_invoice_data,
            apply_amount=500,
            apply_date="2026-06-10",
        )
        types = [a["异常类型"] for a in anomalies]
        assert "重复报销" not in types

    @patch("skill.tools.tool_anomaly_check.check_duplicate_invoice")
    def test_duplicate_check_skipped_when_invoice_number_missing(self, mock_check):
        """发票号码缺失时跳过重复检查（避免无效查询）"""
        invoice = {
            "发票号码": "",
            "开票日期": "2026-06-01",
            "发票金额": 100,
            "销售方名称": "XX公司",
            "购买方名称": "YY公司",
        }
        anomalies = _rule_based_check(invoice, apply_amount=100, apply_date="2026-06-10")
        # 重复检查未调用
        mock_check.assert_not_called()
        # 但应检测到字段缺失
        types = [a["异常类型"] for a in anomalies]
        assert "字段缺失" in types


class TestSummarize:
    """异常结论判定测试"""

    def test_no_anomalies(self):
        conclusion, summary = _summarize([])
        assert conclusion == "通过"

    def test_severe_anomaly(self):
        anomalies = [{"严重程度": "严重", "异常类型": "字段缺失", "异常描述": "测试"}]
        conclusion, summary = _summarize(anomalies)
        assert conclusion == "拦截"

    def test_warning_only(self):
        anomalies = [{"严重程度": "警告", "异常类型": "即将过期", "异常描述": "测试"}]
        conclusion, summary = _summarize(anomalies)
        assert conclusion == "预警"


class TestDetectAnomaly:
    """完整异常检测流程测试"""

    @patch("skill.tools.tool_anomaly_check.call_deepseek_function")
    def test_return_block_on_rule_engine_severe(
        self,
        mock_ds,
        sample_invoice_missing_fields,
    ):
        """规则引擎发现严重异常时直接返回拦截"""
        # 规则引擎会发现字段缺失，直接返回拦截
        result = detect_anomaly(
            sample_invoice_missing_fields,
            apply_amount=None,
        )
        assert result["总体结论"] == "拦截"
        # DeepSeek 不应被调用
        mock_ds.assert_not_called()

    @patch("skill.tools.tool_anomaly_check.call_deepseek_function")
    def test_call_deepseek_when_rules_pass(
        self,
        mock_ds,
        sample_invoice_data,
    ):
        """规则检查通过时调用 DeepSeek 做语义补充"""
        mock_ds.return_value = {
            "总体结论": "通过",
            "异常明细": [],
            "检查摘要": "未发现异常",
        }
        result = detect_anomaly(
            sample_invoice_data,
            apply_amount=500,
            apply_date="2026-06-10",
        )
        mock_ds.assert_called_once()
        assert result["总体结论"] == "通过"

    @patch("skill.tools.tool_anomaly_check.call_deepseek_function")
    def test_merge_rule_and_deepseek_results(
        self,
        mock_ds,
        sample_invoice_data,
    ):
        """规则引擎和 DeepSeek 结果应合并"""
        # 注入一个规则引擎能检测到的问题
        invoice = dict(sample_invoice_data, **{"发票号码": "AB"})  # 太短
        mock_ds.return_value = {
            "总体结论": "通过",
            "异常明细": [],
            "检查摘要": "未发现异常",
        }
        result = detect_anomaly(
            invoice,
            apply_amount=500,
            apply_date="2026-06-10",
        )
        # 应包含规则引擎检测到的格式错误
        assert len(result["异常明细"]) >= 1
        # 总体结论应取更严格的
        assert result["总体结论"] in ("拦截", "预警")
