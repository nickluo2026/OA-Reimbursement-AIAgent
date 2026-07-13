"""异常检测节点：前置拦截

封装工具 ``detect_anomaly``，拦截时置 ``final_status=BLOCK``，
由 ``route_after_anomaly`` 条件边提前结束，跳过分类限额校验。
"""

from __future__ import annotations

import logging
from typing import Any

from ...tools.tool_anomaly_check import detect_anomaly
from ...utils.db_store import save_ai_check_result, update_ai_status
from ..state import CheckStatus, ReimbursementState

logger = logging.getLogger(__name__)


def anomaly_node(state: ReimbursementState) -> dict[str, Any]:
    """功能3：异常输入检查（前置拦截）"""
    ocr_result = state.get("ocr_result") or {}
    logger.info("▶ 功能3: 异常输入检查")

    anomaly_result = detect_anomaly(
        invoice=ocr_result,
        apply_amount=state.get("apply_amount"),
        apply_date=state.get("apply_date"),
    )

    conclusion = anomaly_result.get("总体结论", "通过")
    logger.info("✓ 功能3 完成, 总体结论: %s", conclusion)

    request_id = state.get("request_id")

    # 拦截：置 BLOCK 状态，由条件边提前结束
    if conclusion == "拦截":
        invoice_amount = ocr_result.get("发票金额", 0)
        summary = (
            f"异常检查拦截: {anomaly_result.get('检查摘要', '存在严重异常')}。"
            f"发票金额 {invoice_amount} 元，未执行分类限额校验。"
        )
        if request_id:
            try:
                update_ai_status(request_id, "拦截")
                save_ai_check_result(request_id, "异常检测", "拦截", anomaly_result)
            except Exception as e:
                logger.warning("持久化异常（非致命）: %s", e)
        return {
            "anomaly_result": anomaly_result,
            "final_status": CheckStatus.BLOCK,
            "summary": summary,
        }

    # 非拦截：保存检测结果
    if request_id:
        try:
            save_ai_check_result(request_id, "异常检测", conclusion, anomaly_result)
        except Exception as e:
            logger.warning("持久化异常（非致命）: %s", e)

    return {"anomaly_result": anomaly_result}
