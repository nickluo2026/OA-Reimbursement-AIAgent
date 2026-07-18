"""系统管理员功能集成测试（Flask test client）

验证 web/app.py 新增的管理后台路由与 JSON API 端到端行为：
    GET  /admin                  管理员工作台页面（权限 / 渲染）
    GET  /api/admin/config       系统配置（schema + 当前值）
    POST /api/admin/config       保存配置（落库 + 写审计）
    POST /api/admin/config/reset 恢复默认
    GET  /api/admin/audit        审计日志列表
    GET  /api/admin/usage        用量统计（概览 / 每日 / 按类型 / 明细）
"""

import pytest

from web.app import app
from skill.utils import admin_store


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


@pytest.fixture
def seeded(fresh_db):
    """干净库 + 预置演示数据（审计 / 用量）"""
    admin_store.ensure_seeded()
    yield


def _login(c, account, role, name):
    with c.session_transaction() as sess:
        sess["account"] = account
        sess["role"] = role
        sess["name"] = name


# ── 页面渲染 ──
class TestAdminPage:
    def test_admin_page_requires_login(self, client):
        resp = client.get("/admin")
        assert resp.status_code == 302  # 重定向到登录

    def test_admin_page_renders_for_admin(self, client, fresh_db):
        _login(client, "ADM-001", "admin", "赵管理")
        resp = client.get("/admin")
        assert resp.status_code == 200
        text = resp.get_data(as_text=True)
        assert "系统配置" in text
        assert "审计日志" in text
        assert "用量统计" in text

    def test_admin_page_forbidden_for_employee(self, client, fresh_db):
        _login(client, "EMP-2026", "employee", "张三")
        resp = client.get("/admin")
        assert resp.status_code == 200
        assert "无系统管理权限" in resp.get_data(as_text=True)


# ── 系统配置 ──
class TestAdminConfig:
    def test_config_requires_login(self, client, fresh_db):
        resp = client.get("/api/admin/config")
        assert resp.status_code == 401

    def test_config_forbidden_for_employee(self, client, fresh_db):
        _login(client, "EMP-2026", "employee", "张三")
        resp = client.get("/api/admin/config")
        assert resp.status_code == 403

    def test_config_returns_schema_and_defaults(self, client, fresh_db):
        _login(client, "ADM-001", "admin", "赵管理")
        resp = client.get("/api/admin/config")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "schema" in data and "config" in data
        # 默认值生效
        assert data["config"]["limit_travel_hotel"] == 1000
        assert data["config"]["limit_office"] == 200
        assert data["config"]["limit_other"] == 200
        # schema 分组完整
        groups = [g["group"] for g in data["schema"]]
        assert "💰 费用限额配置" in groups
        assert "🚨 异常检测规则" in groups
        assert "👥 审批权限分配" in groups
        # 费用限额分组含办公/其他
        limit_group = next(g for g in data["schema"] if g["group"] == "💰 费用限额配置")
        limit_keys = {it["key"] for it in limit_group["items"]}
        assert "limit_office" in limit_keys
        assert "limit_other" in limit_keys

    def test_config_save_persists_and_audits(self, client, fresh_db):
        _login(client, "ADM-001", "admin", "赵管理")
        # 修改住宿限额
        resp = client.post(
            "/api/admin/config",
            json={"items": {"limit_travel_hotel": 6000}},
        )
        assert resp.status_code == 200
        assert resp.get_json()["config"]["limit_travel_hotel"] == 6000

        # 再次读取应反映已保存值
        resp2 = client.get("/api/admin/config")
        assert resp2.get_json()["config"]["limit_travel_hotel"] == 6000

        # 审计日志应记录 CONFIG_UPDATE
        audit = client.get("/api/admin/audit").get_json()["items"]
        assert any(a["action"] == "CONFIG_UPDATE" for a in audit)

    def test_config_reset(self, client, fresh_db):
        _login(client, "ADM-001", "admin", "赵管理")
        client.post("/api/admin/config", json={"items": {"limit_travel_hotel": 9999}})
        resp = client.post("/api/admin/config/reset")
        assert resp.status_code == 200
        assert resp.get_json()["config"]["limit_travel_hotel"] == 1000


# ── 审计日志 ──
class TestAdminAudit:
    def test_audit_requires_login(self, client, fresh_db):
        resp = client.get("/api/admin/audit")
        assert resp.status_code == 401

    def test_audit_forbidden_for_employee(self, client, fresh_db):
        _login(client, "EMP-2026", "employee", "张三")
        resp = client.get("/api/admin/audit")
        assert resp.status_code == 403

    def test_audit_returns_seeded_logs(self, client, seeded):
        _login(client, "ADM-001", "admin", "赵管理")
        resp = client.get("/api/admin/audit")
        assert resp.status_code == 200
        items = resp.get_json()["items"]
        assert len(items) > 0
        # 演示数据含登录 / 审批等动作
        actions = {a["action"] for a in items}
        assert "LOGIN" in actions
        assert "APPROVE" in actions

    def test_audit_records_approve_action(self, client, fresh_db):
        # 审批动作应写入审计日志
        from skill.utils.db_store import save_invoice, save_reimbursement

        save_reimbursement(
            request_id="REQ-ADM-1", employee_id="EMP-2026", apply_amount=358.50,
            apply_date="2026-07-14", reason="审计测试", expense_category="差旅",
        )
        save_invoice({"发票号码": "INV-ADM-1", "发票金额": 358.50, "销售方名称": "X"}, "REQ-ADM-1", "")
        _login(client, "APR-001", "approver", "李总")
        client.post("/api/approve", json={"request_id": "REQ-ADM-1", "action": "通过"})
        _login(client, "ADM-001", "admin", "赵管理")
        items = client.get("/api/admin/audit").get_json()["items"]
        assert any(a["action"] == "APPROVE" and "REQ-ADM-1" in a["target"] for a in items)


# ── 用量统计 ──
class TestAdminUsage:
    def test_usage_requires_login(self, client, fresh_db):
        resp = client.get("/api/admin/usage")
        assert resp.status_code == 401

    def test_usage_forbidden_for_employee(self, client, fresh_db):
        _login(client, "EMP-2026", "employee", "张三")
        resp = client.get("/api/admin/usage")
        assert resp.status_code == 403

    def test_usage_returns_aggregates(self, client, seeded):
        _login(client, "ADM-001", "admin", "赵管理")
        resp = client.get("/api/admin/usage")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "overview" in data and "daily" in data and "by_type" in data and "records" in data
        # 演示数据聚合：总调用次数应等于按类型分布之和
        overview = data["overview"]
        by_type = data["by_type"]
        assert overview["total_calls"] == sum(t["calls"] for t in by_type)
        assert overview["total_tokens"] == sum(t["tokens"] for t in by_type)
        # 每日趋势非空且为近 7 天
        assert len(data["daily"]) <= 7
        # 明细含失败记录
        assert any(r["status"] == "失败" for r in data["records"])

    def test_usage_filter_by_type(self, client, seeded):
        _login(client, "ADM-001", "admin", "赵管理")
        resp = client.get("/api/admin/usage?call_type=异常检测")
        records = resp.get_json()["records"]
        assert all(r["call_type"] == "异常检测" for r in records)
