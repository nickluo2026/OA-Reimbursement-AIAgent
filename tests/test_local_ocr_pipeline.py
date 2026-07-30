"""方案 A 验收测试：图片走「本地 OCR 抽文本 → DeepSeek Function Call 文本管线」。

验证：
  - 发票/行程单图片先经本地 OCR 抽文本，再与 PDF 分支共用文本管线
    （call_deepseek_function，call_type 分别为「发票OCR提取」「行程单OCR提取」）
  - OCR 文本被拼入 user_content，且使用带 OCR 容错提示的 system_prompt
  - 本地 OCR 依赖缺失 / 识别失败时，仅当 OCR_VISION_FALLBACK_ENABLED=True 才降级
    DeepSeek Vision 直接识别图片（发票、行程单图片、发票/行程单扫描件 PDF 统一策略，解决发现 A）
  - 扫描件 PDF（无文本层）降级为「渲染页图 → 本地 OCR」后走同一文本管线
  - 兜底开关默认关闭：本地 OCR 失败时直接返回 _error，不调用任何多模态 API

不发起真实网络请求，不依赖真实 OCR 引擎（全部 mock；PDF 渲染用真实 PyMuPDF 生成空白页）。
"""

from __future__ import annotations

from unittest.mock import patch

from skill.tools.tool_itinerary_ocr import ocr_extract_itinerary
from skill.tools.tool_ocr_extract import ocr_extract_invoice

FAKE_INVOICE_TEXT = "发票号码: 12345678\n开票日期: 2026-07-01\n价税合计: 300.00"
FAKE_ITINERARY_TEXT = "行程单\n总金额: 30.00 元\n经济型 2026-06-08 09:30 北京"


def _make_image(tmp_path, name: str = "ticket.png") -> str:
    """生成一张真实（空白）PNG，供 Vision 兜底压缩流程使用（OCR 部分仍被 mock）。"""
    from PIL import Image

    p = tmp_path / name
    Image.new("RGB", (20, 20), (255, 255, 255)).save(p)
    return str(p)


def _make_blank_pdf(tmp_path, name: str = "scan.pdf") -> str:
    """生成一张无文本层的真实空白 PDF（供扫描件 PDF 渲染/Vision 兜底流程使用）。"""
    import fitz

    doc = fitz.open()
    doc.new_page()
    p = tmp_path / name
    doc.save(str(p))
    doc.close()
    return str(p)


# ═══════════════════════════════════════════════
# 发票图片：本地 OCR → 文本管线
# ═══════════════════════════════════════════════


@patch("skill.tools.tool_ocr_extract.call_deepseek_function")
@patch("skill.utils.image_ocr.extract_image_text", return_value=FAKE_INVOICE_TEXT)
def test_invoice_image_uses_local_ocr_then_text_pipeline(mock_ocr, mock_call, tmp_path):
    img = _make_image(tmp_path)
    mock_call.return_value = {"发票号码": "12345678", "发票金额": 300.00}

    result = ocr_extract_invoice(img)

    assert result["发票号码"] == "12345678"
    mock_ocr.assert_called_once_with(img)

    # 与 PDF 分支完全相同的文本管线：call_deepseek_function + 发票OCR提取
    mock_call.assert_called_once()
    kw = mock_call.call_args.kwargs
    assert kw["call_type"] == "发票OCR提取"
    assert FAKE_INVOICE_TEXT in kw["user_content"]
    assert kw["user_content"].startswith("发票文本：")
    # 使用带 OCR 容错提示的 system prompt
    assert "本地 OCR" in kw["system_prompt"]


@patch("skill.utils.http_client.call_deepseek_vision")
@patch("skill.tools.tool_ocr_extract.call_deepseek_function")
@patch(
    "skill.utils.image_ocr.extract_image_text",
    side_effect=ImportError("未检测到可用的本地 OCR 引擎"),
)
def test_invoice_image_ocr_dependency_missing_falls_back_to_vision(
    mock_ocr, mock_call, mock_vision, tmp_path, monkeypatch
):
    monkeypatch.setattr("skill.config.OCR_VISION_FALLBACK_ENABLED", True)
    img = _make_image(tmp_path)
    mock_vision.return_value = {
        "发票号码": "12345678",
        "开票日期": "2026-07-01",
        "发票金额": 300.00,
    }

    result = ocr_extract_invoice(img)

    # 本地 OCR 依赖缺失 + Vision 兜底启用 → 降级到 DeepSeek Vision 直接识别图片
    mock_vision.assert_called_once()
    assert "_error" not in result
    assert result.get("发票号码") == "12345678"


@patch("skill.utils.http_client.call_deepseek_vision")
@patch("skill.tools.tool_ocr_extract.call_deepseek_function")
@patch(
    "skill.utils.image_ocr.extract_image_text",
    side_effect=RuntimeError("本地 OCR（tesseract）未能从图片中识别出文字"),
)
def test_invoice_image_ocr_no_text_falls_back_to_vision(
    mock_ocr, mock_call, mock_vision, tmp_path, monkeypatch
):
    monkeypatch.setattr("skill.config.OCR_VISION_FALLBACK_ENABLED", True)
    img = _make_image(tmp_path)
    mock_vision.return_value = {
        "发票号码": "12345678",
        "开票日期": "2026-07-01",
        "发票金额": 300.00,
    }

    result = ocr_extract_invoice(img)

    # 本地 OCR 识别失败 + Vision 兜底启用 → 降级到 DeepSeek Vision 直接识别图片
    mock_vision.assert_called_once()
    assert "_error" not in result
    assert result.get("发票号码") == "12345678"


