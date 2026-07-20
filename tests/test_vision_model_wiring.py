"""C1+C3 验收测试：视觉调用使用 DEEPSEEK_VISION_MODEL 并记录真实延迟。

验证：
  - 图片/扫描件走 Vision API 时，payload.model == DEEPSEEK_VISION_MODEL（C1）
  - 用量记录 model == DEEPSEEK_VISION_MODEL 且 latency_ms 为非负整数（C3）
  - 修复后 _record_vision_usage 不再因 NameError 被静默吞掉

不发起真实网络请求（mock requests.post）。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from skill.config import DEEPSEEK_VISION_MODEL
from skill.tools.tool_itinerary_ocr import ocr_extract_itinerary
from skill.tools.tool_ocr_extract import ocr_extract_invoice


def _make_image(tmp_path) -> str:
    """生成一个 .png 测试文件（内容无需为真实图片，base64 编码对任意字节生效）。"""
    p = tmp_path / "ticket.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\nfake-image-bytes")
    return str(p)


def _mock_tool_calls_response(func_name: str, args: str) -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {
        "choices": [
            {"message": {"tool_calls": [{"function": {"name": func_name, "arguments": args}}]}}
        ],
        "usage": {"prompt_tokens": 500, "completion_tokens": 200},
    }
    return resp


@patch("skill.utils.admin_store.record_api_usage")
@patch("skill.utils.http_client._get_headers", return_value={"Authorization": "Bearer x"})
@patch("requests.post")
def test_invoice_vision_uses_vision_model_and_records_latency(
    mock_post, mock_headers, mock_record, tmp_path, fresh_db
):
    img = _make_image(tmp_path)
    mock_post.return_value = _mock_tool_calls_response(
        "extract_invoice", '{"发票号码":"123","发票金额":100}'
    )

    result = ocr_extract_invoice(img)

    assert "_error" not in result
    assert result["发票号码"] == "123"

    # C1：视觉调用 payload 使用 DEEPSEEK_VISION_MODEL
    payload = mock_post.call_args.kwargs["json"]
    assert payload["model"] == DEEPSEEK_VISION_MODEL

    # C3：用量记录含 latency_ms 且 model 正确（不再 NameError 静默失败）
    mock_record.assert_called_once()
    kw = mock_record.call_args.kwargs
    assert kw["call_type"] == "Vision API"
    assert kw["model"] == DEEPSEEK_VISION_MODEL
    assert isinstance(kw["latency_ms"], int) and kw["latency_ms"] >= 0
    assert kw["prompt_tokens"] == 500
    assert kw["completion_tokens"] == 200


@patch("skill.utils.admin_store.record_api_usage")
@patch("skill.utils.http_client._get_headers", return_value={"Authorization": "Bearer x"})
@patch("requests.post")
def test_itinerary_vision_uses_vision_model_and_records_latency(
    mock_post, mock_headers, mock_record, tmp_path, fresh_db
):
    img = _make_image(tmp_path)
    mock_post.return_value = _mock_tool_calls_response(
        "extract_itinerary", '{"总金额_元":"30.00","行程详情":[]}'
    )

    result = ocr_extract_itinerary(img)

    assert "_error" not in result
    assert result["总金额_元"] == "30.00"

    payload = mock_post.call_args.kwargs["json"]
    assert payload["model"] == DEEPSEEK_VISION_MODEL

    mock_record.assert_called_once()
    kw = mock_record.call_args.kwargs
    assert kw["call_type"] == "Vision API"
    assert kw["model"] == DEEPSEEK_VISION_MODEL
    assert isinstance(kw["latency_ms"], int) and kw["latency_ms"] >= 0
