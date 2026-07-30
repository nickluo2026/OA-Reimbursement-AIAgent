"""发票/行程单 OCR 真实引擎集成测试（按需运行，CI 默认跳过）。

仅当环境中存在可用本地 OCR 引擎（Tesseract / PaddleOCR）时运行；
否则用 pytest.skip 跳过，避免 CI 因缺少 OCR 引擎而失败。

运行方式：
    pytest -m ocr_integration          # 仅跑本文件（需本机已装 tesseract 及中文语言包）
    pytest tests/test_ocr_integration.py
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.ocr_integration

from skill.utils import image_ocr


def _make_invoice_image(tmp_path, name: str = "invoice.png") -> str:
    """生成一张含中文字段的发票样图（用系统字体渲染，供真实 OCR 识别）。"""
    from PIL import Image, ImageDraw, ImageFont

    fonts = [
        "/System/Library/Fonts/PingFang.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    ]
    font = None
    for fp in fonts:
        try:
            font = ImageFont.truetype(fp, 30)
            break
        except Exception:
            continue
    if font is None:
        font = ImageFont.load_default()

    img = Image.new("RGB", (800, 480), (255, 255, 255))
    d = ImageDraw.Draw(img)
    lines = [
        "增值税电子普通发票",
        "发票代码: 011001900311",
        "发票号码: 08826341",
        "价税合计: ￥1280.00",
        "销售方: 北京示例科技有限公司",
        "购买方: 张三",
        "开票日期: 2026-07-28",
    ]
    for i, ln in enumerate(lines):
        d.text((30, 20 + i * 60), ln, fill=(0, 0, 0), font=font)
    p = tmp_path / name
    img.save(p)
    return str(p)


def _require_engine() -> str:
    """返回可用 OCR 引擎名；若无则 skip。"""
    try:
        return image_ocr._resolve_engine()
    except Exception:
        pytest.skip("本地 OCR 引擎不可用，跳过集成测试")


def test_local_ocr_resolves_to_supported_engine():
    engine = _require_engine()
    assert engine in ("tesseract", "paddle")


def test_local_ocr_extracts_invoice_fields(tmp_path):
    _require_engine()
    img = _make_invoice_image(tmp_path)
    text = image_ocr.extract_image_text(img)
    assert isinstance(text, str) and len(text.strip()) > 0
    # 关键字段应能被本地 OCR 识别（忽略空格差异）
    joined = text.replace(" ", "").replace("　", "")
    assert ("发票号码" in joined) or ("发票代码" in joined) or ("价税合计" in joined)


def test_ocr_fallback_compress_runs_without_network(tmp_path):
    """压缩函数纯本地、不依赖网络；确保 Vision 兜底前处理可用。"""
    from skill.utils.ocr_fallback import compress_image_to_data_url

    img = _make_invoice_image(tmp_path, "c.png")
    data_url = compress_image_to_data_url(img)
    assert data_url.startswith("data:image/")
    assert ";base64," in data_url
