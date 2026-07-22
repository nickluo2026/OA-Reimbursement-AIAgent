#!/usr/bin/env python3
"""删除所有 workflow_status='待审批' 的报销单及其关联数据（先备份）。

关联清理范围：
    - invoice_record   发票数据
    - ai_check_result  AI 校验结果
    - approval_record  审批记录
    - invoice_history  防重表（仅当发票号只属于待审批单时才删）
审计日志 audit_log 仅追加不可删，本脚本不处理。
"""
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import skill.database as dbmod
from skill.database import (
    AICheckResult,
    ApprovalRecord,
    InvoiceHistory,
    InvoiceRecord,
    Reimbursement,
    get_session,
)

DB_PATH = Path(dbmod.DB_PATH)
BAK_PATH = DB_PATH.with_name(f"oa_agent.db.bak-{datetime.now():%Y%m%d-%H%M%S}")


def main():
    # 1. 备份
    if not DB_PATH.exists():
        print(f"[错误] 数据库文件不存在：{DB_PATH}")
        return
    shutil.copy(DB_PATH, BAK_PATH)
    print(f"[备份] {DB_PATH} -> {BAK_PATH}")

    with get_session() as s:
        # 2. 待审批单据
        pending = s.query(Reimbursement).filter_by(workflow_status="待审批").all()
        n = len(pending)
        print(f"[统计] workflow_status='待审批' 共 {n} 条")
        if n == 0:
            print("无可删除数据，已退出。")
            return
        ids = [r.request_id for r in pending]

        # 3. 关联发票号（仅属于待审批单的，才安全清理防重表）
        inv_pending = {
            inv.invoice_number
            for inv in s.query(InvoiceRecord).filter(InvoiceRecord.request_id.in_(ids)).all()
        }
        inv_others = {
            inv.invoice_number
            for inv in s.query(InvoiceRecord).filter(InvoiceRecord.request_id.notin_(ids)).all()
        }
        safe_hist_nums = inv_pending - inv_others

        # 4. 级联删除
        del_inv = s.query(InvoiceRecord).filter(InvoiceRecord.request_id.in_(ids)).delete(
            synchronize_session=False
        )
        del_ai = s.query(AICheckResult).filter(AICheckResult.request_id.in_(ids)).delete(
            synchronize_session=False
        )
        del_appr = s.query(ApprovalRecord).filter(ApprovalRecord.request_id.in_(ids)).delete(
            synchronize_session=False
        )
        del_hist = (
            s.query(InvoiceHistory)
            .filter(InvoiceHistory.invoice_number.in_(safe_hist_nums))
            .delete(synchronize_session=False)
            if safe_hist_nums
            else 0
        )
        del_main = s.query(Reimbursement).filter(Reimbursement.request_id.in_(ids)).delete(
            synchronize_session=False
        )
        s.commit()

        remain = s.query(Reimbursement).filter_by(workflow_status="待审批").count()

    print(
        f"[删除] 报销单 {del_main} / 发票记录 {del_inv} / AI结果 {del_ai} / "
        f"审批记录 {del_appr} / 防重记录 {del_hist}"
    )
    print(f"[复核] 剩余 workflow_status='待审批' 数量: {remain}")
    print("完成。")


if __name__ == "__main__":
    main()
