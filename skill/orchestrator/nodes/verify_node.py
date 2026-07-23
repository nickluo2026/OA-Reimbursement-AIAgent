"""发票查验节点

封装工具 ``verify_invoice``。查验为假票时置 final_status=BLOCK，
由 graph 的 verify→END 边结束流程；否则放行并归档结果。
"""

from __future__ import annotations

import logging
from typing import Any

from ...config import get_verify_rules
from ...tools.tool_verify_invoice import verify_invoice
from ...utils.db_store import (
    save_ai_check_result,
    update_ai_status,
)
from ..state import CheckStatus, ReimbursementState

logger = logging.getLogger(__name__)


def verify_node(state: ReimbursementState) -> dict[str, Any]:
    """功能5：发票真伪查验"""
    ocr_result = state.get("ocr_result") or {}
    logger.info("▶ 功能5: 发票查验")

    rules = get_verify_rules()

    # 管理员可通过 rule_invoice_auth 关闭「检测发票真伪（国税查验）」
    if not rules.get("enable_invoice_auth", True):
        logger.info("发票真伪检测已被管理员关闭（rule_invoice_auth=False），跳过查验")
        return {
            "verify_result": {
                "查验平台": "—",
                "查验状态": "正常",
                "总体结论": "通过",
                "查验明细": {},
                "查验摘要": "发票真伪检测已停用（系统配置），本次报销跳过国税查验",
            }
        }

    result = verify_invoice(
        invoice=ocr_result,
        block_on_fake=rules.get("verify_block_on_fake", True),
        block_on_error=rules.get("verify_block_on_error", False),
    )
    conclusion = result.get("总体结论", "通过")
    status = result.get("查验状态", "正常")
    logger.info("✓ 功能5 完成, 查验状态: %s, 结论: %s", status, conclusion)

    request_id = state.get("request_id")

    # 查验为假票/异常且策略为拦截 → 置 BLOCK，提前结束
    if conclusion == "拦截":
        if request_id:
            try:
                # 查验拦截：不预建报销单，仅留痕 AI 状态与校验结果；前端不提供提交入口，故不建单
                update_ai_status(request_id, "拦截")
                save_ai_check_result(request_id, "发票查验", "拦截", result)
            except Exception as e:
                logger.warning("持久化异常（非致命）: %s", e)
        return {
            "verify_result": result,
            "final_status": CheckStatus.BLOCK,
            "summary": f"发票查验拦截: {result.get('查验摘要', '查验为假票')}",
        }

    # 正常/预警：归档结果并放行
    if request_id:
        try:
            save_ai_check_result(request_id, "发票查验", conclusion, result)
        except Exception as e:
            logger.warning("持久化异常（非致命）: %s", e)

    return {"verify_result": result}
