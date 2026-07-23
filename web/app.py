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

import logging
import os
import secrets
import uuid
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from skill import run_reimbursement_skill
from skill import workflow as wf
from skill.config import get_deepseek_enabled
from skill.database import init_db
from skill.utils.db_store import (
    check_duplicate_invoice,
    save_invoice,
    update_invoice_fields,
)
from skill.utils import admin_store
from skill.utils.mask_sensitive import mask_amount, mask_ocr_result
from skill.utils.structured_log import get_request_id, set_request_id

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

# ── Secret Key：禁止使用硬编码默认值 ──
# [S-005] 生产环境必须通过 FLASK_SECRET_KEY 环境变量设置固定密钥；
#         未设置时生成随机密钥（每次重启后 session 失效），并输出警告。
_flask_secret = os.environ.get("FLASK_SECRET_KEY")
if not _flask_secret:
    _flask_secret = secrets.token_hex(32)
    logger.warning(
        "FLASK_SECRET_KEY 环境变量未设置，已生成随机临时密钥。"
        "生产环境请务必通过环境变量指定固定密钥。"
    )
app.secret_key = _flask_secret

# ── Session Cookie 安全属性 ──
# [S-006] SESSION_COOKIE_SECURE：生产环境（HTTPS）启用，防止 Cookie 在 HTTP 连接中被截获
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("OA_ENV") == "production",
)

# ── 运行环境 & 模板/静态缓存策略（缓存修复）──
# OA_ENV 取值：production（默认，生产）/ development（开发/演示）
# APP_VERSION 为发布版本号，用于静态资源 URL 的缓存破坏（cache-busting），发布时自增。
# [缓存修复-②] 开发/演示环境开启 Jinja 模板自动重载：磁盘上的 *.html 改动后立即重新编译，
#              无需重启进程即可看到更新（debug=False 时此开关默认关闭，是“页面不刷新”的根因）。
# [缓存修复-③] 开发/演示环境关闭静态资源缓存（SEND_FILE_MAX_AGE_DEFAULT=0），
#              浏览器每次都重新拉取 /static/*.js *.css，杜绝浏览器侧陈旧。
# 生产环境保持默认（模板不自动重载以保性能，静态资源长缓存），更新内容必须靠「部署即重启」刷新。
OA_ENV = os.environ.get("OA_ENV", "production")
APP_VERSION = os.environ.get("APP_VERSION", "20260722")
if OA_ENV != "production" or os.environ.get("FLASK_DEBUG") == "1":
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

# ── 演示账号与角色配置（对应 prototype.html，原型演示无需密码校验）──
# 角色定义：对应 design.md §1.1 / §17.2 与 constitution.md §2.6
ROLE_INFO = {
    "employee": {"icon": "👤", "name": "员工", "desc": "提交日常差旅、餐饮、住宿等报销"},
    "approver": {"icon": "👔", "name": "主管", "desc": "审核下属报销申请"},
    "finance_review": {"icon": "📋", "name": "财务", "desc": "复核 AI 校验结果并确认归档"},
    "finance_pay": {"icon": "💰", "name": "出纳", "desc": "发起费用发放（须与归档人不同）"},
    "admin": {"icon": "⚙️", "name": "系统管理员", "desc": "维护报销制度规则"},
}

# 演示账号映射（工号 → 姓名 / 角色 / 密码哈希）
# [S-001] 密码使用 werkzeug PBKDF2 哈希存储，不再接受任意密码。
#         演示密码统一为 "123456"，生产环境应替换为真实密码库。
# 财务职责分离：FIN-001 财务（仅归档）、FIN-002 出纳（仅打款），
# 且系统强制「打款人 ≠ 归档人」。
DEMO_ACCOUNTS = {
    "EMP-2026": {
        "name": "张三",
        "role": "employee",
        "password_hash": generate_password_hash("123456"),
    },
    "APR-001": {
        "name": "李总",
        "role": "approver",
        "password_hash": generate_password_hash("123456"),
    },
    "FIN-001": {
        "name": "王会计",
        "role": "finance_review",
        "password_hash": generate_password_hash("123456"),
    },
    "FIN-002": {
        "name": "李出纳",
        "role": "finance_pay",
        "password_hash": generate_password_hash("123456"),
    },
    "ADM-001": {
        "name": "赵管理",
        "role": "admin",
        "password_hash": generate_password_hash("123456"),
    },
}

