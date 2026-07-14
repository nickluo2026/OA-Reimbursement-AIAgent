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
from skill.database import init_db
from skill.utils.structured_log import set_request_id, get_request_id

# ── 数据库初始化 ──
init_db()

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
        # 原型演示：密码任意，仅记录账号与角色
        info = DEMO_ACCOUNTS.get(account, {"name": account, "role": role})
        session["account"] = account
        session["role"] = role
        session["name"] = info["name"]
        logger.info("用户登录 account=%s role=%s", account, role)
        return redirect(url_for("index"))
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
    employee_id = request.form.get("employee_id", "unknown").strip()

    logger.info("收到报销申请 request_id=%s filename=%s amount=%s", request_id, file.filename, apply_amount)

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

    # ── 清理临时文件 ──
    try:
        save_path.unlink(missing_ok=True)
    except Exception:
        pass

    return jsonify(result)


@app.errorhandler(413)
def request_entity_too_large(_e):
    return jsonify({"status": "错误", "summary": "文件大小超过 10MB 限制"}), 413


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5001)
