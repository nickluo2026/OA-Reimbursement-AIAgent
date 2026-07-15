"""敏感数据脱敏工具单元测试

验证 mask_phone / mask_tax_id / mask_ocr_result 的正确性，
并确保脱敏不修改原始数据（数据库完整性）。
"""

from skill.utils.mask_sensitive import (
    mask_ip,
    mask_phone,
    mask_tax_id,
    mask_ocr_result,
)


class TestMaskPhone:
    def test_normal_phone(self):
        assert mask_phone("13812345678") == "138****5678"

    def test_preserves_prefix3_suffix4(self):
        out = mask_phone("15900001111")
        assert out.startswith("159")
        assert out.endswith("1111")
        assert "*" in out

    def test_short_string_untouched(self):
        assert mask_phone("12345") == "12345"

    def test_empty(self):
        assert mask_phone("") == ""
        assert mask_phone(None) == ""


class TestMaskTaxId:
    def test_normal_18digit(self):
        # 18 位统一社会信用代码：保留前4后4，中间 10 个星（总长不变）
        out = mask_tax_id("91110108MA01ABCD23")
        assert out == "9111**********CD23"
        assert len(out) == 18

    def test_preserves_prefix4_suffix4(self):
        out = mask_tax_id("91310000701234567X")
        assert out.startswith("9131")
        assert out.endswith("567X")
        assert "*" in out
        assert len(out) == 18

    def test_short_code_untouched(self):
        assert mask_tax_id("1234567") == "1234567"

    def test_empty(self):
        assert mask_tax_id("") == ""
        assert mask_tax_id(None) == ""


class TestMaskIp:
    def test_normal_ipv4(self):
        assert mask_ip("192.168.1.100") == "192.168.***.***"

    def test_loopback(self):
        assert mask_ip("127.0.0.1") == "127.0.***.***"

    def test_preserves_prefix2(self):
        out = mask_ip("10.0.255.255")
        assert out.startswith("10.0.")
        assert "***" in out

    def test_ipv6_masked(self):
        """IPv6 非 IPv4 格式，统一返回 ***"""
        assert mask_ip("2001:db8::1") == "***"

    def test_short_string(self):
        assert mask_ip("1.2.3") == "***"

    def test_non_numeric_octets(self):
        assert mask_ip("192.168.abc.def") == "***"

    def test_empty(self):
        assert mask_ip("") == ""
        assert mask_ip(None) == ""


class TestMaskOcrResult:
    def test_invoice_tax_ids_masked(self):
        ocr = {
            "发票号码": "12345678",
            "购买方名称": "XX科技有限公司",
            "购买方税号": "91110108MA01XXXXX",
            "销售方名称": "YY酒店管理有限公司",
            "销售方税号": "91110108MA02YYYYY",
            "发票金额": 300.00,
        }
        masked = mask_ocr_result(ocr)
        # 17 位税号：保留前4后4，中间 9 个星（总长不变）
        assert masked["购买方税号"] == "9111*********XXXX"
        assert masked["销售方税号"] == "9111*********YYYY"
        # 非敏感字段保持原值
        assert masked["发票号码"] == "12345678"
        assert masked["购买方名称"] == "XX科技有限公司"
        assert masked["销售方名称"] == "YY酒店管理有限公司"
        assert masked["发票金额"] == 300.00

    def test_itinerary_phone_masked(self):
        ocr = {
            "申请日期": "2026-06-10",
            "手机号": "13812341234",
            "行程详情": [{"起点": "北京站", "终点": "国贸"}],
            "总金额_元": "85.50",
        }
        masked = mask_ocr_result(ocr)
        assert masked["手机号"] == "138****1234"
        # 行程字段不脱敏（审批必需）
        assert masked["行程详情"] == ocr["行程详情"]

    def test_original_data_not_mutated(self):
        """脱敏不得修改原始数据（数据库完整性）"""
        ocr = {"手机号": "13812345678", "购买方税号": "91110108MA01ABCD23"}
        original_phone = ocr["手机号"]
        original_tax = ocr["购买方税号"]
        _ = mask_ocr_result(ocr)
        assert ocr["手机号"] == original_phone
        assert ocr["购买方税号"] == original_tax

    def test_none_input(self):
        assert mask_ocr_result(None) is None

    def test_empty_dict(self):
        assert mask_ocr_result({}) == {}

    def test_no_sensitive_fields(self):
        """无敏感字段时原样返回（深拷贝）"""
        ocr = {"发票号码": "999", "发票金额": 100}
        masked = mask_ocr_result(ocr)
        assert masked == ocr
        assert masked is not ocr  # 深拷贝，非同一对象

    def test_non_string_sensitive_field_ignored(self):
        """敏感字段非字符串时不报错（跳过）"""
        ocr = {"手机号": 13812345678, "购买方税号": None}
        masked = mask_ocr_result(ocr)
        # 非字符串值保持原样
        assert masked["手机号"] == 13812345678
        assert masked["购买方税号"] is None
