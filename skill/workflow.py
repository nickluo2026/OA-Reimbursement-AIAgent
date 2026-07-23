"""审批 / 财务工作流：报销单审批流转与财务发放

将分散在 ``db_store`` 中的持久化函数与 ``approval_authority.yaml`` 的路由规则
组合为端到端工作流，供 ``web/app.py`` 的审批 / 财务路由调用。

对应设计：
    - design.md §2.5   主管（通过 / 驳回 / 转审）
    - design.md §2.6   财务终审与发放（归档 / 打款）
    - constitution.md §2.6  AI 辅助、人类决策（审批决策权在人类）
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any


def _utc_to_local(dt: datetime | None) -> datetime | None:
    """将 naive UTC 时间转为本地时间（去掉时区信息），使展示时间与系统时钟一致。

    数据库以 UTC 存储（见 database.utcnow），序列化时若不转换，
    前端会把 UTC 字符串当作本地时间显示，导致与系统时间相差时区偏移。
    """
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None)

from .database import ApprovalRecord, get_session, utcnow
from .tools.tool_approval_routing import determine_approval_route
from .utils.db_store import (
    get_ai_results_for_request,
    get_invoices_for_request,
    mark_invoice_reimbursed,
    save_approval_record,
    set_finance_operators,
    update_workflow_status,
)
from .utils.mask_sensitive import mask_ocr_result

logger = logging.getLogger(__name__)

# ── 工作流状态常量 ──
WS_PENDING = "待审批"
WS_IN_REVIEW = "审批中"
WS_APPROVED = "待复核"
WS_REJECTED = "已驳回"
WS_ARCHIVED = "已复核并归档"
WS_PAID = "已打款"

# 主管可见（待处理）状态
PENDING_STATUSES = (WS_PENDING, WS_IN_REVIEW)
# 财务可见（待复核 / 已复核并归档待打款）状态
FINANCE_STATUSES = (WS_APPROVED, WS_ARCHIVED)

# 终结状态（不可再被审批）
TERMINAL_STATUSES = (WS_REJECTED, WS_PAID)


# ═══════════════════════════════════════════════
# 审批路由计算
# ═══════════════════════════════════════════════
def compute_route(amount: float) -> dict[str, Any]:
    """根据金额计算审批路由（金额阶梯 + 会签），见 approval_authority.yaml"""
    return determine_approval_route(amount)


# ═══════════════════════════════════════════════
# 查询
# ═══════════════════════════════════════════════
def get_reimbursement(request_id: str):
    """按单号查询报销单"""
    from .database import Reimbursement

    with get_session() as s:
        return s.query(Reimbursement).filter_by(request_id=request_id).first()


def list_pending() -> list:
    """待审批 / 审批中的报销单（主管工作台列表）"""
    from .database import Reimbursement

    with get_session() as s:
        return (
            s.query(Reimbursement)
            .filter(Reimbursement.workflow_status.in_(PENDING_STATUSES))
            .order_by(Reimbursement.created_at.desc())
            .all()
        )


def list_for_finance() -> list:
    """待复核 / 已复核并归档的报销单（财务工作台列表）"""
    from .database import Reimbursement

    with get_session() as s:
        return (
            s.query(Reimbursement)
            .filter(Reimbursement.workflow_status.in_(FINANCE_STATUSES))
            .order_by(Reimbursement.created_at.desc())
            .all()
        )


def list_by_employee(employee_id: str) -> list:
    """某员工提交的全部报销单（我的报销）"""
    from .database import Reimbursement

    with get_session() as s:
        return (
            s.query(Reimbursement)
            .filter_by(employee_id=employee_id)
            .order_by(Reimbursement.created_at.desc())
            .all()
        )


def count_decisions_this_month(approver_id: str) -> int:
    """统计审批人本月已处理（通过/驳回/转审）的单数"""
    now = utcnow()
    start = datetime(now.year, now.month, 1)
    with get_session() as s:
        return (
            s.query(ApprovalRecord)
            .filter(
                ApprovalRecord.approver_id == approver_id,
                ApprovalRecord.action.in_(("通过", "驳回", "转审")),
                ApprovalRecord.action_time >= start,
            )
            .count()
        )


def count_by_status(status: str) -> int:
    """统计指定工作流状态的报销单数量"""
    from .database import Reimbursement

    with get_session() as s:
        return s.query(Reimbursement).filter_by(workflow_status=status).count()


def _count_approvals(request_id: str, action: str) -> int:
    with get_session() as s:
        return s.query(ApprovalRecord).filter_by(request_id=request_id, action=action).count()


def _ticket_type_of(request_id: str) -> str:
    invoices = get_invoices_for_request(request_id)
    if invoices and invoices[0].invoice_type:
        return invoices[0].invoice_type
    return "发票"


def get_ai_summary_text(request_id: str) -> str:
    """汇总 AI 校验结论为简短中文描述（异常检测摘要 + 分类限额结果）"""
    results = get_ai_results_for_request(request_id)
    parts: list[str] = []
    for r in results:
        detail = r.detail or {}
        if r.check_type == "异常检测":
            txt = detail.get("检查摘要") or detail.get("总体结论")
        elif r.check_type == "分类限额":
            txt = detail.get("校验结果") or detail.get("费用分类")
        else:
            txt = None
        if txt:
            parts.append(str(txt))
    return " · ".join(p for p in parts if p) or "AI 校验完成"


def serialize(r) -> dict[str, Any]:
    """将报销单序列化为前端可用的字典（含审批路由与会签进度）"""
    route = compute_route(r.apply_amount)
    transferred = _count_approvals(r.request_id, "转审") > 0
    passed = _count_approvals(r.request_id, "通过")
    return {
        "request_id": r.request_id,
        "employee_id": r.employee_id,
        "apply_amount": r.apply_amount,
        "apply_date": r.apply_date.isoformat() if r.apply_date else None,
        "reason": r.reason,
        "expense_category": r.expense_category,
        "ai_status": r.ai_status,
        "workflow_status": r.workflow_status,
        "created_at": _utc_to_local(r.created_at).isoformat() if r.created_at else None,
        "ticket_type": _ticket_type_of(r.request_id),
        "ai_summary": get_ai_summary_text(r.request_id),
        "route": route,
        "transferred": transferred,
        "countersign_passed": passed,
        "needs_countersign": route.get("需要会签", False),
        "archived_by": r.archived_by or "",
        "paid_by": r.paid_by or "",
    }


def get_detail(request_id: str) -> dict[str, Any] | None:
    """获取报销单完整明细（发票 / AI 校验 / 审批记录 / 路由）"""
    r = get_reimbursement(request_id)
    if not r:
        return None

    with get_session() as s:
        approvals = (
            s.query(ApprovalRecord)
            .filter_by(request_id=request_id)
            .order_by(ApprovalRecord.action_time.asc())
            .all()
        )
        approval_list = [
            {
                "approver_id": a.approver_id,
                "approver_name": a.approver_name,
                "approval_node": a.approval_node,
                "action": a.action,
                "comment": a.comment,
                "action_time": a.action_time.isoformat() if a.action_time else None,
            }
            for a in approvals
        ]

    invoices = get_invoices_for_request(request_id)
    ai_results = get_ai_results_for_request(request_id)
    return {
        "request_id": r.request_id,
        "employee_id": r.employee_id,
        "apply_amount": r.apply_amount,
        "apply_date": r.apply_date.isoformat() if r.apply_date else None,
        "reason": r.reason,
        "expense_category": r.expense_category,
        "ai_status": r.ai_status,
        "workflow_status": r.workflow_status,
        "created_at": _utc_to_local(r.created_at).isoformat() if r.created_at else None,
        "route": compute_route(r.apply_amount),
        "archived_by": r.archived_by or "",
        "paid_by": r.paid_by or "",
        "invoices": [
            {
                "invoice_number": inv.invoice_number,
                "invoice_type": inv.invoice_type,
                "invoice_amount": inv.invoice_amount,
                "seller_name": inv.seller_name,
                "invoice_date": inv.invoice_date.isoformat() if inv.invoice_date else None,
            }
            for inv in invoices
        ],
        "ai_results": [
            {
                "check_type": ar.check_type,
                "status": ar.status,
                "detail": mask_ocr_result(ar.detail) if isinstance(ar.detail, dict) else ar.detail,
                "check_time": ar.check_time.isoformat() if ar.check_time else None,
            }
            for ar in ai_results
        ],
        "approval_records": approval_list,
    }


def update_reimbursement(
    request_id: str,
    *,
    apply_amount: float | None = None,
    apply_date: str | None = None,
    expense_category: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """更新报销单字段（仅「待审批」状态可改，用于 AI 回写后人工确认落库）。

    - apply_amount / apply_date / expense_category / reason 任一非 None 则更新
    - apply_date 传空串视为清空；传 ISO 日期串则解析
    """
    from datetime import date

    from .database import Reimbursement

    with get_session() as s:
        r = s.query(Reimbursement).filter_by(request_id=request_id).first()
        if not r:
            raise ValueError(f"报销单（报销单号：{request_id}）不存在")
        if r.workflow_status != WS_PENDING:
            raise ValueError(
                f"报销单（报销单号：{request_id}）当前状态「{r.workflow_status}」不可修改，仅「待审批」可改"
            )
        if apply_amount is not None:
            r.apply_amount = float(apply_amount)
        if apply_date is not None:
            r.apply_date = date.fromisoformat(apply_date) if apply_date else None
        if expense_category is not None:
            r.expense_category = expense_category or None
        if reason is not None:
            r.reason = reason
        s.commit()
        logger.info("更新报销单 %s: amount=%s date=%s category=%s", request_id, apply_amount, apply_date, expense_category)
        # 重新查询以获取最新状态序列化
        s.refresh(r)
        return serialize(r)


# ═══════════════════════════════════════════════
# 审批决策（主管）
# ═══════════════════════════════════════════════
def submit_approval(
    request_id: str,
    approver_id: str,
    approver_name: str = "",
    action: str = "通过",
    comment: str = "",
) -> dict[str, Any]:
    """提交审批决策：通过 / 驳回 / 转审

    - 通过：金额 ≥ 会签阈值需多人会签，未达最少签核人数时进入「审批中」
    - 驳回：工作流置「已驳回」，终止
    - 转审：记录转审动作，工作流状态保持不变（仍待处理）
    """
    if action not in ("通过", "驳回", "转审"):
        raise ValueError(f"未知审批动作: {action}")

    r = get_reimbursement(request_id)
    if not r:
        raise ValueError(f"报销单（报销单号：{request_id}）不存在")
    if r.workflow_status == WS_REJECTED:
        raise ValueError(f"报销单（报销单号：{request_id}）已驳回，不可重复审批")
    if r.workflow_status in TERMINAL_STATUSES:
        raise ValueError(f"报销单（报销单号：{request_id}）当前状态「{r.workflow_status}」不可审批")

    route = compute_route(r.apply_amount)
    node = route.get("审批人", "主管")
    save_approval_record(
        request_id=request_id,
        approver_id=approver_id,
        approver_name=approver_name,
        approval_node=node,
        action=action,
        comment=comment,
    )

    if action == "驳回":
        new_status = WS_REJECTED
    elif action == "转审":
        new_status = r.workflow_status  # 保持原状态，仅留痕
    else:  # 通过
        if route.get("需要会签"):
            min_signers = route.get("最少签核人数", 2)
            passed = _count_approvals(request_id, "通过")
            new_status = WS_IN_REVIEW if passed < min_signers else WS_APPROVED
        else:
            new_status = WS_APPROVED

    update_workflow_status(request_id, new_status)
    logger.info("审批 %s: action=%s node=%s -> %s", request_id, action, node, new_status)
    return serialize(get_reimbursement(request_id))


# ═══════════════════════════════════════════════
# 财务终审与发放（财务人员）
# ═══════════════════════════════════════════════
def submit_finance(
    request_id: str,
    finance_id: str,
    finance_name: str = "",
    action: str = "归档",
    comment: str = "",
) -> dict[str, Any]:
    """财务归档 / 出纳（职责分离）

    - 归档（财务岗）：仅「待复核」可归档，置「已复核并归档」，记录归档人
    - 打款（出纳岗）：仅「已复核并归档」可打款，置「已打款」并标记发票已报销（防重）；
      系统强制 **打款人 ≠ 归档人**（职责分离），违规直接拦截
    """
    if action not in ("归档", "打款"):
        raise ValueError(f"未知财务动作: {action}")

    r = get_reimbursement(request_id)
    if not r:
        raise ValueError(f"报销单（报销单号：{request_id}）不存在")

    if action == "归档":
        if r.workflow_status != WS_APPROVED:
            raise ValueError(
                f"报销单（报销单号：{request_id}）当前状态「{r.workflow_status}」不可归档，需先审批通过"
            )
        save_approval_record(
            request_id=request_id,
            approver_id=finance_id,
            approver_name=finance_name,
            approval_node="财务",
            action="归档",
            comment=comment,
        )
        # 记录归档人（财务岗工号），供职责分离校验
        set_finance_operators(request_id, archived_by=finance_id)
        update_workflow_status(request_id, WS_ARCHIVED)
        logger.info("财务归档 %s by=%s", request_id, finance_id)
    else:  # 打款（出纳岗）
        if r.workflow_status != WS_ARCHIVED:
            raise ValueError(f"报销单（报销单号：{request_id}）尚未归档，请先确认归档再打款")
        # 职责分离校验：打款人不得为同一报销单的归档人（舞弊风险拦截）
        if r.archived_by and r.archived_by == finance_id:
            raise ValueError(
                f"舞弊风险拦截：报销单（报销单号：{request_id}）的打款人与归档人"
                f"不能为同一人（{finance_id}），已阻止本次打款。"
            )
        save_approval_record(
            request_id=request_id,
            approver_id=finance_id,
            approver_name=finance_name,
            approval_node="出纳",
            action="打款",
            comment=comment,
        )
        # 记录打款人（出纳岗工号）
        set_finance_operators(request_id, paid_by=finance_id)
        # 防重：标记关联发票已报销
        for inv in get_invoices_for_request(request_id):
            try:
                mark_invoice_reimbursed(inv.invoice_number, request_id, r.apply_amount)
            except Exception as e:  # 已报销则忽略（幂等）
                logger.warning("标记发票已报销失败(可能重复): %s", e)
        update_workflow_status(request_id, WS_PAID)
        # 回单归档：打款完成后留痕（出纳岗动作）
        save_approval_record(
            request_id=request_id,
            approver_id=finance_id,
            approver_name=finance_name,
            approval_node="出纳",
            action="回单归档",
            comment=comment,
        )
        logger.info("出纳 %s 金额=%.2f by=%s", request_id, r.apply_amount, finance_id)

    return serialize(get_reimbursement(request_id))