# 财务两类角色（财务 / 出纳）共享财务工作台与财务 API 权限
FINANCE_ROLES = ("finance_review", "finance_pay")


def allowed_file(filename: str) -> bool:
    ext = Path(filename).suffix.lower()
    return ext in ALLOWED_EXTENSIONS


# ═══════════════════════════════════════════════
# CSRF 防护 [S-007]
# ═══════════════════════════════════════════════
def _get_csrf_token() -> str:
    """获取或生成 CSRF token，存储在 session 中。"""
    if "_csrf_token" not in session:
        session["_csrf_token"] = secrets.token_hex(32)
    return session["_csrf_token"]


@app.context_processor
def _inject_csrf_token():
    """向所有模板注入 csrf_token 与 app_version 变量（app_version 用于静态资源缓存破坏）。"""
    return {"csrf_token": _get_csrf_token(), "app_version": APP_VERSION}


@app.before_request
def _csrf_protect():
    """对所有状态变更请求（POST/PUT/DELETE/PATCH）校验 CSRF token。

    - GET /login 页面渲染时通过 context_processor 生成 token 并写入 session；
    - 登录表单 POST 时在路由内部校验 CSRF（以便返回 HTML 错误页而非 JSON）；
    - 其他 POST 路由（AJAX）通过 X-CSRF-Token 请求头校验；
    - 测试模式（TESTING=True）跳过校验，兼容现有测试。
    """
    if request.method not in ("POST", "PUT", "DELETE", "PATCH"):
        return
    if app.config.get("TESTING"):
        return
    # 登录路由单独处理 CSRF（在路由内部校验，以便渲染错误页面）
    if request.endpoint == "login":
        return
    session_token = session.get("_csrf_token")
    submitted = request.form.get("_csrf_token") or request.headers.get("X-CSRF-Token")
    if not session_token or not submitted or submitted != session_token:
        return jsonify({"error": "CSRF token 校验失败，请刷新页面重试"}), 400


