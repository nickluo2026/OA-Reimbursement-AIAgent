"""Flask 应用 — 发票报销校验 Web 入口。

接收前端的发票上传请求，调用 Skill 引擎完成：
    1. 票据解析
    2. 规则引擎校验
    3. 分类限额校验
    4. 返回结构化结果

相关路由：
    GET  /          渲染上传页面
    POST /upload    处理上传与 AI 校验
"""

import os
import uuid
import logging
from pathlib import Path

from flask import Flask, render_template, request, jsonify, redirect, url_for, session

from skill import run_reimbursement_skill
from skill import workflow as wf
from skill.database import init_db
from skill.utils import admin_store
from skill.utils.structured_log import set_request_id, get_request_id

# ── 数据库初始化 ──
init_db()
try:
    admin_store.ensure_seeded()
except Exception:  # pragma: no cover - 演示数据预置失败不阻断启动
    pass

# ── 日志 ──
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s [%(request_id)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)


class RequestIDFilter(logging.Filter):
    """为所有日志记录注入 request_id，避免第三方库（如 werkzeug）日志因缺少该字段而报 KeyError"""
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            record.request_id = get_request_id()
        return True


# 将 Filter 注册到根 Logger 的所有 Handler，确保第三方日志也生效
for handler in logging.root.handlers:
    handler.addFilter(RequestIDFilter())

# ── 配置 ──
BASE_DIR = Path(__file__).resolve().parent.parent
UPLOAD_DIR = BASE_DIR / "uploads"
ALLOWED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png"}
MAX_CONTENT_LENGTH = 10 * 1024 * 1024  # 10 MB

UPLOAD_DIR.mkdir(exist_ok=True)

app = Flask(
    __name__,
    template_folder=BASE_DIR / "web" / "templates",
    static_folder=BASE_DIR / "web" / "static",
    static_url_path="/static",
)
app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-key-prototype")

# ── 演示账号与角色配置（对应 prototype.html，原型演示无需密码校验）──
# 角色定义：对应 design.md §1.1 / §17.2 与 constitution.md §2.6
ROLE_INFO = {
    "employee": {"icon": "👤", "name": "普通员工", "desc": "提交日常差旅、餐饮、住宿等报销"},
    "approver": {"icon": "👔", "name": "审批领导", "desc": "审核下属报销申请"},
    "finance":  {"icon": "💼", "name": "财务人员", "desc": "财务终审与打款"},
    "admin":    {"icon": "⚙️", "name": "系统管理员", "desc": "维护报销制度规则"},
}

# 演示账号映射（工号 → 姓名），密码任意
DEMO_ACCOUNTS = {
    "EMP-2026": {"name": "张三", "role": "employee"},
    "APR-001":  {"name": "李总", "role": "approver"},
    "FIN-001":  {"name": "王会计", "role": "finance"},
    "ADM-001":  {"name": "赵管理", "role": "admin"},
}


def allowed_file(filename: str) -> bool:
    ext = Path(filename).suffix.lower()
    return ext in ALLOWED_EXTENSIONS


@app.route("/login", methods=["GET", "POST"])
def login():
    """登录页：4 角色选择 + 工号密码（原型演示，密码任意）。

    对应 design.md §11 认证授权架构、§17 前端架构设计。
    """
    if request.method == "POST":
        account = request.form.get("account", "").strip()
        password = request.form.get("password", "").strip()
        role = request.form.get("role", "employee")
        if not account or not password:
            return render_template("login.html", error="请输入工号和密码", selected_role=role)
        # 原型演示：密码任意。角色以工号映射为准，避免下拉框误选导致越权或失权。
        info = DEMO_ACCOUNTS.get(account)
        if info is None:
            # 未知工号：原型演示允许任意账号，角色回退到表单所选
            info = {"name": account, "role": role}
        session["account"] = account
        session["role"] = info["role"]
        session["name"] = info["name"]
        logger.info("用户登录 account=%s role=%s", account, info["role"])
        # 按角色跳转到默认工作台
        target = {
            "approver": "approve_page",
            "finance": "finance_page",
            "admin": "admin_page",
        }.get(info["role"], "index")
        return redirect(url_for(target))
    return render_template("login.html")


