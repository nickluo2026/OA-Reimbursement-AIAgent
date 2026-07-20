"""功能5：发票真伪查验单元测试（Mock Provider）"""

from skill.tools.tool_verify_invoice import verify_invoice


def _invoice(no: str) -> dict:
    return {
        "发票号码": no,
        "开票日期": "2026-01-01",
        "价税合计小写": 100.0,
        "发票代码": "110001",
    }


def test_mock_normal_passes():
    r = verify_invoice(_invoice("INV-1001"))
    assert r["查验状态"] == "正常"
    assert r["总体结论"] == "通过"


def test_mock_void_blocks():
    r = verify_invoice(_invoice("INV-VOID-1"))
    assert r["查验状态"] == "作废"
    assert r["总体结论"] == "拦截"


def test_mock_red_blocks():
    r = verify_invoice(_invoice("INV-RED-1"))
    assert r["查验状态"] == "红冲"
    assert r["总体结论"] == "拦截"


def test_mock_fake_blocks():
    r = verify_invoice(_invoice("INV-FAKE-1"))
    assert r["查验状态"] == "查无此票"
    assert r["总体结论"] == "拦截"


def test_block_on_fake_false_downgrades_to_warn():
    r = verify_invoice(_invoice("INV-FAKE-1"), block_on_fake=False)
    assert r["总体结论"] == "预警"


def test_unknown_provider_falls_back_to_mock():
    r = verify_invoice(_invoice("INV-1001"), provider="unknown-provider")
    assert r["查验平台"] == "mock"
    assert r["查验状态"] == "正常"
