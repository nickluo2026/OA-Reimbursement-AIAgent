"""审批 / 财务 API 集成测试（Flask test client）

验证 web/app.py 中新增的路由与 JSON API 端到端行为：
    GET  /approve  /finance          页面渲染
    GET  /api/approve/list           待审列表
    POST /api/approve                审批决策
    GET  /api/finance/list           财务列表
    POST /api/finance                归档 / 打款
    GET  /api/reimbursement/<id>     明细
    GET  /api/my                     我的报销
"""

import pytest

from skill import workflow as wf
from skill.utils.db_store import save_invoice, save_reimbursement
from web.app import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _login(c, account, role, name):
    with c.session_transaction() as sess:
        sess["account"] = account
        sess["role"] = role
        sess["name"] = name


def _make_reimbursement(rid="REQ-API-001", amount=358.50, employee="EMP-2026"):
    save_reimbursement(
        request_id=rid,
        employee_id=employee,
        apply_amount=amount,
        apply_date="2026-07-14",
        reason="集成测试报销",
        expense_category="差旅",
    )
    save_invoice({"发票号码": "INV-" + rid, "发票金额": amount, "销售方名称": "X"}, rid, "")


# ── 页面渲染 ──
class TestPages:
    def test_approve_page_requires_login(self, client):
        resp = client.get("/approve")
        assert resp.status_code == 302  # 重定向到登录

    def test_approve_page_renders_for_approver(self, client, fresh_db):
        _login(client, "APR-001", "approver", "李总")
        resp = client.get("/approve")
        assert resp.status_code == 200
        assert "待审报销单" in resp.get_data(as_text=True)

    def test_approve_page_forbidden_for_employee(self, client, fresh_db):
        _login(client, "EMP-2026", "employee", "张三")
        resp = client.get("/approve")
        assert resp.status_code == 200
        assert "无审批权限" in resp.get_data(as_text=True)

    def test_finance_page_renders_for_finance(self, client, fresh_db):
        _login(client, "FIN-001", "finance_review", "王会计")
        resp = client.get("/finance")
        assert resp.status_code == 200
        assert "待终审报销单" in resp.get_data(as_text=True)


# ── 列表 / 明细 API ──
class TestListAndDetail:
    def test_approve_list_requires_login(self, client, fresh_db):
        resp = client.get("/api/approve/list")
        assert resp.status_code == 401

    def test_approve_list_returns_items(self, client, fresh_db):
        _make_reimbursement()
        _login(client, "APR-001", "approver", "李总")
        resp = client.get("/api/approve/list")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["count"] == 1
        assert data["items"][0]["employee_name"] == "张三"  # DEMO_ACCOUNTS 映射

    def test_reimbursement_detail(self, client, fresh_db):
        _make_reimbursement()
        _login(client, "APR-001", "approver", "李总")
        resp = client.get("/api/reimbursement/REQ-API-001")
        assert resp.status_code == 200
        d = resp.get_json()
        assert d["request_id"] == "REQ-API-001"
        assert d["invoices"][0]["invoice_number"] == "INV-REQ-API-001"

    def test_reimbursement_detail_404(self, client, fresh_db):
        _login(client, "APR-001", "approver", "李总")
        resp = client.get("/api/reimbursement/NOPE")
        assert resp.status_code == 404

    def test_my_endpoint(self, client, fresh_db):
        _make_reimbursement(employee="EMP-2026")
        _login(client, "EMP-2026", "employee", "张三")
        resp = client.get("/api/my")
        assert resp.status_code == 200
        assert resp.get_json()["count"] == 1


# ── 审批 API ──
class TestApproveAPI:
    def test_approve_pass(self, client, fresh_db):
        _make_reimbursement()
        _login(client, "APR-001", "approver", "李总")
        resp = client.post("/api/approve", json={"request_id": "REQ-API-001", "action": "通过"})
        assert resp.status_code == 200
        assert resp.get_json()["data"]["workflow_status"] == "待复核"

    def test_approve_reject(self, client, fresh_db):
        _make_reimbursement()
        _login(client, "APR-001", "approver", "李总")
        resp = client.post(
            "/api/approve",
            json={"request_id": "REQ-API-001", "action": "驳回", "comment": "票据缺失"},
        )
        assert resp.status_code == 200
        assert resp.get_json()["data"]["workflow_status"] == "已驳回"

    def test_approve_forbidden_for_employee(self, client, fresh_db):
        _make_reimbursement()
        _login(client, "EMP-2026", "employee", "张三")
        resp = client.post("/api/approve", json={"request_id": "REQ-API-001", "action": "通过"})
        assert resp.status_code == 403

    def test_approve_invalid_action(self, client, fresh_db):
        _make_reimbursement()
        _login(client, "APR-001", "approver", "李总")
        resp = client.post("/api/approve", json={"request_id": "REQ-API-001", "action": "瞎搞"})
        assert resp.status_code == 400

    def test_approve_missing_request(self, client, fresh_db):
        _login(client, "APR-001", "approver", "李总")
        resp = client.post("/api/approve", json={"action": "通过"})
        assert resp.status_code == 400


