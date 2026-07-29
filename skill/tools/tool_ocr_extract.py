"""功能1：OCR 提取发票全部内容（方案 A：本地 OCR + DeepSeek 大脑）

流程：
    - PDF（含文本层）→ PyMuPDF 提取文本 → DeepSeek Function Call 结构化输出
    - 图片 → 本地 OCR 引擎（PaddleOCR / Tesseract）抽取文本 → 同上文本管线
    - 扫描件 PDF（无文本层）→ PyMuPDF 渲染页图 → 本地 OCR → 同上文本管线

即：所有票据最终统一走「文本 → DeepSeek Function Call」管线，
DeepSeek 仅作为结构化提取的大脑，不再依赖其原生多模态（Vision）能力。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ..schemas.invoice_schema import EXTRACT_INVOICE_TOOL
from ..utils.http_client import call_deepseek_function
from ..utils.pdf_extractor import extract_pdf_text

logger = logging.getLogger(__name__)

# 支持的图片类型
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}

# 触发 Vision 补全重试的「校验必需」字段：缺这些会导致下游分类/查验/异常无法正确进行
# （发票号码用于重复报销与格式校验、开票日期用于过期/日期异常、发票金额用于分类限额）。
# 购买方名称/销售方名称为辅助信息，即便首轮 Vision 漏识别也不再强制整图重跑 Vision，
# 避免为小字段放大耗时（优化点：详见 tests/perf_e2e_latency.py 的 image_vision 基线）。
# 注：名称类字段缺失不再触发重试，其异常级别已在异常检测中降为「警告」，不会误拦截有效发票。
VISION_RETRY_ESSENTIAL_FIELDS = ["发票号码", "开票日期", "发票金额"]

SYSTEM_PROMPT = (
    "你是发票数据提取助手。\n"
    "\n"
    "工作流程：\n"
    "1. 从用户提供的发票文本中精确提取全部字段\n"
    "2. 将「价税合计小写」数值填到「发票金额」字段\n"
    "3. 商品明细逐项提取，放入「商品明细」数组\n"
    "4. 必须调用 extract_invoice 函数返回结构化结果\n"
    '5. 无数据的字段填空字符串 ""，无数据的数字填 0\n'
    "6. 不要编造未在文本中出现的字段值\n"
    "请务必完整提取购买方名称、销售方名称等全部字段；购买方名称常位于票面下方，切勿遗漏。"
)

# 图片经本地 OCR 后的文本可能存在识别噪声，提示模型容错
OCR_TEXT_SYSTEM_PROMPT = (
    SYSTEM_PROMPT + "\n7. 该文本由本地 OCR 引擎从发票图片识别而来，可能存在少量错字或乱序，"
    "请结合发票常见版式推断字段归属，但不要编造不存在的内容"
)


def _is_image_file(file_path: str) -> bool:
    """判断文件是否为图片类型"""
    ext = Path(file_path).suffix.lower()
    return ext in IMAGE_EXTENSIONS


def ocr_extract_invoice(file_path: str) -> dict[str, Any]:
    """功能1：从发票文件中 OCR 提取全部内容

    Args:
        file_path: 发票文件路径（支持 PDF / JPG / PNG 等）

    Returns:
        结构化发票数据字典，包含发票头、购销方、金额明细、商品明细等；
        若失败，返回包含 ``_error`` 键的错误字典。
    """
    # 路由：图片走本地 OCR 抽文本，PDF 走文本层提取；最终统一进入文本管线
    if _is_image_file(file_path):
        return _ocr_extract_image(file_path)

    return _ocr_extract_pdf(file_path)


def _ocr_extract_pdf(pdf_path: str) -> dict[str, Any]:
    """PDF OCR：PyMuPDF 提取文本 → DeepSeek Function Call"""
    # ① 提取 PDF 文本
    try:
        raw_text = extract_pdf_text(pdf_path)
    except FileNotFoundError as e:
        return {"_error": str(e)}
    except ImportError as e:
        return {"_error": f"依赖缺失: {e}"}
    except RuntimeError as e:
        # 扫描件（无文本层），降级为「渲染页图 → 本地 OCR」
        logger.warning("PDF 无文本层，降级为本地 OCR 处理: %s", e)
        return _ocr_extract_scanned_pdf(pdf_path)
    except Exception as e:
        return {"_error": f"PDF 读取失败: {e}"}

    logger.info("提取到 %d 字符, 调用 DeepSeek Function Call ...", len(raw_text))

    # ② 调用 DeepSeek Function Call
    return _extract_from_text(raw_text, SYSTEM_PROMPT)


def _ocr_extract_image(image_path: str) -> dict[str, Any]:
    """图片 OCR：优先本地 OCR → DeepSeek 文本管线；本地 OCR 不可用或漏字段时
    降级为 DeepSeek Vision 直接识别图片。

    修复：用户机器未安装 Tesseract/PaddleOCR 时图片无法识别（PDF 仍可走文本层）。
    此外，即使本地 OCR 成功但 DeepSeek 文本管线漏了关键字段（如购买方名称），
    也会触发 Vision 降级做一次聚焦重试，最大程度补全字段。
    """
    from ..utils.image_ocr import extract_image_text

    # ① 优先本地 OCR（依赖较重，用户机器可能未安装 Tesseract/PaddleOCR）
    result: dict[str, Any] = {}
    try:
        raw_text = extract_image_text(image_path)
        logger.info("本地 OCR 识别出 %d 字符, 调用 DeepSeek Function Call ...", len(raw_text))
        result = _extract_from_text(raw_text, OCR_TEXT_SYSTEM_PROMPT)
    except FileNotFoundError as e:
        return {"_error": str(e)}
    except Exception as e:
        # 本地 OCR 引擎不可用（ImportError）或识别失败（RuntimeError）等 → 降级 Vision
        logger.warning("本地 OCR 失败，降级为 DeepSeek Vision 直接识别图片: %s", e)
        return _ocr_extract_image_by_vision(image_path, reason=str(e))

    # ② 即使本地 OCR 成功，也检查「校验必需」字段是否遗漏；若有则追加 Vision 降级做聚焦重试。
    #    仅对校验必需字段（号码/日期/金额）触发重试，购买方/销售方名称漏识别不再整图重跑，
    #    避免为小字段放大耗时（优化点）。
    if isinstance(result, dict) and not result.get("_error"):
        missing = [f for f in VISION_RETRY_ESSENTIAL_FIELDS if not result.get(f)]
        if missing:
            logger.warning("本地 OCR 文本管线遗漏字段 %s，追加 Vision 降级", missing)
            vision_result = _ocr_extract_image_by_vision(
                image_path, reason=f"文本管线遗漏字段: {missing}"
            )
            if (
                isinstance(vision_result, dict)
                and not vision_result.get("_error")
                and not vision_result.get("_warning")
            ):
                for f in missing:
                    if vision_result.get(f):
                        result[f] = vision_result[f]
                        logger.info("Vision 降级补全字段: %s", f)
                for k, v in vision_result.items():
                    if k not in result or not result.get(k):
                        if k not in ("_error", "_warning", "_fallback_reason", "_retry_missing"):
                            result[k] = v
                still_missing = [f for f in missing if not result.get(f)]
                if not still_missing:
                    logger.info("Vision 降级后所有关键字段均已齐备")
    return result


def _ocr_extract_image_by_vision(image_path: str, reason: str = "") -> dict[str, Any]:
    """降级识别：图片 → base64 data URL → DeepSeek Vision (Function Call with image)。

    首轮调用后检查关键必填字段（购买方名称 / 销售方名称 / 发票号码 / 开票日期），
    若有遗漏则执行一次聚焦重试（让模型重点关注遗漏区域），合并两轮结果。

    用于本地 OCR（Paddle/Tesseract）不可用时的兜底识别。
    模型由 :data:`DEEPSEEK_VISION_MODEL` 或管理员配置的 ``deepseek_vision_model`` 指定。
    """
    import base64
    import mimetypes

    from ..utils.http_client import call_deepseek_vision

    try:
        with open(image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
    except Exception as e:
        return {"_error": f"读取图片失败: {e}"}
    mime = mimetypes.guess_type(image_path)[0] or "image/png"
    data_url = f"data:{mime};base64,{b64}"

    # ① 首轮 Vision 调用
    logger.info("调用 DeepSeek Vision 直接识别图片: %s (降级原因: %s)", image_path, reason)
    result = call_deepseek_vision(
        system_prompt=SYSTEM_PROMPT,
        image_data_url=data_url,
        tools=EXTRACT_INVOICE_TOOL,
        call_type="发票OCR提取(vision)",
    )
    if isinstance(result, dict) and (result.get("_error") or result.get("_warning")):
        result["_fallback_reason"] = f"本地 OCR 失败: {reason}"
        return result

    # ② 仅检查「校验必需」字段是否遗漏；若有则做一次聚焦重试。
    #    购买方/销售方名称漏识别不再触发整图重跑（其异常级别已降为警告，不会误拦截）。
    critical_fields = VISION_RETRY_ESSENTIAL_FIELDS
    missing = [
        f for f in critical_fields if not (result.get(f) if isinstance(result, dict) else None)
    ]
    if missing:
        logger.warning("Vision 首次识别遗漏字段 %s，执行聚焦重试", missing)
        retry_hint = (
            f"第一次识别遗漏了以下字段：{'、'.join(missing)}。"
            f"请重新仔细观察整张图片，重点关注这些字段对应的版式区域，确保完整提取。"
        )
        result2 = call_deepseek_vision(
            system_prompt=SYSTEM_PROMPT,
            image_data_url=data_url,
            tools=EXTRACT_INVOICE_TOOL,
            call_type="发票OCR提取(vision·重试)",
            text_hint=retry_hint,
        )
        if isinstance(result2, dict) and not result2.get("_error") and not result2.get("_warning"):
            # 合并：缺失字段优先补全，其它字段仅在原结果为空时更新
            for f in missing:
                if result2.get(f):
                    result[f] = result2[f]
                    logger.info("聚焦重试补全字段: %s", f)
            for k, v in result2.items():
                if k not in result or not result.get(k):
                    if k not in ("_error", "_warning", "_fallback_reason", "_retry_missing"):
                        result[k] = v
        still_missing = [f for f in critical_fields if not result.get(f)]
        if still_missing:
            result["_retry_missing"] = still_missing
            logger.warning("聚焦重试后仍遗漏字段: %s", still_missing)
        else:
            logger.info("聚焦重试后所有关键字段均已补全")

    result["_fallback_reason"] = f"本地 OCR 失败: {reason}"
    return result


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

    logger.info("扫描件 PDF 本地 OCR 识别出 %d 字符", len(raw_text))
    return _extract_from_text(raw_text, OCR_TEXT_SYSTEM_PROMPT)


def _extract_from_text(raw_text: str, system_prompt: str) -> dict[str, Any]:
    """统一文本管线：发票文本 → DeepSeek Function Call 结构化输出"""
    user_content = f"发票文本：\n{raw_text}"
    return call_deepseek_function(
        system_prompt=system_prompt,
        user_content=user_content,
        tools=EXTRACT_INVOICE_TOOL,
        call_type="发票OCR提取",
    )
