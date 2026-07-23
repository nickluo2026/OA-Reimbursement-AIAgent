"""一次性冒烟测试：模拟 DeepSeek 停用态 上传→手动提交（含发票号）。"""
import sys, os, io
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import skill.config as cfg
_orig = cfg.get_deepseek_enabled
cfg.get_deepseek_enabled = lambda: False  # 强制停用

from web.app import app
from skill import workflow as wf
from skill.utils.db_store import check_duplicate_invoice, get_invoices_for_request

app.config["TESTING"] = True
c = app.test_client()

# 1) 登录员工
with c.session_transaction() as s:
    s["account"] = "EMP-2026"; s["role"] = "employee"; s["name"] = "张三"

# 2) 上传（停用态）
pdf = b"%PDF-1.4 fake"
r = c.post("/upload", data={"file": (io.BytesIO(pdf), "inv.pdf"),
                            "ticket_type": "发票"}, content_type="multipart/form-data")
print("upload status:", r.status_code)
body = r.get_json()
print("upload status field:", body.get("status"))
rid = body.get("_request_id")
assert rid, "缺少 _request_id"
print("request_id:", rid)

# 停用态不应有发票记录
assert get_invoices_for_request(rid) == [], "停用态不应预建发票"
print("OK: 停用态未预建发票")

# 3) 手动提交（含发票号）
r2 = c.post(f"/api/reimbursement/{rid}/update", json={
    "apply_amount": "123.45", "apply_date": "2026-07-23",
    "expense_category": "交通", "reason": "停用态手填", "invoice_number": "SMOKE-INV-001"})
print("submit status:", r2.status_code)
d = r2.get_json()
print("submit resp:", {k: d.get(k) for k in ("request_id", "workflow_status", "apply_amount", "expense_category")})
print("invoices:", d.get("invoices"))
assert d.get("workflow_status") == wf.WS_PENDING
assert d["invoices"][0]["invoice_number"] == "SMOKE-INV-001"
assert check_duplicate_invoice("SMOKE-INV-001") is True
print("OK: 停用态手动提交 + 发票号落库 + 防重生效")

# 4) 无报销单/无发票记录/又未手填发票号 → 404
r3 = c.post("/api/reimbursement/SMOKE-NEW/update", json={"expense_category": "交通"})
print("missing-invoice status:", r3.status_code, "(期望 404)")
assert r3.status_code == 404

cfg.get_deepseek_enabled = _orig
print("\nALL SMOKE CHECKS PASSED")
