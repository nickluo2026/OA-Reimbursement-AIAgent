# -*- coding: utf-8 -*-
"""Agent 编排入口：委托 LangGraph StateGraph 执行报销校验

V1.4 重构：原硬编码线性串联逻辑迁移至 ``skill/orchestrator/graph.py`` 的 StateGraph。
本模块保留 ``run_reimbursement_skill()`` 签名与返回结构不变，``web/app.py`` 透明切换。

执行顺序（由 StateGraph 编排）：
  功能1  ocr_extract_invoice        — OCR 提取发票全部内容
  功能3  detect_anomaly             — 异常检查（前置拦截，拦截则直接返回）
  功能2  classify_and_check_limit   — 分类限额（仅金额 > 100 时执行）
"""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any

from .orchestrator.graph import run_graph
from .orchestrator.state import CheckStatus, ReimbursementState

logger = logging.getLogger(__name__)


def run_reimbursement_skill(
    pdf_path: str,
    apply_amount: float | None = None,
    apply_date: str | None = None,
    request_id: str | None = None,
    employee_id: str = "unknown",
    reason: str = "",
    expense_category: str = "",
    ticket_type: str = "发票",
) -> dict[str, Any]:
    """报销智能校验主编排函数（V1.4 委托 LangGraph StateGraph 执行）

    Args:
        pdf_path: 发票文件路径（支持 PDF / JPG / PNG）
        apply_amount: 用户申请报销金额（可选）
        apply_date: 申请日期 YYYY-MM-DD（可选，默认今天）
        request_id: 报销单号（可选，用于数据库持久化）
        employee_id: 员工工号（可选，用于数据库持久化）
        reason: 报销事由（可选）
        expense_category: 费用分类预选（可选）
        ticket_type: 票据类型（发票/行程单），决定路由分支

    Returns:
        完整校验结果字典，结构如下::

            {
                "status": "通过" | "拦截" | "预警" | "错误",
                "ocr_result": {...},          # 功能1 提取的票据数据
                "anomaly_result": {...},       # 功能3 异常检查结果
                "classify_result": {...},      # 功能2 分类限额结果（金额>100时才有）
                "itinerary_result": {...},     # 行程单合理性校验结果（行程单类型才有）
                "summary": "..."               # 总结说明
            }
    """
    today = apply_date or date.today().strftime("%Y-%m-%d")

    # 构造 StateGraph 初始状态
    initial_state: ReimbursementState = {
        "pdf_path": pdf_path,
        "apply_amount": apply_amount,
        "apply_date": today,
        "request_id": request_id or "",
        "employee_id": employee_id,
        "reason": reason,
        "expense_category": expense_category,
        "ticket_type": ticket_type,
        "ocr_result": None,
        "anomaly_result": None,
        "classify_result": None,
        "verify_result": None,
        "itinerary_result": None,
        "final_status": CheckStatus.PASS,
        "summary": "",
        "warnings": [],
        "errors": [],
        "history": [],
    }

    # 委托 StateGraph 执行
    final = run_graph(initial_state)

    # 转换为旧返回结构（保持向后兼容）
    final_status = final.get("final_status", CheckStatus.PASS)
    status = final_status.value if isinstance(final_status, CheckStatus) else str(final_status)

    return {
        "status": status,
        "ocr_result": final.get("ocr_result"),
        "anomaly_result": final.get("anomaly_result"),
        "classify_result": final.get("classify_result"),
        "itinerary_result": final.get("itinerary_result"),
        "verify_result": final.get("verify_result"),  # 功能5：发票真伪查验
        "final_status": final_status,                 # 最终状态枚举（PASS/WARN/BLOCK）
        "summary": final.get("summary", ""),
    }


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    # 命令行用法: python -m skill.agent invoice.pdf [申请金额] [申请日期] [票据类型]
    pdf = sys.argv[1] if len(sys.argv) > 1 else "invoice.pdf"
    amt = float(sys.argv[2]) if len(sys.argv) > 2 else None
    dt = sys.argv[3] if len(sys.argv) > 3 else None
    ttype = sys.argv[4] if len(sys.argv) > 4 else "发票"

    output = run_reimbursement_skill(pdf_path=pdf, apply_amount=amt, apply_date=dt, ticket_type=ttype)
    print(json.dumps(output, ensure_ascii=False, indent=2))