@app.route("/logout")
def logout():
    """登出：清除 session，返回登录页。"""
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
def index():
    """首页：需登录，按角色展示用户信息（对应 design.md §17.4）。"""
    if "account" not in session:
        return redirect(url_for("login"))
    role = session.get("role", "employee")
    role_info = ROLE_INFO.get(role, ROLE_INFO["employee"])
    return render_template(
        "index.html",
        user_name=session.get("name", "—"),
        user_account=session.get("account", "—"),
        user_role=role_info["name"],
        user_icon=role_info["icon"],
        role_desc=role_info["desc"],
        role_key=role,
    )


@app.route("/upload", methods=["POST"])
def upload():
    # ── 参数校验 ──
    if "file" not in request.files:
        return jsonify({"status": "错误", "summary": "未检测到上传文件"}), 400

    file = request.files["file"]
    if not file or not file.filename:
        return jsonify({"status": "错误", "summary": "文件名不能为空"}), 400

    if not allowed_file(file.filename):
        return jsonify({"status": "错误", "summary": "仅支持 PDF / JPG / PNG 格式"}), 400

    # ── 保存上传文件 ──
    ext = Path(file.filename).suffix.lower()
    save_name = f"{uuid.uuid4().hex}{ext}"
    save_path = UPLOAD_DIR / save_name
    file.save(str(save_path))
    logger.info("文件已保存: %s", save_path)

    # ── 表单参数 ──
    apply_amount_raw = request.form.get("apply_amount", "").strip()
    apply_amount = float(apply_amount_raw) if apply_amount_raw else None
    apply_date = request.form.get("apply_date", "").strip() or None
    reason = request.form.get("reason", "").strip()
    expense_category = request.form.get("expense_category", "").strip()
    remark = request.form.get("remark", "").strip()
    ticket_type = request.form.get("ticket_type", "发票").strip() or "发票"

    # ── 调用 AI 校验 ──
    request_id = uuid.uuid4().hex[:16]
    set_request_id(request_id)

    logger.info("收到报销申请 request_id=%s filename=%s amount=%s", request_id, file.filename, apply_amount)

    # 提交人取登录账号（前端未传 employee_id 时回退到 session）
    employee_id = request.form.get("employee_id", "").strip() or session.get("account", "unknown")

    try:
        result = run_reimbursement_skill(
            pdf_path=str(save_path),
            apply_amount=apply_amount,
            apply_date=apply_date,
            request_id=request_id,
            employee_id=employee_id,
            reason=reason,
            expense_category=expense_category,
            ticket_type=ticket_type,
        )
    except Exception as e:
        logger.exception("AI 校验异常")
        result = {"status": "错误", "summary": f"AI 校验异常: {e}"}

    # ── 补充表单信息到结果（方便结果页展示） ──
    result["_form"] = {
        "apply_amount": apply_amount,
        "apply_date": apply_date or "",
        "reason": reason,
        "expense_category": expense_category,
        "remark": remark,
        "filename": file.filename,
        "ticket_type": ticket_type,
    }
    result["_request_id"] = request_id

    # ── 写入审计日志（提交报销）──
    try:
        if "account" in session:
            amt_str = f"¥{apply_amount:.2f}" if apply_amount is not None else ""
            target = f"{request_id} · {amt_str}" if amt_str else request_id
            admin_store.add_audit_log(
                session.get("name", "员工"),
                session.get("role", "员工"),
                "SUBMIT",
                target,
                "成功",
                request.remote_addr,
            )
    except Exception:
        pass

    # ── 清理临时文件 ──
    try:
        save_path.unlink(missing_ok=True)
    except Exception:
        pass

    return jsonify(result)


# ═══════════════════════════════════════════════
# 通用辅助：登录 / 角色校验 / 序列化
# ═══════════════════════════════════════════════
def _require_login():
    """未登录返回 401；否则返回 None。"""
    if "account" not in session:
        return jsonify({"error": "请先登录"}), 401
    return None


def _require_role(role: str):
    """未登录 401；角色不符 403；否则返回 None。"""
    err = _require_login()
    if err:
        return err
    if session.get("role") != role:
        return jsonify({"error": "无权限访问该资源"}), 403
    return None


