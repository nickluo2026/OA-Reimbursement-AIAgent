"""发票/行程单图片的 DeepSeek Vision 兜底识别（本地 OCR 不可用或漏字段时）。

把原先仅存在于 ``tool_ocr_extract`` 的 Vision 三级兜底逻辑抽为共享函数，
供发票与行程单工具复用，并为「扫描件 PDF」提供 Vision 兜底，
消除「发票有兜底、行程单/扫描件 PDF 无兜底」的不一致。

设计要点：
  - 发送 Vision 前先把图片等比压缩到最长边 ``OCR_VISION_MAX_SIDE``，
    避免超大发票图 base64 占用过量请求体 / token。
  - DeepSeek 停用（返回 ``_disabled``）时原样透传，不做字段补全，
    交由 ``ocr_node`` 统一置 ERROR（修复原发票工具在 ``_disabled`` 下仍做字段补全的边界问题）。
  - 校验必需字段遗漏时做一次聚焦重试并合并结果。
"""

from __future__ import annotations

import base64
import logging
import mimetypes
import os
import tempfile
from pathlib import Path

from PIL import Image

logger = logging.getLogger(__name__)

# Vision 发送前图片最长边压缩上限（px），避免超大发票图 base64 占用过量请求体/token
VISION_MAX_SIDE = int(os.getenv("OCR_VISION_MAX_SIDE", "1600"))


def compress_image_to_data_url(image_path: str, max_side: int = VISION_MAX_SIDE) -> str:
    """读取图片并等比压缩到最长边 ``max_side``，返回 base64 data URL。"""
    with Image.open(image_path) as img:
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        w, h = img.size
        long = max(w, h)
        if long > max_side:
            scale = max_side / float(long)
            img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        tmp_path = tmp.name
        tmp.close()
        try:
            img.save(tmp_path, format="PNG")
            with open(tmp_path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("ascii")
        finally:
            Path(tmp_path).unlink(missing_ok=True)
    mime = mimetypes.guess_type(image_path)[0] or "image/png"
    return f"data:{mime};base64,{b64}"


def render_pdf_first_page(pdf_path: str, dpi: int = 200) -> str | None:
    """把 PDF 首页渲染为临时 PNG 并返回路径；失败（如无 pymupdf/损坏）返回 None。

    供「扫描件 PDF 本地 OCR 失败」时转入 Vision 兜底使用。
    """
    try:
        import fitz  # PyMuPDF
    except Exception:
        return None
    try:
        doc = fitz.open(pdf_path)
        try:
            page = doc[0]
            zoom = dpi / 72.0
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
            tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
            tmp_path = tmp.name
            tmp.close()
            pix.save(tmp_path)
            return tmp_path
        finally:
            doc.close()
    except Exception as e:
        logger.warning("扫描件 PDF 首页渲染失败: %s", e)
        return None


def ocr_image_via_vision(
    image_path: str,
    *,
    tool_def: list,
    essential_fields: list[str],
    system_prompt: str,
    reason: str = "",
) -> dict:
    """图片 → 压缩 → DeepSeek Vision (Function Call) 兜底识别。

    流程：
      ① 首轮 Vision 识别
      ② 若返回 ``_disabled``（DeepSeek 停用）→ 原样返回（不做字段补全）
      ③ 若 ``_error`` / ``_warning`` → 附带 ``_fallback_reason`` 返回
      ④ 校验必需字段遗漏 → 聚焦重试一次并合并

    Args:
        image_path: 本地图片路径（会先压缩再发 Vision）
        tool_def: Function Call 工具定义（EXTRACT_INVOICE_TOOL / ITINERARY_EXTRACT_TOOL）
        essential_fields: 触发聚焦重试的必需字段
        system_prompt: 系统提示词
        reason: 降级原因（用于日志/标记）
    """
    from ..utils.http_client import call_deepseek_vision

    data_url = compress_image_to_data_url(image_path)
    logger.info("调用 DeepSeek Vision 直接识别图片: %s (降级原因: %s)", image_path, reason)
    result = call_deepseek_vision(
        system_prompt=system_prompt,
        image_data_url=data_url,
        tools=tool_def,
        call_type="OCR(Vision兜底)",
    )

    # DeepSeek 停用：直接透传，不做字段补全（由 ocr_node 统一置 ERROR）
    if isinstance(result, dict) and result.get("_disabled"):
        result["_fallback_reason"] = f"本地 OCR 失败: {reason}"
        return result

    if isinstance(result, dict) and (result.get("_error") or result.get("_warning")):
        result["_fallback_reason"] = f"本地 OCR 失败: {reason}"
        return result

    if not isinstance(result, dict):
        return result

    # 聚焦重试：仅对校验必需字段
    missing = [f for f in essential_fields if not result.get(f)]
    if missing:
        logger.warning("Vision 首次识别遗漏字段 %s，执行聚焦重试", missing)
        retry_hint = (
            f"第一次识别遗漏了以下字段：{'、'.join(missing)}。"
            f"请重新仔细观察整张图片，重点关注这些字段对应的版式区域，确保完整提取。"
        )
        result2 = call_deepseek_vision(
            system_prompt=system_prompt,
            image_data_url=data_url,
            tools=tool_def,
            call_type="OCR(Vision兜底·重试)",
            text_hint=retry_hint,
        )
        if (
            isinstance(result2, dict)
            and not result2.get("_error")
            and not result2.get("_warning")
            and not result2.get("_disabled")
        ):
            for f in missing:
                if result2.get(f):
                    result[f] = result2[f]
            for k, v in result2.items():
                if k not in result or not result.get(k):
                    if k not in ("_error", "_warning", "_fallback_reason", "_retry_missing"):
                        result[k] = v
            still_missing = [f for f in essential_fields if not result.get(f)]
            if still_missing:
                result["_retry_missing"] = still_missing
                logger.warning("聚焦重试后仍遗漏字段: %s", still_missing)
            else:
                logger.info("聚焦重试后所有关键字段均已补全")

    result["_fallback_reason"] = f"本地 OCR 失败: {reason}"
    return result
