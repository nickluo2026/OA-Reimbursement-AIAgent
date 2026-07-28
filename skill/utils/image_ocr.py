"""本地 OCR 引擎封装（方案 A：本地 OCR 抽文本 → DeepSeek Function Call 文本管线）

图片发票/行程单不再依赖大模型原生多模态能力：
    图片 / 扫描件 PDF → 本地 OCR 引擎抽取文本 → 与 PDF 文本层完全相同的
    「文本 → DeepSeek Function Call」管线（DeepSeek 仍作为结构化提取的大脑）。

支持两种引擎（经环境变量 ``LOCAL_OCR_ENGINE`` 选择）：
    - paddle:    PaddleOCR（中文识别更准，依赖较重）
                 pip install paddleocr paddlepaddle
    - tesseract: Tesseract + pytesseract（轻量，需系统安装 tesseract 与中文语言包）
                 macOS: brew install tesseract tesseract-lang
                 Ubuntu: apt install tesseract-ocr tesseract-ocr-chi-sim
    - auto（默认）: 优先 PaddleOCR，不可用时回退 pytesseract
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

# PaddleOCR 引擎单例（模型加载较慢，进程内复用）
_paddle_ocr = None


def _paddle_available() -> bool:
    try:
        import paddleocr  # noqa: F401

        return True
    except Exception:
        return False


def _tesseract_available() -> bool:
    try:
        import pytesseract
        from PIL import Image  # noqa: F401

        from ..config import TESSERACT_CMD

        if TESSERACT_CMD:
            pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def _resolve_engine() -> str:
    """根据配置与依赖可用性确定 OCR 引擎名。

    Raises:
        ImportError: 无任何可用的本地 OCR 引擎
    """
    from ..config import LOCAL_OCR_ENGINE

    engine = (LOCAL_OCR_ENGINE or "auto").lower()
    if engine == "paddle":
        if not _paddle_available():
            raise ImportError("PaddleOCR 未安装，请运行: pip install paddleocr paddlepaddle")
        return "paddle"
    if engine == "tesseract":
        if not _tesseract_available():
            raise ImportError(
                "Tesseract 不可用，请安装 pytesseract 并确保系统已安装 tesseract "
                "及中文语言包（macOS: brew install tesseract tesseract-lang）"
            )
        return "tesseract"
    # auto：优先 PaddleOCR（中文更准），回退 Tesseract
    if _paddle_available():
        return "paddle"
    if _tesseract_available():
        return "tesseract"
    raise ImportError(
        "未检测到可用的本地 OCR 引擎。请安装以下任一引擎：\n"
        "  - PaddleOCR（推荐，中文更准）: pip install paddleocr paddlepaddle\n"
        "  - Tesseract: pip install pytesseract pillow 且系统安装 tesseract 与 chi_sim 语言包"
    )


def _ocr_with_paddle(image_path: str) -> str:
    """PaddleOCR 识别图片，返回按识别顺序拼接的文本。"""
    global _paddle_ocr

    from paddleocr import PaddleOCR

    if _paddle_ocr is None:
        try:
            # PaddleOCR 2.x 参数
            _paddle_ocr = PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)
        except TypeError:
            # PaddleOCR 3.x 移除了 show_log / use_angle_cls
            _paddle_ocr = PaddleOCR(lang="ch")

    try:
        result = _paddle_ocr.ocr(image_path, cls=True)
    except TypeError:
        result = _paddle_ocr.ocr(image_path)

    lines: list[str] = []
    for page in result or []:
        if page is None:
            continue
        # PaddleOCR 3.x：结果对象支持字典式访问，文本在 rec_texts
        rec_texts = None
        try:
            rec_texts = page["rec_texts"]
        except Exception:
            rec_texts = None
        if rec_texts is not None:
            lines.extend(t.strip() for t in rec_texts if isinstance(t, str) and t.strip())
            continue
        # PaddleOCR 2.x：[[box, (text, confidence)], ...]
        for item in page:
            try:
                text = item[1][0]
            except Exception:
                continue
            if isinstance(text, str) and text.strip():
                lines.append(text.strip())
    return "\n".join(lines)


def _ocr_with_tesseract(image_path: str) -> str:
    """Tesseract 识别图片，返回文本。"""
    import pytesseract
    from PIL import Image

    from ..config import TESSERACT_CMD, TESSERACT_LANG

    if TESSERACT_CMD:
        pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD

    with Image.open(image_path) as img:
        # 转 RGB，避免 RGBA/P 模式在部分 tesseract 版本下报错
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        text = pytesseract.image_to_string(img, lang=TESSERACT_LANG)
    return text


def extract_image_text(image_path: str) -> str:
    """本地 OCR 引擎从图片中抽取文本。

    Args:
        image_path: 图片文件路径（jpg/png/bmp/webp 等）

    Returns:
        OCR 识别出的文本（按行拼接）

    Raises:
        FileNotFoundError: 文件不存在
        ImportError: 本地 OCR 引擎依赖缺失
        RuntimeError: OCR 未识别出任何文字
    """
    if not os.path.exists(image_path):
        raise FileNotFoundError(f"文件不存在: {image_path}")

    engine = _resolve_engine()
    logger.info("本地 OCR（%s）识别图片: %s", engine, image_path)

    if engine == "paddle":
        text = _ocr_with_paddle(image_path)
    else:
        text = _ocr_with_tesseract(image_path)

    text = (text or "").strip()
    if not text:
        raise RuntimeError(f"本地 OCR（{engine}）未能从图片中识别出文字: {image_path}")

    logger.info("本地 OCR（%s）识别出 %d 字符", engine, len(text))
    return text


def extract_scanned_pdf_text(pdf_path: str, dpi: int = 200) -> str:
    """扫描件 PDF（无文本层）：PyMuPDF 渲染每页为图片 → 本地 OCR 抽文本。

    Args:
        pdf_path: PDF 文件路径
        dpi: 渲染分辨率（越高越清晰，OCR 越慢）

    Returns:
        各页 OCR 文本，以 ``\\n--- 分页 ---\\n`` 分隔

    Raises:
        ImportError: pymupdf 或本地 OCR 引擎缺失
        FileNotFoundError: 文件不存在
        RuntimeError: 全部页面均未识别出文字
    """
    try:
        import fitz  # PyMuPDF
    except ImportError as e:
        raise ImportError("pymupdf 未安装，请运行: pip install pymupdf") from e

    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"文件不存在: {pdf_path}")

    pages: list[str] = []
    doc = fitz.open(pdf_path)
    try:
        zoom = dpi / 72.0
        matrix = fitz.Matrix(zoom, zoom)
        for page in doc:
            pix = page.get_pixmap(matrix=matrix)
            tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
            tmp_path = tmp.name
            tmp.close()
            try:
                pix.save(tmp_path)
                try:
                    pages.append(extract_image_text(tmp_path))
                except RuntimeError:
                    # 单页无文字不中断，继续处理后续页
                    continue
            finally:
                Path(tmp_path).unlink(missing_ok=True)
    finally:
        doc.close()

    if not pages:
        raise RuntimeError(f"扫描件 PDF 各页均未识别出文字: {pdf_path}")

    return "\n--- 分页 ---\n".join(pages)