def employee_display_name(employee_id: str) -> str:
    """工号 → 姓名（演示账号映射，未知则回退工号）。"""
    info = DEMO_ACCOUNTS.get(employee_id)
    return info["name"] if info else employee_id


def _serialize_with_name(r):
    """报销单序列化并附带提交人姓名（前端列表展示用）。"""
    return dict(wf.serialize(r), employee_name=employee_display_name(r.employee_id))


# ═══════════════════════════════════════════════
# 审批领导 / 财务 / 管理员 工作台页面
# ═══════════════════════════════════════════════
@app.route("/approve")
def approve_page():
    """审批领导工作台：需登录且角色为 approver，否则展示无权限提示。"""
    if "account" not in session:
        return redirect(url_for("login"))
    role = session.get("role", "employee")
    role_info = ROLE_INFO.get(role, ROLE_INFO["employee"])
    return render_template(
        "approve.html",
        user_name=session.get("name", "—"),
        user_account=session.get("account", "—"),
        user_role=role_info["name"],
        user_icon=role_info["icon"],
        role_desc=role_info["desc"],
        role_key=role,
        forbidden=role != "approver",
    )


@app.route("/finance")
def finance_page():
    """财务终审工作台：需登录且角色为 finance。"""
    if "account" not in session:
        return redirect(url_for("login"))
    role = session.get("role", "employee")
    role_info = ROLE_INFO.get(role, ROLE_INFO["employee"])
    return render_template(
        "finance.html",
        user_name=session.get("name", "—"),
        user_account=session.get("account", "—"),
        user_role=role_info["name"],
        user_icon=role_info["icon"],
        role_desc=role_info["desc"],
        role_key=role,
        forbidden=role != "finance",
    )


@app.route("/admin")
def admin_page():
    """系统管理员工作台：需登录且角色为 admin。"""
    if "account" not in session:
        return redirect(url_for("login"))
    role = session.get("role", "employee")
    role_info = ROLE_INFO.get(role, ROLE_INFO["employee"])
    return render_template(
        "admin.html",
        user_name=session.get("name", "—"),
        user_account=session.get("account", "—"),
        user_role=role_info["name"],
        user_icon=role_info["icon"],
        role_desc=role_info["desc"],
        role_key=role,
        forbidden=role != "admin",
    )


# ═══════════════════════════════════════════════
# 审批领导工作台 API
# ═══════════════════════════════════════════════
@app.route("/api/approve/list")
def api_approve_list():
    """待审列表（审批领导）。"""
    err = _require_role("approver")
    if err:
        return err
    items = wf.list_pending()
    serialized = [_serialize_with_name(r) for r in items]
    return jsonify({
        "count": len(serialized),
        "items": serialized,
        "done_this_month": wf.count_decisions_this_month(session["account"]),
    })


