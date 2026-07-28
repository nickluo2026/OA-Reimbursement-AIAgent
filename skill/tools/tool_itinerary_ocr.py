"""行程单 OCR 提取工具（方案 A：本地 OCR + DeepSeek 大脑）

流程与发票 OCR 一致：
    - PDF（含文本层）→ PyMuPDF 提取文本 → DeepSeek Function Call 结构化输出
    - 图片 → 本地 OCR 引擎（PaddleOCR / Tesseract）抽取文本 → 同上文本管线
    - 扫描件 PDF（无文本层）→ PyMuPDF 渲染页图 → 本地 OCR → 同上文本管线

使用 ``ITINERARY_EXTRACT_TOOL`` 作为 Function Call 工具定义，
提取行程明细数组（车型/上车时间/城市/起终点/里程/金额）。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ..schemas.itinerary_schema import ITINERARY_EXTRACT_TOOL
from ..utils.http_client import call_deepseek_function
from ..utils.pdf_extractor import extract_pdf_text

logger = logging.getLogger(__name__)

# 支持的图片类型
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}

SYSTEM_PROMPT = (
    "你是行程单数据提取助手。\n"
    "\n"
    "工作流程：\n"
    "1. 从用户提供的行程单文本中精确提取全部字段\n"
    "2. 行程详情逐项提取，放入「行程详情」数组（序号/车型/上车时间/城市/起终点/里程/金额）\n"
    "3. 「总金额_元」应等于所有行程明细金额之和\n"
    "4. 必须调用 extract_itinerary 函数返回结构化结果\n"
    '5. 无数据的字段填空字符串 ""，无数据的数字填 0\n'
    "6. 不要编造未在文本中出现的字段值"
)

# 图片经本地 OCR 后的文本可能存在识别噪声，提示模型容错
OCR_TEXT_SYSTEM_PROMPT = (
    SYSTEM_PROMPT + "\n7. 该文本由本地 OCR 引擎从行程单图片识别而来，可能存在少量错字或乱序，"
    "请结合行程单常见版式推断字段归属，但不要编造不存在的内容"
)


def _is_image_file(file_path: str) -> bool:
    """判断文件是否为图片类型"""
    ext = Path(file_path).suffix.lower()
    return ext in IMAGE_EXTENSIONS


def ocr_extract_itinerary(file_path: str) -> dict[str, Any]:
    """行程单 OCR 提取全部内容

    Args:
        file_path: 行程单文件路径（支持 PDF / JPG / PNG 等）

    Returns:
        结构化行程单数据字典；若失败，返回包含 ``_error`` 键的错误字典。
    """
    if _is_image_file(file_path):
        return _ocr_extract_image(file_path)

    return _ocr_extract_pdf(file_path)


def _ocr_extract_pdf(pdf_path: str) -> dict[str, Any]:
    """PDF OCR：PyMuPDF 提取文本 → DeepSeek Function Call"""
    try:
        raw_text = extract_pdf_text(pdf_path)
    except FileNotFoundError as e:
        return {"_error": str(e)}
    except ImportError as e:
        return {"_error": f"依赖缺失: {e}"}
    except RuntimeError as e:
        # 扫描件（无文本层），降级为「渲染页图 → 本地 OCR」
        logger.warning("行程单 PDF 无文本层，降级为本地 OCR 处理: %s", e)
        return _ocr_extract_scanned_pdf(pdf_path)
    except Exception as e:
        return {"_error": f"PDF 读取失败: {e}"}

    logger.info("行程单提取到 %d 字符, 调用 DeepSeek Function Call ...", len(raw_text))

    return _extract_from_text(raw_text, SYSTEM_PROMPT)


def _ocr_extract_image(image_path: str) -> dict[str, Any]:
    """图片 OCR：本地 OCR 引擎抽取文本 → DeepSeek Function Call 文本管线"""
    from ..utils.image_ocr import extract_image_text

    try:
        raw_text = extract_image_text(image_path)
    except FileNotFoundError as e:
        return {"_error": str(e)}
    except ImportError as e:
        return {"_error": f"本地 OCR 依赖缺失: {e}"}
    except RuntimeError as e:
        return {"_error": str(e)}
    except Exception as e:
        return {"_error": f"本地 OCR 识别失败: {e}"}

    logger.info("行程单本地 OCR 识别出 %d 字符, 调用 DeepSeek Function Call ...", len(raw_text))
    return _extract_from_text(raw_text, OCR_TEXT_SYSTEM_PROMPT)


def _ocr_extract_scanned_pdf(pdf_path: str) -> dict[str, Any]:
    """扫描件 PDF：渲染页图 → 本地 OCR → DeepSeek Function Call 文本管线"""
    from ..config import OCR_RENDER_DPI
    from ..utils.image_ocr import extract_scanned_pdf_text

    try:
        raw_text = extract_scanned_pdf_text(pdf_path, dpi=OCR_RENDER_DPI)
    except FileNotFoundError as e:
        return {"_error": str(e)}
    except ImportError as e:
        return {"_error": f"本地 OCR 依赖缺失: {e}"}
    except RuntimeError as e:
        return {"_error": str(e)}
    except Exception as e:
        return {"_error": f"扫描件 PDF 本地 OCR 失败: {e}"}

    logger.info("行程单扫描件 PDF 本地 OCR 识别出 %d 字符", len(raw_text))
    return _extract_from_text(raw_text, OCR_TEXT_SYSTEM_PROMPT)


def _extract_from_text(raw_text: str, system_prompt: str) -> dict[str, Any]:
    """统一文本管线：行程单文本 → DeepSeek Function Call 结构化输出"""
    user_content = f"行程单文本：\n{raw_text}"
    return call_deepseek_function(
        system_prompt=system_prompt,
        user_content=user_content,
        tools=ITINERARY_EXTRACT_TOOL,
        call_type="行程单OCR提取",
    )