# ── 财务 API ──
class TestFinanceAPI:
    def test_finance_list_after_approve(self, client, fresh_db):
        _make_reimbursement()
        _login(client, "APR-001", "approver", "李总")
        client.post("/api/approve", json={"request_id": "REQ-API-001", "action": "通过"})
        _login(client, "FIN-001", "finance_review", "王会计")
        resp = client.get("/api/finance/list")
        assert resp.status_code == 200
        assert resp.get_json()["pending_archive"] == 1

    def test_finance_archive_and_pay(self, client, fresh_db):
        _make_reimbursement()
        _login(client, "APR-001", "approver", "李总")
        client.post("/api/approve", json={"request_id": "REQ-API-001", "action": "通过"})
        _login(client, "FIN-001", "finance_review", "王会计")

        r1 = client.post("/api/finance", json={"request_id": "REQ-API-001", "action": "归档"})
        assert r1.status_code == 200
        assert r1.get_json()["data"]["workflow_status"] == "已复核并归档"

        # 打款须由出纳岗（FIN-002）执行，落实职责分离
        _login(client, "FIN-002", "finance_pay", "李出纳")
        r2 = client.post("/api/finance", json={"request_id": "REQ-API-001", "action": "打款"})
        assert r2.status_code == 200
        assert r2.get_json()["data"]["workflow_status"] == "已打款"

    def test_finance_pay_before_archive(self, client, fresh_db):
        _make_reimbursement()
        _login(client, "APR-001", "approver", "李总")
        client.post("/api/approve", json={"request_id": "REQ-API-001", "action": "通过"})
        _login(client, "FIN-001", "finance_review", "王会计")
        resp = client.post("/api/finance", json={"request_id": "REQ-API-001", "action": "打款"})
        assert resp.status_code == 400
        assert "归档" in resp.get_json()["error"]

    def test_finance_segregation_api(self, client, fresh_db):
        """职责分离（API 级）：同一财务账号既归档又打款须被拦截"""
        _make_reimbursement()
        _login(client, "APR-001", "approver", "李总")
        client.post("/api/approve", json={"request_id": "REQ-API-001", "action": "通过"})
        _login(client, "FIN-001", "finance_review", "王会计")
        r1 = client.post("/api/finance", json={"request_id": "REQ-API-001", "action": "归档"})
        assert r1.status_code == 200

        # 同一账号（FIN-001，同时为归档人）尝试打款 → 舞弊拦截
        r2 = client.post("/api/finance", json={"request_id": "REQ-API-001", "action": "打款"})
        assert r2.status_code == 400
        assert "舞弊" in r2.get_json()["error"] or "归档人" in r2.get_json()["error"]

    def test_finance_forbidden_for_approver(self, client, fresh_db):
        _make_reimbursement()
        _login(client, "APR-001", "approver", "李总")
        client.post("/api/approve", json={"request_id": "REQ-API-001", "action": "通过"})
        _login(client, "APR-001", "approver", "李总")
        resp = client.post("/api/finance", json={"request_id": "REQ-API-001", "action": "归档"})
        assert resp.status_code == 403


