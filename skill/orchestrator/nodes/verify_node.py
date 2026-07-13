"""发票查验节点（P1 占位）

V1.4 为占位节点，直接通过；P1 阶段对接增值税发票查验平台。
"""

from __future__ import annotations

import logging
from typing import Any

from ..state import ReimbursementState

logger = logging.getLogger(__name__)


def verify_node(state: ReimbursementState) -> dict[str, Any]:
    """发票查验（P1 占位）：直通，不改变流程状态"""
    logger.info("▷ 发票查验: P1 占位，直通")
    return {"verify_result": None}
