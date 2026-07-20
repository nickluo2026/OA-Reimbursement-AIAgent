"""功能2：费用分类与限额校验 — 单元测试"""

from unittest.mock import patch

from skill.tools.tool_classify_limit import classify_and_check_limit


class TestClassifyAndCheckLimit:
    """分类限额校验测试"""

    @patch("skill.tools.tool_classify_limit.call_deepseek_function")
    def test_normal_classify(self, mock_ds, sample_invoice_data):
        """正常分类限额校验（餐饮限额 1000，金额 300 不超限）"""
        mock_ds.return_value = {
            "费用分类": "餐饮",
            "分类依据": "发票内容为餐饮服务",
            "发票金额": 300,
            "分类限额": 1000,
            "是否超限": False,
            "校验结果": "金额300 ≤ 限额1000，通过",
        }
        result = classify_and_check_limit(sample_invoice_data)
        assert result["费用分类"] == "餐饮"
        assert result["是否超限"] is False

    @patch("skill.tools.tool_classify_limit.call_deepseek_function")
    def test_over_limit(self, mock_ds):
        """超限发票：金额 1200 > 餐饮限额 1000"""
        mock_ds.return_value = {
            "费用分类": "餐饮",
            "分类依据": "发票内容为餐饮服务",
            "发票金额": 1200,
            "分类限额": 300,
            "是否超限": True,
            "校验结果": "金额1200 > 限额300，超出900元，需人工审批",
        }
        invoice = {"发票金额": 1200, "商品明细": [{"项目名称": "餐饮服务"}]}
        result = classify_and_check_limit(invoice)
        assert result["是否超限"] is True
        assert "超出" in result["校验结果"]

    @patch("skill.tools.tool_classify_limit.call_deepseek_function")
    def test_deepseek_failure(self, mock_ds):
        """DeepSeek 调用失败时应返回错误信息"""
        mock_ds.return_value = {"_error": "调用失败"}
        result = classify_and_check_limit({"发票金额": 300})
        assert "_error" in result or "校验结果" in result

    @patch("skill.tools.tool_classify_limit.call_deepseek_function")
    def test_travel_merges_into_other(self, mock_ds):
        """差旅限额已并入「其他」：差旅分类回退到「其他」限额（200）"""
        mock_ds.return_value = {
            "费用分类": "差旅",
            "分类依据": "发票项目名称为'住宿费'",
            "发票金额": 300,
            "分类限额": 1000,
            "是否超限": False,
            "校验结果": "金额300 ≤ 限额1000，通过",
        }
        result = classify_and_check_limit({"发票金额": 300})
        # 回退到「其他=200」，300 > 200 → 超限
        assert result["费用分类"] == "差旅"
        assert result["分类限额"] == 200
        assert result["是否超限"] is True
