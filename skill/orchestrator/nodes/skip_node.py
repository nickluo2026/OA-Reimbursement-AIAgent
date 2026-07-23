"""小额免审节点：金额 ≤ 阈值时跳过限额校验

由 ``route_after_anomaly`` 条件边在金额 ≤ 100 时路由进入，
填充免审分类结果，保持返回结构与正常校验一致。
"""

from __future__ import annotations

import logging
from typing import Any

from ...config import SMALL_AMOUNT_THRESHOLD
from ...utils.db_store import save_ai_check_result, update_ai_status
from ..state import ReimbursementState

logger = logging.getLogger(__name__)


def skip_node(state: ReimbursementState) -> dict[str, Any]:
    """小额免审：跳过分类限额校验"""
    ocr_result = state.get("ocr_result") or {}
    anomaly_result = state.get("anomaly_result") or {}
    conclusion = anomaly_result.get("总体结论", "通过")
    invoice_amount = ocr_result.get("发票金额", 0)

    logger.info(
        "▷ 跳过功能2: 金额 %.2f ≤ %.0f 元, 小额免审",
        invoice_amount,
        SMALL_AMOUNT_THRESHOLD,
    )

    classify_result = {
        "费用分类": "小额免审",
        "校验结果": f"金额 {invoice_amount} 元 ≤ {SMALL_AMOUNT_THRESHOLD} 元，免于限额校验",
    }
    anomaly_note = f"异常检查结论: {conclusion}。" if conclusion != "通过" else "无异常。"
    summary = f"小额免审通过。发票金额 {invoice_amount} 元。{anomaly_note}"

    # 留痕：小额免审整体视为「通过」，异常检查为「预警」时整体置「预警」；
    # 与分类限额/异常检测/查验节点一致，仅写 AI 状态与校验结果，不预建报销单
    # （报销单统一在「提交审批」时由 workflow.create_reimbursement_on_submit 创建）。
    request_id = state.get("request_id")
    if request_id:
        try:
            status = "预警" if conclusion == "预警" else "通过"
            update_ai_status(request_id, status)
            save_ai_check_result(request_id, "小额免审", status, classify_result)
        except Exception as e:
            logger.warning("持久化异常（非致命）: %s", e)

    return {"classify_result": classify_result, "summary": summary}
