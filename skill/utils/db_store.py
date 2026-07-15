"""数据存储工具：报销数据持久化 CRUD

将 AI 校验结果写入 SQLite 数据库，支持：
    - 报销单持久化
    - 发票记录存储
    - 重复报销查询
    - AI 校验结果归档
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from ..database import (
    AICheckResult,
    ApprovalRecord,
    InvoiceHistory,
    InvoiceRecord,
    Reimbursement,
    get_session,
    utcnow,
)

logger = logging.getLogger(__name__)


def save_reimbursement(
    request_id: str,
    employee_id: str,
    apply_amount: float,
    apply_date: str,
    reason: str = "",
    expense_category: str = "",
    remark: str = "",
) -> Reimbursement:
    """创建或更新报销单"""
    with get_session() as s:
        record = Reimbursement(
            request_id=request_id,
            employee_id=employee_id,
            apply_amount=apply_amount,
            apply_date=date.fromisoformat(apply_date) if apply_date else date.today(),
            reason=reason,
            expense_category=expense_category,
            remark=remark,
            updated_at=utcnow(),
        )
        s.merge(record)
        s.commit()
        return record


def update_ai_status(
    request_id: str,
    ai_status: str,
    workflow_status: str | None = None,
) -> None:
    """更新报销单 AI 校验状态"""
    with get_session() as s:
        record = s.query(Reimbursement).filter_by(request_id=request_id).first()
        if record:
            record.ai_status = ai_status
            record.updated_at = utcnow()
            if workflow_status:
                record.workflow_status = workflow_status
            s.commit()
        else:
            logger.warning("报销单 %s 不存在，无法更新状态", request_id)


def update_workflow_status(request_id: str, workflow_status: str) -> None:
    """仅更新报销单工作流状态（不影响 ai_status）"""
    with get_session() as s:
        record = s.query(Reimbursement).filter_by(request_id=request_id).first()
        if record:
            record.workflow_status = workflow_status
            record.updated_at = utcnow()
            s.commit()
        else:
            logger.warning("报销单 %s 不存在，无法更新工作流状态", request_id)


def save_invoice(ocr_result: dict[str, Any], request_id: str, file_path: str = "") -> InvoiceRecord:
    """保存发票记录（从 OCR 结果提取）"""
    with get_session() as s:
        invoice_date_str = ocr_result.get("开票日期", "")
        try:
            inv_date = date.fromisoformat(invoice_date_str)
        except (ValueError, TypeError):
            inv_date = None

        record = InvoiceRecord(
            request_id=request_id,
            invoice_type=ocr_result.get("发票类型", ""),
            invoice_code=ocr_result.get("发票代码", ""),
            invoice_number=str(ocr_result.get("发票号码", "")),
            invoice_date=inv_date,
            invoice_amount=ocr_result.get("发票金额", 0),
            seller_name=ocr_result.get("销售方名称", ""),
            seller_tax_id=ocr_result.get("销售方税号", ""),
            buyer_name=ocr_result.get("购买方名称", ""),
            buyer_tax_id=ocr_result.get("购买方税号", ""),
            tax_amount=_safe_float(ocr_result.get("税额")),
            file_path=file_path,
            ocr_raw=ocr_result,
        )
        s.add(record)
        s.commit()
        return record


def save_ai_check_result(
    request_id: str,
    check_type: str,
    status: str,
    detail: dict[str, Any],
) -> AICheckResult:
    """保存 AI 校验结果"""
    with get_session() as s:
        record = AICheckResult(
            request_id=request_id,
            check_type=check_type,
            status=status,
            detail=detail,
            check_time=utcnow(),
        )
        s.add(record)
        s.commit()
        return record


def check_duplicate_invoice(invoice_number: str, window_days: int = 30) -> bool:
    """检查发票是否重复报销"""
    with get_session() as s:
        exists = s.query(InvoiceHistory).filter_by(
            invoice_number=invoice_number,
        ).first()
        if exists:
            return True

        # 同时检查发票记录表中是否有同一号码
        existing = s.query(InvoiceRecord).filter_by(
            invoice_number=invoice_number,
        ).first()
        return existing is not None


def mark_invoice_reimbursed(
    invoice_number: str,
    request_id: str,
    amount: float,
) -> None:
    """标记发票已报销（防重）"""
    with get_session() as s:
        record = InvoiceHistory(
            invoice_number=invoice_number,
            request_id=request_id,
            reimbursed_date=date.today(),
            amount=amount,
        )
        s.add(record)
        s.commit()


def save_approval_record(
    request_id: str,
    approver_id: str,
    approver_name: str = "",
    approval_node: str = "",
    action: str = "",
    comment: str = "",
) -> ApprovalRecord:
    """保存审批记录"""
    with get_session() as s:
        record = ApprovalRecord(
            request_id=request_id,
            approver_id=approver_id,
            approver_name=approver_name,
            approval_node=approval_node,
            action=action,
            comment=comment,
            action_time=utcnow(),
        )
        s.add(record)
        s.commit()
        return record


def get_reimbursement(request_id: str) -> Reimbursement | None:
    """查询报销单"""
    with get_session() as s:
        return s.query(Reimbursement).filter_by(request_id=request_id).first()


def get_invoices_for_request(request_id: str) -> list[InvoiceRecord]:
    """查询报销单关联的发票列表"""
    with get_session() as s:
        return s.query(InvoiceRecord).filter_by(request_id=request_id).all()


def get_ai_results_for_request(request_id: str) -> list[AICheckResult]:
    """查询报销单的 AI 校验结果"""
    with get_session() as s:
        return s.query(AICheckResult).filter_by(request_id=request_id).all()


def _safe_float(value: Any) -> float | None:
    """安全转换为 float"""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None
