"""分类限额节点：费用分类 + 限额校验

封装工具 ``classify_and_check_limit``，超限置 ``final_status=WARNING``。
仅当 ``route_after_anomaly`` 判定金额 > 100 时可达。
"""

from __future__ import annotations

import logging
from typing import Any

from ...tools.tool_classify_limit import classify_and_check_limit
from ...utils.db_store import (
    save_ai_check_result,
    update_ai_status,
)
from ..state import CheckStatus, ReimbursementState

logger = logging.getLogger(__name__)


def classify_node(state: ReimbursementState) -> dict[str, Any]:
    """功能2：费用分类与限额校验"""
    ocr_result = state.get("ocr_result") or {}
    anomaly_result = state.get("anomaly_result") or {}
    conclusion = anomaly_result.get("总体结论", "通过")
    invoice_amount = ocr_result.get("发票金额", 0)

    logger.info("▶ 功能2: 分类限额校验 (金额 %.2f)", invoice_amount)
    classify_result = classify_and_check_limit(invoice=ocr_result)

    is_over_limit = classify_result.get("是否超限", False)
    request_id = state.get("request_id")

    if is_over_limit:
        logger.warning("✓ 功能2 完成, 费用超限: %s", classify_result.get("校验结果"))
        summary = (
            f"费用超限: {classify_result.get('校验结果', '')}。" f"异常检查结论: {conclusion}。"
        )
        if request_id:
            try:
                # 预警：不预建报销单，仅留痕 AI 校验结果，待「提交审批」时再建
                update_ai_status(request_id, "预警")
                save_ai_check_result(request_id, "分类限额", "预警", classify_result)
            except Exception as e:
                logger.warning("持久化异常（非致命）: %s", e)
        return {
            "classify_result": classify_result,
            "final_status": CheckStatus.WARNING,
            "summary": summary,
        }

    logger.info("✓ 功能2 完成, 限额通过")
    summary = (
        f"校验通过。费用分类: {classify_result.get('费用分类', '未知')}，"
        f"金额 {invoice_amount} 元 ≤ 限额 {classify_result.get('分类限额', 0)} 元。"
        f"异常检查结论: {conclusion}。"
    )
    if request_id:
        try:
            # 通过：不预建报销单，仅留痕 AI 校验结果与状态，待「提交审批」时再建
            update_ai_status(request_id, "通过")
            save_ai_check_result(request_id, "分类限额", "通过", classify_result)
        except Exception as e:
            logger.warning("持久化异常（非致命）: %s", e)

    return {"classify_result": classify_result, "summary": summary}
