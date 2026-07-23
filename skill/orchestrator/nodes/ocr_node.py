"""OCR 节点：发票字段提取 + 报销单/发票持久化

封装工具 ``ocr_extract_invoice``，OCR 失败时置 ``final_status=ERROR``，
由 ``route_after_ocr`` 条件边提前结束流程。
"""

from __future__ import annotations

import logging
from typing import Any

from ...tools.tool_ocr_extract import ocr_extract_invoice
from ...utils.db_store import (
    save_ai_check_result,
    save_invoice,
)
from ..state import CheckStatus, ReimbursementState

logger = logging.getLogger(__name__)


def ocr_node(state: ReimbursementState) -> dict[str, Any]:
    """功能1：OCR 提取发票全部内容"""
    pdf_path = state["pdf_path"]
    logger.info("▶ 功能1: OCR 提取发票内容 (%s)", pdf_path)

    ocr_result = ocr_extract_invoice(pdf_path)

    # OCR 失败：置错误状态，由条件边提前结束
    if "_error" in ocr_result:
        logger.warning("✗ 功能1 失败: %s", ocr_result["_error"])
        return {
            "ocr_result": ocr_result,
            "final_status": CheckStatus.ERROR,
            "summary": f"OCR 提取失败: {ocr_result['_error']}",
            "errors": [ocr_result["_error"]],
        }

    # DeepSeek 大模型已停用：无法执行 OCR/校验，给出明确提示
    if ocr_result.get("_disabled"):
        msg = (
            "DeepSeek 大模型已停用（系统配置），无法执行发票 OCR 与 AI 校验，"
            "请在系统配置中启用 DeepSeek 大模型"
        )
        logger.warning("✗ 功能1 不可用: %s", msg)
        return {
            "ocr_result": ocr_result,
            "final_status": CheckStatus.ERROR,
            "summary": msg,
            "errors": [msg],
        }

    invoice_amount = ocr_result.get("发票金额", 0)
    logger.info("✓ 功能1 完成, 发票金额: %s", invoice_amount)

    # ── 持久化：仅保存发票 + OCR 结果（若有 request_id）──
    # 报销单不再在此处创建：通过/拦截单由后续状态节点预建，预警单推迟到「提交审批」时创建。
    request_id = state.get("request_id")
    if request_id:
        try:
            save_invoice(ocr_result, request_id, pdf_path)
            save_ai_check_result(
                request_id,
                "OCR提取",
                "通过",
                {
                    "发票金额": invoice_amount,
                    "发票号码": ocr_result.get("发票号码", ""),
                },
            )
        except Exception as e:
            logger.warning("持久化异常（非致命）: %s", e)

    return {"ocr_result": ocr_result}
