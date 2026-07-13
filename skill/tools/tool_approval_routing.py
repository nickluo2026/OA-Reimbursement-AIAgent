"""审批权限路由工具：根据金额自动确定审批级别"""

from __future__ import annotations

import logging
from typing import Any

from ..config import _load_yaml

logger = logging.getLogger(__name__)


def get_approval_config() -> dict[str, Any]:
    """加载审批权限配置"""
    return _load_yaml("approval_authority.yaml")


def determine_approval_route(amount: float) -> dict[str, Any]:
    """根据金额确定审批路由

    Args:
        amount: 报销金额（元）

    Returns:
        包含审批级别、审批人、是否需要会签等信息的字典
    """
    config = get_approval_config()
    levels = config.get("approval_levels", [])

    selected_level = None
    for lv in levels:
        max_amt = lv.get("max_amount")
        if max_amt is None or amount < max_amt:
            selected_level = lv
            break

    if selected_level is None:
        selected_level = levels[-1] if levels else {}

    # 会签检查
    countersign = config.get("countersign", {})
    needs_countersign = (
        countersign.get("enabled", False)
        and amount >= countersign.get("threshold", 50000)
    )

    result = {
        "报销金额": amount,
        "审批级别": selected_level.get("level"),
        "审批人": selected_level.get("approver", "未知"),
        "级别描述": selected_level.get("description", ""),
        "需要会签": needs_countersign,
    }

    if needs_countersign:
        result["最少签核人数"] = countersign.get("min_signers", 2)

    logger.info(
        "审批路由: 金额=%.2f, 级别=%s, 审批人=%s, 会签=%s",
        amount,
        result["审批级别"],
        result["审批人"],
        needs_countersign,
    )
    return result


def should_auto_approve(amount: float) -> bool:
    """判断是否满足自动通过（小额免审）条件"""
    config = get_approval_config()
    threshold = config.get("auto_approval", {}).get("threshold", 100)
    return amount <= threshold
