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
import time
from pathlib import Path
from typing import Any

from ..config import LOCAL_OCR_ENGINE
from ..schemas.invoice_schema import EXTRACT_INVOICE_TOOL
from ..utils.admin_store import record_api_usage
from ..utils.http_client import call_deepseek_function
from ..utils.pdf_extractor import extract_pdf_text
from ..utils.progress import STATUS_INFO, STEP_OCR, emit_progress
from ..utils.structured_log import get_request_id

logger = logging.getLogger(__name__)

# 支持的图片类型
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}

# 触发 Vision 补全重试的「校验必需」字段：缺这些会导致下游分类/查验/异常无法正确进行
# （发票号码用于重复报销与格式校验、开票日期用于过期/日期异常、发票金额用于分类限额）。
# 购买方名称/销售方名称为辅助信息，即便首轮 Vision 漏识别也不再强制整图重跑 Vision，
# 避免为小字段放大耗时（优化点：详见 tests/perf_e2e_latency.py 的 image_vision 基线）。
# 注：名称类字段缺失不再触发重试，其异常级别已在异常检测中降为「警告」，不会误拦截有效发票。
VISION_RETRY_ESSENTIAL_FIELDS = ["发票号码", "开票日期", "发票金额"]


def _record_local_ocr(latency_ms: int, ok: bool) -> None:
    """将本地 OCR（Tesseract）耗时写入 ``api_usage``，补全发票智能体全链路计时。

    仅在真实请求上下文（``request_id`` 已设置）下记录，避免单元测试污染数据库。
    """
    rid = get_request_id()
    if rid == "unknown":
        return
    record_api_usage(
        call_type="本地OCR",
        model=LOCAL_OCR_ENGINE,
        latency_ms=latency_ms,
        status="成功" if ok else "失败",
        request_id=rid,
    )

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
    emit_progress(STEP_OCR, STATUS_INFO, f"已读取 PDF 文本层（{len(raw_text)} 字符），结构化中…")

    # ② 调用 DeepSeek Function Call
    return _extract_from_text(raw_text, SYSTEM_PROMPT)


def _ocr_extract_image(image_path: str) -> dict[str, Any]:
    """图片 OCR：本地 OCR 引擎抽取文本 → DeepSeek 文本管线。

    本地 OCR 不可用（如未安装 tesseract/pytesseract）时：
      - 若 OCR_VISION_FALLBACK_ENABLED=True → 降级 DeepSeek Vision 直接看图识别
      - 否则 → 直接返回 _error（链路只走「本地 OCR + 文本 Function Call」）
    """
    from ..config import OCR_VISION_FALLBACK_ENABLED
    from ..utils.image_ocr import extract_image_text
    from ..utils.ocr_fallback import ocr_image_via_vision

    ocr_t0 = time.perf_counter()
    emit_progress(STEP_OCR, STATUS_INFO, "本地 OCR 识别图片文字中…")
    try:
        raw_text = extract_image_text(image_path)
    except FileNotFoundError as e:
        return {"_error": str(e)}
    except Exception as e:
        # 本地 OCR 引擎不可用（ImportError）或识别失败（RuntimeError）等
        ocr_ms = int((time.perf_counter() - ocr_t0) * 1000)
        _record_local_ocr(ocr_ms, ok=False)
        if not OCR_VISION_FALLBACK_ENABLED:
            logger.warning("本地 OCR 失败 (耗时 %dms)，且 Vision 降级已禁用，返回错误: %s", ocr_ms, e)
            return {
                "_error": f"本地 OCR 失败且 Vision 降级已禁用（OCR_VISION_FALLBACK_ENABLED=false）: {e}"
            }
        logger.warning("本地 OCR 失败 (耗时 %dms)，降级为 DeepSeek Vision 直接识别图片: %s", ocr_ms, e)
        emit_progress(STEP_OCR, STATUS_INFO, "本地 OCR 不可用，降级 Vision 识别中…")
        return ocr_image_via_vision(
            image_path,
            tool_def=EXTRACT_INVOICE_TOOL,
            essential_fields=VISION_RETRY_ESSENTIAL_FIELDS,
            system_prompt=SYSTEM_PROMPT,
            reason=str(e),
        )

    # 本地 OCR 成功：记录耗时，再走 DeepSeek 文本管线
    ocr_ms = int((time.perf_counter() - ocr_t0) * 1000)
    _record_local_ocr(ocr_ms, ok=True)
    logger.info("本地 OCR 识别出 %d 字符 (耗时 %dms), 调用 DeepSeek Function Call 结构化发票字段", len(raw_text), ocr_ms)
    emit_progress(
        STEP_OCR,
        STATUS_INFO,
        f"本地 OCR 完成（{ocr_ms / 1000:.1f}s / {len(raw_text)} 字符），大模型结构化中…",
    )
    result = _extract_from_text(raw_text, OCR_TEXT_SYSTEM_PROMPT)

    # DeepSeek 文本管线被停用（_disabled）：直接透传，不再做字段补全（修复发现 E）
    if isinstance(result, dict) and result.get("_disabled"):
        return result

    # ② 即使本地 OCR 成功，也检查「校验必需」字段是否遗漏；若有且 Vision 降级启用，
    #    则追加 Vision 降级做聚焦重试（仅 OCR_VISION_FALLBACK_ENABLED=True 时）。
    #    仅对校验必需字段（号码/日期/金额）触发重试，购买方/销售方名称漏识别不再整图重跑，
    #    避免为小字段放大耗时（优化点）。
    if (
        isinstance(result, dict)
        and not result.get("_error")
        and OCR_VISION_FALLBACK_ENABLED
    ):
        missing = [f for f in VISION_RETRY_ESSENTIAL_FIELDS if not result.get(f)]
        if missing:
            logger.warning("本地 OCR 文本管线遗漏字段 %s，追加 Vision 降级", missing)
            emit_progress(STEP_OCR, STATUS_INFO, f"补全遗漏字段 {'、'.join(missing)} 中…")
            vision_result = ocr_image_via_vision(
                image_path,
                tool_def=EXTRACT_INVOICE_TOOL,
                essential_fields=VISION_RETRY_ESSENTIAL_FIELDS,
                system_prompt=SYSTEM_PROMPT,
                reason=f"文本管线遗漏字段: {missing}",
            )
            # DeepSeek 停用：直接透传，不再合并
            if isinstance(vision_result, dict) and vision_result.get("_disabled"):
                return vision_result
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


