#!/usr/bin/env python3
"""清空全部报销单及其关联数据（含磁盘发票文件），操作前自动备份数据库。

删除范围：
    - reimbursement      报销单主表（全部）
    - invoice_record    发票数据（全部，按 request_id 关联）
    - ai_check_result   AI 校验结果（全部，按 request_id 关联）
    - approval_record   审批记录（全部，按 request_id 关联）
    - invoice_history   防重表（全部，因所有报销单都将被删除）
    - uploads/invoices/ 下与报销单相关的票据原件与渲染影像文件

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
from skill.utils.file_storage import INVOICE_DIR

DB_PATH = Path(dbmod.DB_PATH)
BAK_PATH = DB_PATH.with_name(f"oa_agent.db.bak-{datetime.now():%Y%m%d-%H%M%S}")


def delete_invoice_files(ids):
    """删除 uploads/invoices/ 下与被删报销单相关的文件（原件 + 渲染影像）。"""
    if not ids or not INVOICE_DIR.exists():
        return 0
    removed = 0
    for f in INVOICE_DIR.iterdir():
        if not f.is_file():
            continue
        if any(f.name.startswith(rid) for rid in ids):
            try:
                f.unlink()
                removed += 1
            except OSError as e:
                print(f"[警告] 删除文件失败 {f}: {e}")
    return removed


def main():
    if not DB_PATH.exists():
        print(f"[错误] 数据库文件不存在：{DB_PATH}")
        return

    # 1. 备份
    shutil.copy(DB_PATH, BAK_PATH)
    print(f"[备份] {DB_PATH} -> {BAK_PATH}")

    with get_session() as s:
        # 2. 全部报销单
        all_reimb = s.query(Reimbursement).all()
        n = len(all_reimb)
        print(f"[统计] 报销单总数: {n}")
        if n == 0:
            print("无可删除数据，已退出。")
            return
        ids = [r.request_id for r in all_reimb]

        # 3. 级联删除关联数据
        del_inv = s.query(InvoiceRecord).delete(synchronize_session=False)
        del_ai = s.query(AICheckResult).delete(synchronize_session=False)
        del_appr = s.query(ApprovalRecord).delete(synchronize_session=False)
        del_hist = s.query(InvoiceHistory).delete(synchronize_session=False)
        del_main = s.query(Reimbursement).delete(synchronize_session=False)
        s.commit()

        remain = s.query(Reimbursement).count()

    # 4. 清理磁盘上的发票文件
    file_removed = delete_invoice_files(ids)

    print(
        f"[删除] 报销单 {del_main} / 发票记录 {del_inv} / AI结果 {del_ai} / "
        f"审批记录 {del_appr} / 防重记录 {del_hist}"
    )
    print(f"[删除] 磁盘发票文件 {file_removed} 个（目录：{INVOICE_DIR}）")
    print(f"[复核] 剩余报销单数量: {remain}")
    print("完成。")


if __name__ == "__main__":
    main()
