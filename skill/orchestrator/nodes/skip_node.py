"""小额免审节点：金额 ≤ 阈值时跳过限额校验

由 ``route_after_anomaly`` 条件边在金额 ≤ 100 时路由进入，
填充免审分类结果，保持返回结构与正常校验一致。
"""

from __future__ import annotations

import logging
from typing import Any

from ...config import SMALL_AMOUNT_THRESHOLD
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

    return {"classify_result": classify_result, "summary": summary}
