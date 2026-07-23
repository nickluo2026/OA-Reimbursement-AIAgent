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

        # 防重清理：以「打款单维度」判断，而非发票号共享维度。
        # invoice_history 是某报销单【打款】时写入的防重记录；被删单的防重记录
        # 应在其打款单被删除时一并清理，除非该发票号在其他【有效打款单】的
        # 防重表中仍存在（此时仍应继续防重，避免误删真实多单打款的发票）。
        hist_target_nums = {
            h.invoice_number
            for h in s.query(InvoiceHistory).filter(InvoiceHistory.request_id.in_(ids)).all()
        }
        other_hist_nums = {
            h.invoice_number
            for h in s.query(InvoiceHistory).filter(InvoiceHistory.request_id.notin_(ids)).all()
        }
        # 仅清理「仅属于被删打款单、且其他打款单未持有」的发票号防重记录
        safe_hist_nums = hist_target_nums - other_hist_nums

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
            .filter(
                InvoiceHistory.request_id.in_(ids),
                InvoiceHistory.invoice_number.in_(safe_hist_nums),
            )
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
