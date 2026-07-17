# -*- coding: utf-8 -*-
"""全局状态定义（StateGraph State）

LangGraph 通过 ``TypedDict`` 定义全局共享状态，节点间传参由框架自动管理，
消除手工传递易错问题。对应 design.md §16.3。
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional, TypedDict


class CheckStatus(str, Enum):
    """校验状态枚举（与原 agent.py 返回的 status 字符串保持一致）"""

    PASS = "通过"
    WARNING = "预警"
    BLOCK = "拦截"
    ERROR = "错误"


class ReimbursementState(TypedDict, total=False):
    """报销校验工作流全局状态。

    所有字段可选（``total=False``），由各节点按需写入并合并。
    """

    # —— 输入 ——
    request_id: str                          # 报销单号（全链路追踪；为空则不持久化）
    pdf_path: str                            # 票据文件路径
    apply_amount: Optional[float]            # 申请金额
    apply_date: str                          # 申请日期 YYYY-MM-DD
    employee_id: str                         # 员工工号
    reason: str                              # 报销事由
    expense_category: str                    # 费用分类预选
    ticket_type: str                         # 票据类型：发票/行程单/火车票/机票

    # —— 节点产出（Agent 间共享）——
    ocr_result: Optional[dict[str, Any]]     # OCR 提取的结构化票据数据
    anomaly_result: Optional[dict[str, Any]]  # 异常检测结果
    classify_result: Optional[dict[str, Any]]  # 分类限额校验结果
    verify_result: Optional[dict[str, Any]]  # 发票查验结果（P1 占位）
    itinerary_result: Optional[dict[str, Any]]  # 行程单合理性校验结果

    # —— 流程控制 ——
    final_status: CheckStatus                # 最终校验状态
    summary: str                             # 总结说明（出口直接使用）
    warnings: list[str]                      # 预警明细
    block_reason: Optional[str]              # 拦截原因
    errors: list[str]                        # 异常错误
    history: list[dict[str, Any]]            # 节点执行历史（审计）