# 注：原 _ocr_extract_image_by_vision 已抽离为共享模块
# skill.utils.ocr_fallback.ocr_image_via_vision，供发票/行程单图片及扫描件 PDF
# 统一复用，消除兜底策略不一致（发现 A）。


def _ocr_extract_scanned_pdf(pdf_path: str) -> dict[str, Any]:
    """扫描件 PDF：渲染页图 → 本地 OCR → DeepSeek Function Call 文本管线。

    本地 OCR 整体失败（全部页无文字）时：
      - 若 OCR_VISION_FALLBACK_ENABLED=True → 降级 DeepSeek Vision（渲染首页为图）
      - 否则 → 直接返回 _error
    """
    from ..config import OCR_RENDER_DPI, OCR_VISION_FALLBACK_ENABLED
    from ..utils.image_ocr import extract_scanned_pdf_text
    from ..utils.ocr_fallback import ocr_image_via_vision, render_pdf_first_page

    ocr_t0 = time.perf_counter()
    emit_progress(STEP_OCR, STATUS_INFO, "渲染扫描件并本地 OCR 识别中…")
    try:
        raw_text = extract_scanned_pdf_text(pdf_path, dpi=OCR_RENDER_DPI)
    except FileNotFoundError as e:
        return {"_error": str(e)}
    except ImportError as e:
        return {"_error": f"本地 OCR 依赖缺失: {e}"}
    except RuntimeError as e:
        # 本地 OCR 完全失败（全部页无文字）
        ocr_ms = int((time.perf_counter() - ocr_t0) * 1000)
        _record_local_ocr(ocr_ms, ok=False)
        if not OCR_VISION_FALLBACK_ENABLED:
            logger.warning("扫描件 PDF 本地 OCR 失败 (耗时 %dms)，且 Vision 降级已禁用，返回错误: %s", ocr_ms, e)
            return {
                "_error": f"扫描件 PDF 本地 OCR 失败且 Vision 降级已禁用（OCR_VISION_FALLBACK_ENABLED=false）: {e}"
            }
        logger.warning("扫描件 PDF 本地 OCR 失败 (耗时 %dms)，降级 DeepSeek Vision: %s", ocr_ms, e)
        first_page = render_pdf_first_page(pdf_path, dpi=OCR_RENDER_DPI)
        if first_page:
            try:
                return ocr_image_via_vision(
                    first_page,
                    tool_def=EXTRACT_INVOICE_TOOL,
                    essential_fields=VISION_RETRY_ESSENTIAL_FIELDS,
                    system_prompt=SYSTEM_PROMPT,
                    reason=f"扫描件PDF本地OCR失败: {e}",
                )
            finally:
                Path(first_page).unlink(missing_ok=True)
        return {"_error": f"扫描件 PDF 本地 OCR 失败且无可用 Vision 兜底: {e}"}
    except Exception as e:
        return {"_error": f"扫描件 PDF 本地 OCR 失败: {e}"}

    ocr_ms = int((time.perf_counter() - ocr_t0) * 1000)
    _record_local_ocr(ocr_ms, ok=True)
    logger.info("扫描件 PDF 本地 OCR 识别出 %d 字符 (耗时 %dms)", len(raw_text), ocr_ms)
    emit_progress(
        STEP_OCR,
        STATUS_INFO,
        f"本地 OCR 完成（{ocr_ms / 1000:.1f}s / {len(raw_text)} 字符），大模型结构化中…",
    )
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
