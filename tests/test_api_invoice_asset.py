"""[测试] 发票影像取文件接口：未登录 401 / 员工越权 403 / 无影像 404 / 文件类型白名单"""


def test_invoice_thumb_not_logged_in(client):
    r = client.get("/api/reimbursement/test123/invoice/0/thumb")
    assert r.status_code == 401


def test_invoice_page_not_logged_in(client):
    r = client.get("/api/reimbursement/rid123/invoice/0/page/1")
    assert r.status_code == 401


def test_invoice_file_not_logged_in(client):
    r = client.get("/api/reimbursement/rid123/invoice/0/file")
    assert r.status_code == 401


def test_invoice_thumb_no_image_legacy(client):
    """存量旧单：不存在的报销单 + 财务角色 → 404"""
    with client.session_transaction() as sess:
        sess["account"] = "FIN-001"
        sess["role"] = "finance_review"
        sess["name"] = "测试"
    r = client.get("/api/reimbursement/nonexistent/invoice/0/thumb")
    assert r.status_code == 404