# ═══════════════════════════════════════════════
# 扫描件 PDF：渲染页图 → 本地 OCR → 文本管线；整体失败降级 Vision
# ═══════════════════════════════════════════════


@patch("skill.tools.tool_ocr_extract.call_deepseek_function")
@patch("skill.utils.image_ocr.extract_scanned_pdf_text", return_value=FAKE_INVOICE_TEXT)
@patch(
    "skill.tools.tool_ocr_extract.extract_pdf_text",
    side_effect=RuntimeError("PDF 无可提取文字（可能是扫描件）"),
)
def test_scanned_pdf_falls_back_to_local_ocr(mock_pdf, mock_scan_ocr, mock_call, tmp_path):
    pdf = _make_blank_pdf(tmp_path)
    mock_call.return_value = {"发票号码": "12345678", "发票金额": 300.00}

    result = ocr_extract_invoice(pdf)

    assert result["发票号码"] == "12345678"
    mock_scan_ocr.assert_called_once()
    assert mock_scan_ocr.call_args.args[0] == pdf

    kw = mock_call.call_args.kwargs
    assert kw["call_type"] == "发票OCR提取"
    assert FAKE_INVOICE_TEXT in kw["user_content"]


@patch("skill.utils.http_client.call_deepseek_vision")
@patch("skill.tools.tool_ocr_extract.call_deepseek_function")
@patch(
    "skill.utils.image_ocr.extract_scanned_pdf_text",
    side_effect=RuntimeError("本地 OCR（tesseract）未能从图片中识别出文字"),
)
def test_invoice_scanned_pdf_ocr_fails_falls_back_to_vision(
    mock_scan_ocr, mock_call, mock_vision, tmp_path, monkeypatch
):
    monkeypatch.setattr("skill.config.OCR_VISION_FALLBACK_ENABLED", True)
    pdf = _make_blank_pdf(tmp_path)
    mock_vision.return_value = {
        "发票号码": "12345678",
        "开票日期": "2026-07-01",
        "发票金额": 300.00,
    }

    result = ocr_extract_invoice(pdf)

    # 扫描件 PDF 本地 OCR 整体失败 + Vision 兜底启用 → 降级到 DeepSeek Vision（渲染首页为图）
    mock_vision.assert_called_once()
    assert "_error" not in result
    assert result.get("发票号码") == "12345678"


# ═══════════════════════════════════════════════
# 行程单图片：本地 OCR → 文本管线；失败降级 Vision（解决发现 A）
# ═══════════════════════════════════════════════


@patch("skill.tools.tool_itinerary_ocr.call_deepseek_function")
@patch("skill.utils.image_ocr.extract_image_text", return_value=FAKE_ITINERARY_TEXT)
def test_itinerary_image_uses_local_ocr_then_text_pipeline(mock_ocr, mock_call, tmp_path):
    img = _make_image(tmp_path, "itinerary.png")
    mock_call.return_value = {"总金额_元": "30.00", "行程详情": []}

    result = ocr_extract_itinerary(img)

    assert result["总金额_元"] == "30.00"
    mock_ocr.assert_called_once_with(img)

    mock_call.assert_called_once()
    kw = mock_call.call_args.kwargs
    assert kw["call_type"] == "行程单OCR提取"
    assert FAKE_ITINERARY_TEXT in kw["user_content"]
    assert kw["user_content"].startswith("行程单文本：")
    assert "本地 OCR" in kw["system_prompt"]


@patch("skill.utils.http_client.call_deepseek_vision")
@patch("skill.tools.tool_itinerary_ocr.call_deepseek_function")
@patch(
    "skill.utils.image_ocr.extract_image_text",
    side_effect=ImportError("未检测到可用的本地 OCR 引擎"),
)
def test_itinerary_image_ocr_dependency_missing_falls_back_to_vision(
    mock_ocr, mock_call, mock_vision, tmp_path, monkeypatch
):
    monkeypatch.setattr("skill.config.OCR_VISION_FALLBACK_ENABLED", True)
    img = _make_image(tmp_path, "itinerary.png")
    mock_vision.return_value = {"总金额_元": "30.00", "行程详情": []}

    result = ocr_extract_itinerary(img)

    # 本地 OCR 依赖缺失 + Vision 兜底启用 → 降级到 DeepSeek Vision 直接识别图片（解决发现 A）
    mock_vision.assert_called_once()
    assert "_error" not in result
    assert result.get("总金额_元") == "30.00"