@app.route("/api/approve", methods=["POST"])
def api_approve():
    """审批决策：通过 / 驳回 / 转审。"""
    err = _require_role("approver")
    if err:
        return err
    data = request.get_json(silent=True) or {}
    request_id = data.get("request_id")
    action = data.get("action")
    comment = data.get("comment", "")
    if not request_id or not action:
        return jsonify({"error": "缺少 request_id 或 action"}), 400
    try:
        result = wf.submit_approval(
            request_id, session["account"], session.get("name", ""), action, comment
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    # 审计日志：APPROVE / REJECT / TRANSFER
    try:
        audit_action = {"通过": "APPROVE", "驳回": "REJECT", "转审": "TRANSFER"}.get(action, action or "")
        admin_store.add_audit_log(
            session.get("name", "审批领导"), session.get("role", "审批领导"),
            audit_action, f"{request_id} · ¥{result['apply_amount']:.2f}", "成功", request.remote_addr,
        )
    except Exception:
        pass
    return jsonify({"status": "ok", "data": result})


# ═══════════════════════════════════════════════
# 财务终审工作台 API
# ═══════════════════════════════════════════════
@app.route("/api/finance/list")
def api_finance_list():
    """财务列表（已通过 / 已归档）。"""
    err = _require_role("finance")
    if err:
        return err
    items = wf.list_for_finance()
    serialized = [_serialize_with_name(r) for r in items]
    return jsonify({
        "items": serialized,
        "pending_archive": sum(1 for i in items if i.workflow_status == wf.WS_APPROVED),
        "archived": sum(1 for i in items if i.workflow_status == wf.WS_ARCHIVED),
        "paid": sum(1 for i in items if i.workflow_status == wf.WS_PAID),
    })


@app.route("/api/finance", methods=["POST"])
def api_finance():
    """财务操作：归档 / 打款。"""
    err = _require_role("finance")
    if err:
        return err
    data = request.get_json(silent=True) or {}
    request_id = data.get("request_id")
    action = data.get("action")
    comment = data.get("comment", "")
    if not request_id or not action:
        return jsonify({"error": "缺少 request_id 或 action"}), 400
    try:
        result = wf.submit_finance(
            request_id, session["account"], session.get("name", ""), action, comment
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    # 审计日志：ARCHIVE / PAYMENT_INIT
    try:
        audit_action = {"归档": "ARCHIVE", "打款": "PAYMENT_INIT"}.get(action, action or "")
        admin_store.add_audit_log(
            session.get("name", "财务人员"), session.get("role", "财务人员"),
            audit_action, f"{request_id} · ¥{result['apply_amount']:.2f}", "成功", request.remote_addr,
        )
    except Exception:
        pass
    return jsonify({"status": "ok", "data": result})


# ═══════════════════════════════════════════════
# 报销单明细 / 我的报销
# ═══════════════════════════════════════════════
@app.route("/api/reimbursement/<request_id>")
def api_reimbursement_detail(request_id):
    """报销单完整明细（发票 / AI 校验 / 审批记录 / 路由）。"""
    err = _require_login()
    if err:
        return err
    detail = wf.get_detail(request_id)
    if not detail:
        return jsonify({"error": "报销单不存在"}), 404
    return jsonify(detail)


@app.route("/api/my")
def api_my():
    """当前用户提交的报销单列表。"""
    err = _require_login()
    if err:
        return err
    items = wf.list_by_employee(session["account"])
    serialized = [_serialize_with_name(r) for r in items]
    return jsonify({"count": len(serialized), "items": serialized})


# ═══════════════════════════════════════════════
# 系统管理员后台 API
# ═══════════════════════════════════════════════
@app.route("/api/admin/config", methods=["GET", "POST"])
def api_admin_config():
    """系统配置：GET 返回 schema + 当前值；POST 保存配置（落库 + 写审计）。"""
    err = _require_role("admin")
    if err:
        return err
    if request.method == "GET":
        return jsonify({
            "schema": admin_store.get_config_schema(),
            "config": admin_store.get_system_config(),
        })
    data = request.get_json(silent=True) or {}
    items = data.get("items", {})
    merged = admin_store.save_system_config(
        items, operator=session.get("name", "系统管理员"),
        role="系统管理员", ip=request.remote_addr,
    )
    return jsonify({"status": "ok", "config": merged})


@app.route("/api/admin/config/reset", methods=["POST"])
def api_admin_config_reset():
    """恢复系统配置为默认值。"""
    err = _require_role("admin")
    if err:
        return err
    merged = admin_store.reset_system_config(
        operator=session.get("name", "系统管理员"),
        role="系统管理员", ip=request.remote_addr,
    )
    return jsonify({"status": "ok", "config": merged})


@app.route("/api/admin/audit")
def api_admin_audit():
    """审计日志列表。"""
    err = _require_role("admin")
    if err:
        return err
    return jsonify({"items": admin_store.list_audit_log()})


@app.route("/api/admin/usage")
def api_admin_usage():
    """用量统计：概览 / 每日 / 按类型 / 明细（支持筛选）。"""
    err = _require_role("admin")
    if err:
        return err
    date_filter = request.args.get("date")
    type_filter = request.args.get("call_type")
    status_filter = request.args.get("status")
    return jsonify({
        "overview": admin_store.get_usage_overview(),
        "daily": admin_store.get_usage_daily(),
        "by_type": admin_store.get_usage_by_type(),
        "records": admin_store.list_usage_records(date_filter, type_filter, status_filter),
    })


@app.errorhandler(413)
def request_entity_too_large(_e):
    return jsonify({"status": "错误", "summary": "文件大小超过 10MB 限制"}), 413


if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=5001)
