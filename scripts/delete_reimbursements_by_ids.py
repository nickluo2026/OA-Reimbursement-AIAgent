#!/usr/bin/env python3
"""按 request_id 精确删除指定报销单及其关联数据（先备份）。

用法：
    python scripts/delete_reimbursements_by_ids.py <id1> <id2> ...
    # 不传参时使用脚本内 DEFAULT_IDS
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

DEFAULT_IDS = ["df60dfe6e45f43b0", "444ee439eaa2490a", "ec6af323958c4821"]

TARGET_IDS = sys.argv[1:] if len(sys.argv) > 1 else DEFAULT_IDS


def main():
    if not DB_PATH.exists():
        print(f"[错误] 数据库文件不存在：{DB_PATH}")
        return
    shutil.copy(DB_PATH, BAK_PATH)
    print(f"[备份] {DB_PATH} -> {BAK_PATH}")

    with get_session() as s:
        targets = s.query(Reimbursement).filter(Reimbursement.request_id.in_(TARGET_IDS)).all()
        found_ids = {r.request_id for r in targets}
        missing = [i for i in TARGET_IDS if i not in found_ids]

        print("[统计] 目标单状态：")
        for r in targets:
            print(f"  - {r.request_id}  workflow_status={r.workflow_status}  ai_status={r.ai_status}")
        if missing:
            print(f"[提示] 以下单号不存在，将跳过：{missing}")

        if not targets:
            print("无匹配数据，已退出。")
            return

        ids = [r.request_id for r in targets]

        # 关联发票号（仅属于目标单的，才安全清理防重表）
        inv_target = {
            inv.invoice_number
            for inv in s.query(InvoiceRecord).filter(InvoiceRecord.request_id.in_(ids)).all()
        }
        inv_others = {
            inv.invoice_number
            for inv in s.query(InvoiceRecord).filter(InvoiceRecord.request_id.notin_(ids)).all()
        }
        safe_hist_nums = inv_target - inv_others

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

        remain = (
            s.query(Reimbursement).filter(Reimbursement.request_id.in_(TARGET_IDS)).count()
        )

    print(
        f"[删除] 报销单 {del_main} / 发票记录 {del_inv} / AI结果 {del_ai} / "
        f"审批记录 {del_appr} / 防重记录 {del_hist}"
    )
    print(f"[复核] 目标单剩余数量: {remain}")
    print("完成。")


if __name__ == "__main__":
    main()
