"""行程单工具单元测试

覆盖：
    - 异常检测：字段缺失/日期逻辑/金额异常
    - 合理性校验：金额匹配/天数/单笔最高/日期范围/连续性
"""

from skill.tools.tool_itinerary_anomaly import detect_itinerary_anomaly
from skill.tools.tool_itinerary_verify import verify_itinerary


class TestDetectItineraryAnomaly:
    """行程单异常检测（纯规则）"""

    def test_pass_normal(self, sample_itinerary_data):
        """正常行程单 → 通过"""
        result = detect_itinerary_anomaly(
            itinerary=sample_itinerary_data,
            apply_amount=100,
            apply_date="2026-06-10",
        )
        assert result["总体结论"] == "通过"
        assert result["异常明细"] == []

    def test_missing_fields_block(self, sample_itinerary_missing_fields):
        """字段缺失 → 拦截"""
        result = detect_itinerary_anomaly(
            itinerary=sample_itinerary_missing_fields,
            apply_amount=100,
            apply_date="2026-06-10",
        )
        assert result["总体结论"] == "拦截"
        types = [a["异常类型"] for a in result["异常明细"]]
        assert "字段缺失" in types

    def test_date_logic_block(self):
        """开始日期晚于结束日期 → 拦截"""
        itinerary = {
            "申请日期": "2026-06-10",
            "行程开始日期": "2026-06-09",
            "行程结束日期": "2026-06-08",
            "总金额_元": "50",
            "行程详情": [
                {"序号": 1, "金额_元": "50", "上车时间": "2026-06-08 10:00"},
            ],
        }
        result = detect_itinerary_anomaly(itinerary, apply_amount=100, apply_date="2026-06-10")
        assert result["总体结论"] == "拦截"
        descs = [a["异常描述"] for a in result["异常明细"]]
        assert any("晚于结束日期" in d for d in descs)

    def test_total_amount_exceeds_apply_block(self, sample_itinerary_data):
        """总金额超过申请金额 → 拦截"""
        result = detect_itinerary_anomaly(
            itinerary=sample_itinerary_data,
            apply_amount=50,  # 行程单总金额 85.5 > 50
            apply_date="2026-06-10",
        )
        assert result["总体结论"] == "拦截"
        descs = [a["异常描述"] for a in result["异常明细"]]
        assert any("超过申请金额" in d for d in descs)

    def test_single_amount_warning(self):
        """单笔金额超阈值 → 预警"""
        itinerary = {
            "申请日期": "2026-06-10",
            "行程开始日期": "2026-06-08",
            "行程结束日期": "2026-06-09",
            "总金额_元": "600",
            "行程详情": [
                {"序号": 1, "金额_元": "600", "上车时间": "2026-06-08 10:00"},
            ],
        }
        result = detect_itinerary_anomaly(itinerary, apply_amount=1000, apply_date="2026-06-10")
        # 单笔 600 > 500 阈值 → 警告；总金额 600 < 2000 上限
        assert result["总体结论"] == "预警"
        descs = [a["异常描述"] for a in result["异常明细"]]
        assert any("超过单笔阈值" in d for d in descs)

    def test_trip_date_after_apply_block(self):
        """行程日期晚于申请日 → 拦截"""
        itinerary = {
            "申请日期": "2026-06-10",
            "行程开始日期": "2026-06-15",
            "行程结束日期": "2026-06-16",
            "总金额_元": "50",
            "行程详情": [
                {"序号": 1, "金额_元": "50", "上车时间": "2026-06-15 10:00"},
            ],
        }
        result = detect_itinerary_anomaly(itinerary, apply_amount=100, apply_date="2026-06-10")
        assert result["总体结论"] == "拦截"


class TestVerifyItinerary:
    """行程单合理性校验（纯规则）"""

    def test_pass_normal(self, sample_itinerary_data):
        """正常行程单 → 通过"""
        result = verify_itinerary(sample_itinerary_data, apply_amount=100)
        assert result["校验结论"] == "通过"
        assert result["行程天数"] == 2

    def test_amount_mismatch_block(self, sample_itinerary_amount_mismatch):
        """总金额与明细合计不一致 → 拦截"""
        result = verify_itinerary(sample_itinerary_amount_mismatch, apply_amount=200)
        assert result["校验结论"] == "拦截"
        assert "不一致" in result["总金额校验"]

    def test_amount_exceeds_apply_block(self, sample_itinerary_data):
        """总金额超过申请金额 → 拦截"""
        result = verify_itinerary(sample_itinerary_data, apply_amount=50)
        assert result["校验结论"] == "拦截"
        assert "超过申请金额" in result["总金额校验"]

    def test_single_amount_warning(self):
        """单笔金额超阈值 → 预警"""
        itinerary = {
            "申请日期": "2026-06-10",
            "行程开始日期": "2026-06-08",
            "行程结束日期": "2026-06-09",
            "总金额_元": "600",
            "行程详情": [
                {"序号": 1, "金额_元": "600", "上车时间": "2026-06-08 10:00"},
            ],
        }
        result = verify_itinerary(itinerary, apply_amount=1000)
        assert result["校验结论"] == "预警"
        assert "超过阈值" in result["单笔最高金额"]

    def test_date_out_of_range_block(self):
        """行程上车时间超出日期范围 → 拦截"""
        itinerary = {
            "申请日期": "2026-06-10",
            "行程开始日期": "2026-06-08",
            "行程结束日期": "2026-06-09",
            "总金额_元": "50",
            "行程详情": [
                {"序号": 1, "金额_元": "50", "上车时间": "2026-06-11 10:00"},  # 超出范围
            ],
        }
        result = verify_itinerary(itinerary, apply_amount=100)
        assert result["校验结论"] == "拦截"
        assert "不在行程日期范围内" in result["日期合理性"]

    def test_days_calculation(self):
        """行程天数计算"""
        itinerary = {
            "申请日期": "2026-06-10",
            "行程开始日期": "2026-06-01",
            "行程结束日期": "2026-06-05",
            "总金额_元": "100",
            "行程详情": [
                {"序号": 1, "金额_元": "100", "上车时间": "2026-06-01 10:00"},
            ],
        }
        result = verify_itinerary(itinerary, apply_amount=200)
        assert result["行程天数"] == 5  # 5 - 1 + 1

    def test_continuity_warning(self):
        """行程间隔过大 → 预警"""
        itinerary = {
            "申请日期": "2026-06-20",
            "行程开始日期": "2026-06-01",
            "行程结束日期": "2026-06-20",
            "总金额_元": "100",
            "行程详情": [
                {"序号": 1, "金额_元": "50", "上车时间": "2026-06-01 10:00"},
                {"序号": 2, "金额_元": "50", "上车时间": "2026-06-10 10:00"},  # 间隔 9 天 > 72 小时
            ],
        }
        result = verify_itinerary(itinerary, apply_amount=200)
        assert result["校验结论"] == "预警"
        assert "间隔" in result["行程连续性"]

    def test_missing_dates_block(self, sample_itinerary_missing_fields):
        """日期缺失 → 拦截"""
        result = verify_itinerary(sample_itinerary_missing_fields, apply_amount=100)
        assert result["校验结论"] == "拦截"