# ── 报销单更新 API（AI 回写落库） ──
class TestReimbursementUpdate:
    def test_update_fields_while_pending(self, client, fresh_db):
        _make_reimbursement()
        _login(client, "EMP-2026", "employee", "张三")
        resp = client.post(
            "/api/reimbursement/REQ-API-001/update",
            json={"apply_amount": "999.00", "apply_date": "2026-07-20",
                  "expense_category": "住宿", "reason": "AI 回写后确认"},
        )
        assert resp.status_code == 200
        d = resp.get_json()
        assert d["apply_amount"] == 999.0
        assert d["apply_date"] == "2026-07-20"
        assert d["expense_category"] == "住宿"
        assert d["reason"] == "AI 回写后确认"

    def test_update_forbidden_for_other_employee(self, client, fresh_db):
        _make_reimbursement(employee="EMP-2026")
        _login(client, "EMP-9999", "employee", "他人")
        resp = client.post(
            "/api/reimbursement/REQ-API-001/update",
            json={"apply_amount": "1.00"},
        )
        assert resp.status_code == 403

    def test_update_rejected_after_approval(self, client, fresh_db):
        """审批通过（待复核）后不可再修改"""
        _make_reimbursement()
        _login(client, "APR-001", "approver", "李总")
        client.post("/api/approve", json={"request_id": "REQ-API-001", "action": "通过"})
        _login(client, "EMP-2026", "employee", "张三")
        resp = client.post(
            "/api/reimbursement/REQ-API-001/update",
            json={"apply_amount": "1.00"},
        )
        assert resp.status_code == 400
        assert "不可修改" in resp.get_json()["error"]

    def test_update_404_for_missing(self, client, fresh_db):
        _login(client, "EMP-2026", "employee", "张三")
        resp = client.post(
            "/api/reimbursement/NOPE/update",
            json={"apply_amount": "1.00"},
        )
        assert resp.status_code == 404

    def test_submit_creates_reimbursement_for_warning(self, client, fresh_db):
        """预警单运行期未预建：提交审批时按发票存在创建报销单"""
        from skill.utils.db_store import save_invoice

        _login(client, "EMP-2026", "employee", "张三")
        # 仅落库发票（模拟 AI 校验「预警」场景：有发票/AI结果，但无报销单）
        save_invoice(
            {"发票号码": "WARN-001", "发票金额": 200},
            "REQ-WARN-001",
            "warn.pdf",
        )
        resp = client.post(
            "/api/reimbursement/REQ-WARN-001/update",
            json={"apply_amount": "200.00", "apply_date": "2026-07-20",
                  "expense_category": "住宿", "reason": "预警后提交"},
        )
        assert resp.status_code == 200
        d = resp.get_json()
        assert d["request_id"] == "REQ-WARN-001"
        assert d["workflow_status"] == wf.WS_PENDING
        assert d["ai_status"] == "预警"
        assert d["apply_amount"] == 200.0
        assert d["expense_category"] == "住宿"

    def test_disabled_manual_submit_creates_invoice(self, client, fresh_db):
        """DeepSeek 停用态：OCR 未跑、无发票记录，用户手填发票号后提交审批应建单并落库发票号"""
        _login(client, "EMP-2026", "employee", "张三")
        resp = client.post(
            "/api/reimbursement/REQ-DIS-001/update",
            json={
                "apply_amount": "188.00",
                "apply_date": "2026-07-20",
                "expense_category": "交通",
                "reason": "停用态手填",
                "invoice_number": "DIS-INV-001",
            },
        )
        assert resp.status_code == 200
        d = resp.get_json()
        assert d["request_id"] == "REQ-DIS-001"
        assert d["workflow_status"] == wf.WS_PENDING
        assert d["apply_amount"] == 188.0
        # 发票号已落库
        assert d["invoices"] and d["invoices"][0]["invoice_number"] == "DIS-INV-001"

    def test_disabled_duplicate_invoice_rejected(self, client, fresh_db):
        """停用态补录的发票号若已存在应被拦截（防重）"""
        from skill.utils.db_store import check_duplicate_invoice

        _login(client, "EMP-2026", "employee", "张三")
        # 先建一个已存在该发票号的报销单
        client.post(
            "/api/reimbursement/REQ-DIS-A/update",
            json={"apply_amount": "100", "expense_category": "交通", "invoice_number": "DUP-DIS-001"},
        )
        assert check_duplicate_invoice("DUP-DIS-001") is True
        # 另一个单复用同一发票号 → 409
        resp = client.post(
            "/api/reimbursement/REQ-DIS-B/update",
            json={"apply_amount": "100", "expense_category": "交通", "invoice_number": "DUP-DIS-001"},
        )
        assert resp.status_code == 409
        assert "重复" in resp.get_json()["error"]

    def test_disabled_submit_without_invoice_number_404(self, client, fresh_db):
        """停用态既无发票记录、又未手填发票号 → 视为非法单号（404）"""
        _login(client, "EMP-2026", "employee", "张三")
        resp = client.post(
            "/api/reimbursement/REQ-DIS-X/update",
            json={"apply_amount": "100", "expense_category": "交通"},
        )
        assert resp.status_code == 404
