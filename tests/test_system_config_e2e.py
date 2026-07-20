"""系统配置（原型系统配置内容）端到端测试

覆盖 prototype.html「系统配置」分组的新增项与依赖代码：
    - 🤖 启用/停用 DeepSeek 大模型（ds_enabled / api_key / base_url / model）
    - 🚨 检测发票真伪开关（rule_invoice_auth）经 verify 节点生效
    - 🚨 行程单字段完整性开关（rule_itinerary_field）
    - 💰 餐饮 月度限额（label 对齐）
并验证：配置保存落库 → 运行时 getter 生效 → 各工具/节点行为随之改变。
"""

import pytest

from skill.config import (
    get_category_limits,
    get_deepseek_settings,
    get_itinerary_rules,
    get_verify_rules,
)
from skill.orchestrator.state import CheckStatus, ReimbursementState
from skill.utils import admin_store
from web.app import app as _web_app


@pytest.fixture
def client():
    _web_app.config["TESTING"] = True
    with _web_app.test_client() as c:
        yield c


@pytest.fixture
def cfg_client(client, fresh_db):
    """登录为系统管理员（admin 角色）。"""
    admin_store.ensure_seeded()
    with client.session_transaction() as sess:
        sess["account"] = "ADM-001"
        sess["role"] = "admin"
        sess["name"] = "赵管理"
    return client


# ─────────────────────────────────────────────
# 1. Schema / 默认值 与 原型对齐
# ─────────────────────────────────────────────
class TestSystemConfigSchema:
    def test_deepseek_group_present(self, fresh_db):
        schema = admin_store.get_config_schema()
        groups = {g["group"]: g for g in schema}
        assert "🤖 启用/停用Deepseek大模型" in groups
        keys = {it["key"] for it in groups["🤖 启用/停用Deepseek大模型"]["items"]}
        assert keys == {
            "ds_enabled",
            "deepseek_api_key",
            "deepseek_base_url",
            "deepseek_model",
        }

    def test_deepseek_item_types(self, fresh_db):
        schema = admin_store.get_config_schema()
        groups = {g["group"]: g for g in schema}
        items = {it["key"]: it for it in groups["🤖 启用/停用Deepseek大模型"]["items"]}
        assert items["ds_enabled"]["type"] == "toggle"
        assert items["deepseek_api_key"]["type"] == "secret"
        assert items["deepseek_base_url"]["type"] == "text"
        assert items["deepseek_model"]["type"] == "text"

    def test_default_values(self, fresh_db):
        cfg = admin_store.get_system_config()
        assert cfg["ds_enabled"] is True
        assert cfg["deepseek_model"] == "deepseek-v4-flash"
        assert cfg["deepseek_base_url"] == "https://api.deepseek.com/chat/completions"
        assert cfg["rule_invoice_auth"] is True

    def test_meal_limit_label_monthly(self, fresh_db):
        schema = admin_store.get_config_schema()
        labels = [
            it["label"] for g in schema for it in g["items"] if it["key"] == "limit_meal_single"
        ]
        assert labels == ["餐饮 月度限额"]


# ─────────────────────────────────────────────
# 2. 保存 → 落库 → 运行时 getter 生效
# ─────────────────────────────────────────────
class TestSystemConfigPersistence:
    def test_save_new_items_persists(self, fresh_db):
        merged = admin_store.save_system_config(
            {
                "ds_enabled": False,
                "deepseek_model": "deepseek-test-model",
                "deepseek_base_url": "https://example.test/v1",
                "rule_invoice_auth": False,
                "rule_itinerary_field": False,
            },
            operator="赵管理",
            role="系统管理员",
            ip="10.0.1.32",
        )
        assert merged["ds_enabled"] is False
        assert merged["deepseek_model"] == "deepseek-test-model"

        # 重新读取应反映已保存值
        reread = admin_store.get_system_config()
        assert reread["ds_enabled"] is False

    def test_getters_reflect_config(self, fresh_db):
        admin_store.save_system_config(
            {
                "ds_enabled": False,
                "deepseek_model": "deepseek-test-model",
                "rule_invoice_auth": False,
                "rule_itinerary_field": False,
            },
            operator="赵管理",
        )
        assert get_deepseek_settings()["enabled"] is False
        assert get_deepseek_settings()["model"] == "deepseek-test-model"
        assert get_verify_rules()["enable_invoice_auth"] is False
        assert get_itinerary_rules()["enable_itinerary_field"] is False

    def test_api_override_falls_back_to_env(self, fresh_db, monkeypatch):
        monkeypatch.setattr("skill.config.DEEPSEEK_API_KEY", "env-key")
        monkeypatch.setattr("skill.config.DEEPSEEK_MODEL", "env-model")
        # 管理员清空字段（留空）→ 回退到环境变量
        admin_store.save_system_config(
            {"deepseek_api_key": "", "deepseek_model": ""},
            operator="赵管理",
        )
        settings = get_deepseek_settings()
        assert settings["api_key"] == "env-key"
        assert settings["model"] == "env-model"

    def test_reset_restores_defaults(self, fresh_db):
        admin_store.save_system_config({"ds_enabled": False}, operator="赵管理")
        reset = admin_store.reset_system_config(operator="赵管理")
        assert reset["ds_enabled"] is True


