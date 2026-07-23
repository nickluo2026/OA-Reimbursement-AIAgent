"""端到端测试：员工提交 → 主管通过 → 财务归档打款

通过 mock 底层 AI 工具（OCR / 异常检测 / 分类限额），让真实的 LangGraph
流水线照常执行并把报销单持久化到数据库，从而完整验证
web/app.py 的 /upload → /api/approve → /api/finance 端到端链路。

对应 constitution.md §2.6：AI 辅助、人类决策，审批/财务流转均落库。
"""

import os
import tempfile
from unittest.mock import patch

from skill import workflow as wf
from skill.utils.db_store import check_duplicate_invoice, get_reimbursement


def _login(c, account, role, name):
    with c.session_transaction() as sess:
        sess["account"] = account
        sess["role"] = role
        sess["name"] = name


SAMPLE_OCR = {
    "发票类型": "增值税普通发票",
    "发票号码": "E2E-INV-001",
    "发票代码": "044001900111",
    "开票日期": "2026-07-10",
    "购买方名称": "XX科技有限公司",
    "销售方名称": "YY酒店管理有限公司",
    "发票金额": 358.50,
    "税额": "20.50",
    "价税合计_小写": 379.00,
}

SAMPLE_ANOMALY = {"总体结论": "通过", "异常明细": [], "检查摘要": "无异常"}

SAMPLE_CLASSIFY = {
    "费用分类": "差旅",
    "分类依据": "住宿费",
    "发票金额": 358.50,
    "分类限额": 1000,
    "是否超限": False,
    "校验结果": "通过",
}


@patch("skill.orchestrator.nodes.classify_node.classify_and_check_limit")
@patch("skill.orchestrator.nodes.anomaly_node.detect_anomaly")
@patch("skill.orchestrator.nodes.ocr_node.ocr_extract_invoice")
class TestEndToEndFlow:
    def test_employee_to_finance_full_flow(
        self, mock_ocr, mock_anomaly, mock_classify, client, fresh_db
    ):
        mock_ocr.return_value = SAMPLE_OCR
        mock_anomaly.return_value = SAMPLE_ANOMALY
        mock_classify.return_value = SAMPLE_CLASSIFY

        # ── 1) 员工登录并提交报销 ──
        _login(client, "EMP-2026", "employee", "张三")

        # 构造一个临时发票文件（内容无关，OCR 已被 mock）
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"%PDF-1.4 fake invoice")
            tmp_path = f.name

        try:
            with open(tmp_path, "rb") as fp:
                resp = client.post(
                    "/upload",
                    data={
                        "file": (fp, "invoice.pdf"),
                        "apply_amount": "358.50",
                        "apply_date": "2026-07-14",
                        "reason": "北京出差住宿费",
                        "expense_category": "差旅-住宿",
                        "ticket_type": "发票",
                    },
                    content_type="multipart/form-data",
                )
        finally:
            os.unlink(tmp_path)

        assert resp.status_code == 200
        body = resp.get_json()
        assert body["status"] == "通过"
        rid = body["_request_id"]

        # 提交检验（/upload）不应预建报销单：此时报销单尚不存在
        assert get_reimbursement(rid) is None

        # ── 1.5) 员工「提交审批」：仅此时才创建报销单 ──
        r_submit = client.post(
            f"/api/reimbursement/{rid}/update",
            json={
                "apply_amount": "358.50",
                "apply_date": "2026-07-14",
                "expense_category": "差旅-住宿",
                "reason": "北京出差住宿费",
            },
        )
        assert r_submit.status_code == 200
        submit_body = r_submit.get_json()
        assert submit_body["workflow_status"] == wf.WS_PENDING
        assert submit_body["ai_status"] == "通过"

        # 报销单已落库：待审批 + AI 通过 + 提交人
        reb = get_reimbursement(rid)
        assert reb is not None
        assert reb.workflow_status == wf.WS_PENDING
        assert reb.ai_status == "通过"
        assert reb.employee_id == "EMP-2026"

        # ── 2) 主管通过 ──
        _login(client, "APR-001", "approver", "李总")
        r_approve = client.post(
            "/api/approve", json={"request_id": rid, "action": "通过", "comment": "同意"}
        )
        assert r_approve.status_code == 200
        assert r_approve.get_json()["data"]["workflow_status"] == wf.WS_APPROVED

        # 此时待审列表为空，财务列表出现该单
        assert wf.list_pending() == []
        assert len(wf.list_for_finance()) == 1

        # ── 3) 财务归档 ──
        _login(client, "FIN-001", "finance_review", "王会计")
        r_archive = client.post("/api/finance", json={"request_id": rid, "action": "归档"})
        assert r_archive.status_code == 200
        assert r_archive.get_json()["data"]["workflow_status"] == wf.WS_ARCHIVED

        # ── 4) 出纳（职责分离：须与归档人不同账号） ──
        _login(client, "FIN-002", "finance_pay", "李出纳")
        r_pay = client.post("/api/finance", json={"request_id": rid, "action": "打款"})
        assert r_pay.status_code == 200
        assert r_pay.get_json()["data"]["workflow_status"] == wf.WS_PAID

        # 发票已标记报销（防重生效）
        assert check_duplicate_invoice("E2E-INV-001") is True

        # 财务列表已清空（已打款不出现在待处理列表）
        assert wf.list_for_finance() == []

    def test_employee_reject_flow(self, mock_ocr, mock_anomaly, mock_classify, client, fresh_db):
        """审批驳回后不可再进入财务流程"""
        mock_ocr.return_value = SAMPLE_OCR
        mock_anomaly.return_value = SAMPLE_ANOMALY
        mock_classify.return_value = SAMPLE_CLASSIFY

        _login(client, "EMP-2026", "employee", "张三")
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"%PDF-1.4")
            tmp_path = f.name
        try:
            with open(tmp_path, "rb") as fp:
                resp = client.post(
                    "/upload",
                    data={
                        "file": (fp, "invoice.pdf"),
                        "apply_amount": "358.50",
                        "apply_date": "2026-07-14",
                        "reason": "测试",
                        "ticket_type": "发票",
                    },
                    content_type="multipart/form-data",
                )
        finally:
            os.unlink(tmp_path)

        rid = resp.get_json()["_request_id"]

        # 提交检验后不建单，需先「提交审批」建单才能进入审批流转
        assert get_reimbursement(rid) is None
        r_submit = client.post(
            f"/api/reimbursement/{rid}/update",
            json={
                "apply_amount": "358.50",
                "apply_date": "2026-07-14",
                "reason": "测试",
            },
        )
        assert r_submit.status_code == 200

        _login(client, "APR-001", "approver", "李总")
        r = client.post(
            "/api/approve",
            json={"request_id": rid, "action": "驳回", "comment": "票据缺失"},
        )
        assert r.get_json()["data"]["workflow_status"] == wf.WS_REJECTED
        # 驳回单不在财务待处理列表
        assert wf.list_for_finance() == []