@app.route("/login", methods=["GET", "POST"])
def login():
    """登录页：工号 + 密码认证。

    [S-001] 密码使用 PBKDF2 哈希校验，不再接受任意密码。
    [S-002] 仅允许预注册账号登录，移除未知工号回退逻辑。
    [S-007] 表单提交需携带 CSRF token。
    """
    if request.method == "POST":
        # ── CSRF 校验 ──
        session_token = session.get("_csrf_token")
        submitted_token = request.form.get("_csrf_token", "")
        if not session_token or submitted_token != session_token:
            return render_template("login.html", error="会话已过期，请刷新页面重试"), 400

        account = request.form.get("account", "").strip()
        password = request.form.get("password", "").strip()
        if not account or not password:
            return render_template("login.html", error="请输入工号和密码")

        # [S-002] 仅允许预注册账号登录；角色由服务端映射决定，忽略前端 role 参数
        info = DEMO_ACCOUNTS.get(account)
        if info is None or not check_password_hash(info["password_hash"], password):
            # 记录登录失败审计
            try:
                admin_store.add_audit_log(
                    account or "未知",
                    "—",
                    "LOGIN_FAILED",
                    account or "—",
                    "失败",
                    request.remote_addr,
                )
            except Exception:
                pass
            return render_template("login.html", error="工号或密码错误")

        session["account"] = account
        session["role"] = info["role"]
        session["name"] = info["name"]
        logger.info("用户登录 account=%s role=%s", account, info["role"])

        # 登录成功审计
        try:
            admin_store.add_audit_log(
                info["name"],
                info["role"],
                "LOGIN",
                account,
                "成功",
                request.remote_addr,
            )
        except Exception:
            pass

        # 按角色跳转到默认工作台
        target = {
            "approver": "approve_page",
            "finance_review": "finance_page",
            "finance_pay": "finance_page",
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
    # [S-003] 登录校验：未登录用户不可上传文件或触发 AI 校验
    err = _require_login()
    if err:
        return err

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

    logger.info(
        "收到报销申请 request_id=%s filename=%s amount=%s", request_id, file.filename, apply_amount
    )

    # 提交人取登录账号（前端未传 employee_id 时回退到 session）
    employee_id = request.form.get("employee_id", "").strip() or session.get("account", "unknown")

    result: dict[str, Any]
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
        "报销单号": request_id,
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
            amt_str = mask_amount(apply_amount)
            target = amt_str if amt_str else "—"
            admin_store.add_audit_log(
                session.get("name", "员工"),
                session.get("role", "员工"),
                "SUBMIT",
                target,
                "成功",
                request.remote_addr,
                request_id=request_id,
            )
    except Exception:
        pass

    # ── 敏感数据脱敏（数据库保留完整数据，仅 API 返回时脱敏）──
    _ocr = result.get("ocr_result")
    if isinstance(_ocr, dict):
        result["ocr_result"] = mask_ocr_result(_ocr)

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


def _require_finance():
    """财务工作台/财务 API 准入：未登录 401；非财务角色 403；否则 None。

    财务（finance_review）与出纳（finance_pay）均放行，
    具体动作（归档 / 打款）的职责分离由 ``workflow.submit_finance`` 强制校验。
    """
    err = _require_login()
    if err:
        return err
    if session.get("role") not in FINANCE_ROLES:
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
# 主管 / 财务 / 管理员 工作台页面
# ═══════════════════════════════════════════════
@app.route("/approve")
def approve_page():
    """主管工作台：需登录且角色为 approver，否则展示无权限提示。"""
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
    """财务工作台：需登录且角色为财务 / 出纳。"""
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
        forbidden=role not in FINANCE_ROLES,
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
# 主管工作台 API
# ═══════════════════════════════════════════════
@app.route("/api/approve/list")
def api_approve_list():
    """待审列表（主管）。"""
    err = _require_role("approver")
    if err:
        return err
    items = wf.list_pending()
    serialized = [_serialize_with_name(r) for r in items]
    return jsonify(
        {
            "count": len(serialized),
            "items": serialized,
            "done_this_month": wf.count_decisions_this_month(session["account"]),
        }
    )


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
        audit_action = {"通过": "APPROVE", "驳回": "REJECT", "转审": "TRANSFER"}.get(
            action, action or ""
        )
        amt = result.get("apply_amount") if isinstance(result, dict) else None
        amt_str = (" " + mask_amount(amt)) if amt is not None else ""
        admin_store.add_audit_log(
            session.get("name", "主管"),
            session.get("role", "主管"),
            audit_action,
            f"{request_id}{amt_str}",
            "成功",
            request.remote_addr or "",
            request_id=request_id or "",
        )
    except Exception:
        logger.exception("审批审计日志写入失败 request_id=%s action=%s", request_id, action)
    return jsonify({"status": "ok", "data": result})


# ═══════════════════════════════════════════════
# 财务终审工作台 API
# ═══════════════════════════════════════════════
@app.route("/api/finance/list")
def api_finance_list():
    """财务列表（待复核 / 已复核并归档）。"""
    err = _require_finance()
    if err:
        return err
    items = wf.list_for_finance()
    serialized = [_serialize_with_name(r) for r in items]
    return jsonify(
        {
            "items": serialized,
            "pending_archive": sum(1 for i in items if i.workflow_status == wf.WS_APPROVED),
            "archived": sum(1 for i in items if i.workflow_status == wf.WS_ARCHIVED),
            "paid": sum(1 for i in items if i.workflow_status == wf.WS_PAID),
        }
    )


@app.route("/api/finance", methods=["POST"])
def api_finance():
    """财务操作：归档（财务）/ 打款（出纳）。"""
    err = _require_finance()
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
    # 审计日志：ARCHIVE / PAYMENT_INIT（按财务子角色区分，落实职责分离留痕）
    try:
        audit_action = {"归档": "ARCHIVE", "打款": "PAYMENT_INIT"}.get(action, action or "")
        audit_role = {"归档": "财务", "打款": "出纳"}.get(
            action, session.get("role", "财务人员")
        )
        audit_name = session.get("name", audit_role)
        admin_store.add_audit_log(
            audit_name,
            audit_role,
            audit_action,
            f"¥{result['apply_amount']:.2f}",
            "成功",
            request.remote_addr,
            request_id=request_id,
        )
        # 打款后补记「回单归档」审计，与 workflow 中的回单归档动作对应
        if action == "打款":
            admin_store.add_audit_log(
                audit_name,
                audit_role,
                "RECEIPT_ARCHIVE",
                mask_amount(result.get("apply_amount")),
                "成功",
                request.remote_addr,
                request_id=request_id,
            )
    except Exception:
        pass
    return jsonify({"status": "ok", "data": result})


# ═══════════════════════════════════════════════
# 报销单明细 / 我的报销
# ═══════════════════════════════════════════════
@app.route("/api/reimbursement/<request_id>")
def api_reimbursement_detail(request_id):
    """报销单完整明细（发票 / AI 校验 / 审批记录 / 路由）。

    [S-004] 数据归属校验：员工仅可查看本人提交的报销单；
            主管 / 财务 / 管理员可查看全部（职责范围内）。
    """
    err = _require_login()
    if err:
        return err
    # 员工越权防护：只能查看自己的报销单
    role = session.get("role", "employee")
    if role == "employee":
        reb = wf.get_reimbursement(request_id)
        if not reb or reb.employee_id != session["account"]:
            return jsonify({"error": "无权查看此报销单"}), 403
    detail = wf.get_detail(request_id)
    if not detail:
        return jsonify({"error": "报销单不存在"}), 404
    return jsonify(detail)


@app.route("/api/reimbursement/<request_id>/update", methods=["POST"])
def api_reimbursement_update(request_id):
    """更新报销单字段（AI 回写后人工确认落库），并支持停用态人工补录发票号码。

    - 员工仅可改本人「待审批」报销单；主管/财务/管理员可改任意待审批单
    - 仅「待审批」状态可改（workflow 层强制）
    - invoice_number：DeepSeek 停用态下人工补录的发票号码（可选），写入关联发票
      记录并做重复报销校验；停用态 OCR 未执行、不会预建发票记录，因此允许用户
      手填发票号后直接建单（无发票的行程单类报销单仍可不填发票号建单）。
    """
    err = _require_login()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    # 越权防护：员工仅可改本人待审批单
    role = session.get("role", "employee")
    invoice_number = (data.get("invoice_number") or "").strip()

    reb = wf.get_reimbursement(request_id)
    if not reb:
        # AI 校验阶段未预建报销单，提交审批时按提交内容创建
        invoices = wf.get_invoices_for_request(request_id)
        if not invoices:
            if not invoice_number:
                # 既无发票记录、又无手填发票号 → 视为非法/不存在的单号
                return jsonify({"error": "报销单不存在"}), 404
            # 停用态：用户手填发票号 → 建单并创建发票记录
            try:
                wf.create_reimbursement_on_submit(
                    request_id,
                    employee_id=session.get("account", "unknown"),
                    apply_amount=data.get("apply_amount"),
                    apply_date=data.get("apply_date"),
                    expense_category=data.get("expense_category"),
                    reason=data.get("reason"),
                )
            except ValueError as e:
                return jsonify({"error": str(e)}), 400
            if check_duplicate_invoice(invoice_number, exclude_request_id=request_id):
                return jsonify({"error": f"发票号码 {invoice_number} 已存在重复报销记录，请核对"}), 409
            save_invoice(
                {
                    "发票号码": invoice_number,
                    "发票金额": data.get("apply_amount") or 0,
                    "开票日期": data.get("invoice_date") or "",
                },
                request_id,
            )
        else:
            # 已有发票记录（AI 态预警单等）→ 直接建单
            try:
                wf.create_reimbursement_on_submit(
                    request_id,
                    employee_id=session.get("account", "unknown"),
                    apply_amount=data.get("apply_amount"),
                    apply_date=data.get("apply_date"),
                    expense_category=data.get("expense_category"),
                    reason=data.get("reason"),
                )
            except ValueError as e:
                return jsonify({"error": str(e)}), 400
            if invoice_number:
                if check_duplicate_invoice(invoice_number, exclude_request_id=request_id):
                    return jsonify({"error": f"发票号码 {invoice_number} 已存在重复报销记录，请核对"}), 409
                update_invoice_fields(
                    request_id,
                    invoice_number=invoice_number,
                    invoice_date=data.get("invoice_date") or None,
                )
    else:
        if role == "employee" and reb.employee_id != session["account"]:
            return jsonify({"error": "无权修改此报销单"}), 403
        # 停用态补录 / 修正发票号
        if invoice_number:
            if check_duplicate_invoice(invoice_number, exclude_request_id=request_id):
                return jsonify({"error": f"发票号码 {invoice_number} 已存在重复报销记录，请核对"}), 409
            update_invoice_fields(
                request_id,
                invoice_number=invoice_number,
                invoice_date=data.get("invoice_date") or None,
            )

    # 更新报销单字段（仅「待审批」可改，workflow 层强制）
    try:
        updated = wf.update_reimbursement(
            request_id,
            apply_amount=data.get("apply_amount"),
            apply_date=data.get("apply_date"),
            expense_category=data.get("expense_category"),
            reason=data.get("reason"),
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(wf.get_detail(request_id))


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
@app.route("/api/deepseek/status")
def api_deepseek_status():
    """DeepSeek 大模型启用状态（供前端在提交校验前探测，避免无谓的流水线动画）。

    返回 {"enabled": true/false}，无需管理员权限，登录即可访问。
    """
    err = _require_login()
    if err:
        return err
    return jsonify({"enabled": bool(get_deepseek_enabled())})


@app.route("/api/admin/config", methods=["GET", "POST"])
def api_admin_config():
    """系统配置：GET 返回 schema + 当前值；POST 保存配置（落库 + 写审计）。"""
    err = _require_role("admin")
    if err:
        return err
    if request.method == "GET":
        return jsonify(
            {
                "schema": admin_store.get_config_schema(),
                "config": admin_store.get_system_config(),
            }
        )
    data = request.get_json(silent=True) or {}
    items = data.get("items", {})
    merged = admin_store.save_system_config(
        items,
        operator=session.get("name", "系统管理员"),
        role="系统管理员",
        ip=request.remote_addr,
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
        role="系统管理员",
        ip=request.remote_addr,
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
    return jsonify(
        {
            "overview": admin_store.get_usage_overview(),
            "daily": admin_store.get_usage_daily(),
            "by_type": admin_store.get_usage_by_type(),
            "records": admin_store.list_usage_records(date_filter, type_filter, status_filter),
        }
    )


@app.errorhandler(413)
def request_entity_too_large(_e):
    return jsonify({"status": "错误", "summary": "文件大小超过 10MB 限制"}), 413


@app.route("/result")
def result_page():
    """独立校验结果页（由 upload.js 在提交后读取 URL hash 数据渲染）。

    通过 render_template 渲染以便注入 app_version，使 /static 引用带版本号缓存破坏。
    """
    return render_template("result.html")