# ─────────────────────────────────────────────
# 3. ds_enabled 关闭 → 模型调用跳过
# ─────────────────────────────────────────────
class TestDeepSeekDisabled:
    def test_call_deepseek_function_disabled(self, fresh_db):
        admin_store.save_system_config({"ds_enabled": False}, operator="赵管理")
        from skill.utils.http_client import call_deepseek_function

        # 关闭后不应发起任何网络请求，直接返回 _disabled 标记
        result = call_deepseek_function(
            system_prompt="x",
            user_content="y",
            tools=[],
            call_type="异常检测",
        )
        assert result.get("_disabled") is True

    def test_call_deepseek_function_enabled_no_network(self, fresh_db):
        # 默认启用，但没有网络且未 mock → 应返回 _error（而非崩溃）
        from skill.utils.http_client import call_deepseek_function

        result = call_deepseek_function(
            system_prompt="x",
            user_content="y",
            tools=[],
            call_type="异常检测",
        )
        # 无有效 key/网络时落到异常处理分支，但不应抛出
        assert isinstance(result, dict)


# ─────────────────────────────────────────────
# 4. rule_invoice_auth → verify 节点跳过查验
# ─────────────────────────────────────────────
class TestInvoiceAuthToggle:
    def _state(self, invoice):
        return ReimbursementState(
            pdf_path="",
            apply_amount=100.0,
            apply_date="2026-06-10",
            request_id="REQ-V-1",
            employee_id="EMP-2026",
            reason="",
            expense_category="差旅",
            ticket_type="发票",
            ocr_result=invoice,
            anomaly_result=None,
            classify_result=None,
            verify_result=None,
            itinerary_result=None,
            final_status=CheckStatus.PASS,
            summary="",
            warnings=[],
            errors=[],
            history=[],
        )

    def test_auth_disabled_skips_verify(self, fresh_db):
        from skill.orchestrator.nodes.verify_node import verify_node

        admin_store.save_system_config({"rule_invoice_auth": False}, operator="赵管理")
        out = verify_node(self._state({"发票号码": "12345678", "发票金额": 100.0}))
        assert out["verify_result"]["总体结论"] == "通过"
        assert "停用" in out["verify_result"]["查验摘要"]

    def test_auth_enabled_runs_verify(self, fresh_db):
        from skill.orchestrator.nodes.verify_node import verify_node

        # 默认启用；普通号码走 mock provider 返回正常
        out = verify_node(self._state({"发票号码": "12345678", "发票金额": 100.0}))
        assert out["verify_result"]["查验状态"] == "正常"


# ─────────────────────────────────────────────
# 5. 前端 API 端到端（保存新配置项）
# ─────────────────────────────────────────────
class TestAdminApiE2E:
    def test_save_new_config_items_via_api(self, cfg_client):
        resp = cfg_client.post(
            "/api/admin/config",
            json={
                "items": {
                    "ds_enabled": False,
                    "deepseek_model": "deepseek-e2e-model",
                    "rule_invoice_auth": False,
                }
            },
            headers={"X-CSRF-Token": "x"},  # 测试模式跳过校验，header 可选
        )
        assert resp.status_code == 200
        cfg = resp.get_json()["config"]
        assert cfg["ds_enabled"] is False
        assert cfg["deepseek_model"] == "deepseek-e2e-model"

        # 再次 GET 应反映已保存
        resp2 = cfg_client.get("/api/admin/config")
        assert resp2.get_json()["config"]["ds_enabled"] is False


class TestCountersignToggle:
    def test_countersign_toggle_in_schema(self, fresh_db):
        schema = admin_store.get_config_schema()
        groups = {g["group"]: g for g in schema}
        items = {it["key"]: it for it in groups["👥 审批权限分配"]["items"]}
        assert "countersign_enabled" in items
        assert items["countersign_enabled"]["type"] == "toggle"
        assert (
            items["countersign_enabled"]["label"]
            == "金额 ≥ 10000 元 需两人会签（在对应级别基础上增加一位审批人）"
        )

    def test_countersign_default_enabled(self, fresh_db):
        assert admin_store.get_system_config()["countersign_enabled"] is True

    def test_countersign_off_disables_route(self, fresh_db):
        from skill.tools.tool_approval_routing import determine_approval_route

        # 默认开启：≥10000 需会签
        assert determine_approval_route(60000)["需要会签"] is True
        # 管理员关闭后，路由不再要求会签
        admin_store.save_system_config({"countersign_enabled": False}, operator="赵管理")
        assert determine_approval_route(60000)["需要会签"] is False
        # 重新开启后恢复
        admin_store.save_system_config({"countersign_enabled": True}, operator="赵管理")
        assert determine_approval_route(60000)["需要会签"] is True


class TestCategoryLimitOverride:
    def test_office_limit_overrides_yaml(self, fresh_db):
        # 默认 YAML 办公限额
        assert get_category_limits()["办公"] == 200.0
        # 管理员保存新限额后，覆盖应生效
        admin_store.save_system_config({"limit_office": 500}, operator="赵管理")
        assert get_category_limits()["办公"] == 500.0

    def test_other_limit_overrides_yaml(self, fresh_db):
        assert get_category_limits()["其他"] == 200.0
        admin_store.save_system_config({"limit_other": 800}, operator="赵管理")
        assert get_category_limits()["其他"] == 800.0

    def test_all_category_limits_override(self, fresh_db):
        admin_store.save_system_config(
            {
                "limit_travel_transport": 300,
                "limit_travel_hotel": 700,
                "limit_meal_single": 400,
                "limit_office": 150,
                "limit_other": 250,
            },
            operator="赵管理",
        )
        limits = get_category_limits()
        assert limits["交通"] == 300.0
        assert limits["住宿"] == 700.0
        assert limits["餐饮"] == 400.0
        assert limits["办公"] == 150.0
        assert limits["其他"] == 250.0