@patch("skill.utils.http_client.call_deepseek_vision")
@patch("skill.tools.tool_itinerary_ocr.call_deepseek_function")
@patch(
    "skill.utils.image_ocr.extract_scanned_pdf_text",
    side_effect=RuntimeError("本地 OCR（tesseract）未能从图片中识别出文字"),
)
def test_itinerary_scanned_pdf_ocr_fails_falls_back_to_vision(
    mock_scan_ocr, mock_call, mock_vision, tmp_path, monkeypatch
):
    monkeypatch.setattr("skill.config.OCR_VISION_FALLBACK_ENABLED", True)
    pdf = _make_blank_pdf(tmp_path, "itinerary_scan.pdf")
    mock_vision.return_value = {"总金额_元": "30.00", "行程详情": []}

    result = ocr_extract_itinerary(pdf)

    # 行程单扫描件 PDF 本地 OCR 整体失败 + Vision 兜底启用 → 降级到 DeepSeek Vision（渲染首页为图）
    mock_vision.assert_called_once()
    assert "_error" not in result
    assert result.get("总金额_元") == "30.00"


# ═══════════════════════════════════════════════
# Vision 兜底开关（OCR_VISION_FALLBACK_ENABLED）默认关闭
# ═══════════════════════════════════════════════


@patch("skill.utils.http_client.call_deepseek_vision")
@patch("skill.tools.tool_ocr_extract.call_deepseek_function")
@patch(
    "skill.utils.image_ocr.extract_image_text",
    side_effect=ImportError("未检测到可用的本地 OCR 引擎"),
)
def test_invoice_image_ocr_fails_when_vision_fallback_disabled(
    mock_ocr, mock_call, mock_vision, tmp_path, monkeypatch
):
    monkeypatch.setattr("skill.config.OCR_VISION_FALLBACK_ENABLED", False)
    img = _make_image(tmp_path)

    result = ocr_extract_invoice(img)

    # 兜底关闭：本地 OCR 失败直接报错，不调用 DeepSeek Vision
    mock_vision.assert_not_called()
    assert "_error" in result
    assert "OCR_VISION_FALLBACK_ENABLED" in result["_error"]


@patch("skill.utils.http_client.call_deepseek_vision")
@patch("skill.tools.tool_ocr_extract.call_deepseek_function")
@patch(
    "skill.utils.image_ocr.extract_scanned_pdf_text",
    side_effect=RuntimeError("本地 OCR（tesseract）未能从图片中识别出文字"),
)
def test_invoice_scanned_pdf_ocr_fails_when_vision_fallback_disabled(
    mock_scan_ocr, mock_call, mock_vision, tmp_path, monkeypatch
):
    monkeypatch.setattr("skill.config.OCR_VISION_FALLBACK_ENABLED", False)
    pdf = _make_blank_pdf(tmp_path)

    result = ocr_extract_invoice(pdf)

    # 兜底关闭：扫描件 PDF 本地 OCR 失败直接报错，不调用 Vision
    mock_vision.assert_not_called()
    assert "_error" in result
    assert "OCR_VISION_FALLBACK_ENABLED" in result["_error"]


@patch("skill.utils.http_client.call_deepseek_vision")
@patch("skill.tools.tool_itinerary_ocr.call_deepseek_function")
@patch(
    "skill.utils.image_ocr.extract_image_text",
    side_effect=ImportError("未检测到可用的本地 OCR 引擎"),
)
def test_itinerary_image_ocr_fails_when_vision_fallback_disabled(
    mock_ocr, mock_call, mock_vision, tmp_path, monkeypatch
):
    monkeypatch.setattr("skill.config.OCR_VISION_FALLBACK_ENABLED", False)
    img = _make_image(tmp_path, "itinerary.png")

    result = ocr_extract_itinerary(img)

    # 兜底关闭：行程单图片本地 OCR 失败直接报错，不调用 Vision
    mock_vision.assert_not_called()
    assert "_error" in result
    assert "OCR_VISION_FALLBACK_ENABLED" in result["_error"]


# ═══════════════════════════════════════════════
# 引擎选择逻辑
# ═══════════════════════════════════════════════


def test_resolve_engine_prefers_paddle_in_auto_mode():
    from skill.utils import image_ocr

    with (
        patch.object(image_ocr, "_paddle_available", return_value=True),
        patch.object(image_ocr, "_tesseract_available", return_value=True),
        patch("skill.config.LOCAL_OCR_ENGINE", "auto"),
    ):
        assert image_ocr._resolve_engine() == "paddle"


def test_resolve_engine_falls_back_to_tesseract():
    from skill.utils import image_ocr

    with (
        patch.object(image_ocr, "_paddle_available", return_value=False),
        patch.object(image_ocr, "_tesseract_available", return_value=True),
        patch("skill.config.LOCAL_OCR_ENGINE", "auto"),
    ):
        assert image_ocr._resolve_engine() == "tesseract"


def test_resolve_engine_raises_when_no_engine():
    import pytest

    from skill.utils import image_ocr

    with (
        patch.object(image_ocr, "_paddle_available", return_value=False),
        patch.object(image_ocr, "_tesseract_available", return_value=False),
        patch("skill.config.LOCAL_OCR_ENGINE", "auto"),
    ):
        with pytest.raises(ImportError):
            image_ocr._resolve_engine()
