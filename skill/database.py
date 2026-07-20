"""数据库层：SQLAlchemy ORM 模型与会话管理

基于设计文档 §6 ER 模型，定义 6 张核心表：
    - employee         : 员工信息
    - reimbursement    : 报销单主表
    - invoice_record   : 发票数据
    - invoice_history  : 已报销发票历史（防重）
    - approval_record  : 审批记录
    - ai_check_result  : AI 校验结果
"""

from __future__ import annotations

import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Generator

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


# ── 数据库路径 ──
DB_PATH = os.getenv("OA_DB_PATH", str(Path(__file__).resolve().parent.parent / "oa_agent.db"))

# ── 引擎与会话工厂 ──
_engine = create_engine(
    f"sqlite:///{DB_PATH}",
    echo=False,
    connect_args={"check_same_thread": False},
    pool_pre_ping=True,
)
# expire_on_commit=False：提交/关闭会话后，已加载的列属性仍可访问，
# 便于在会话外（如 workflow.serialize）读取报销单字段。
_SessionFactory = sessionmaker(bind=_engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_session() -> Session:
    """获取一个新的数据库会话（调用方负责关闭）"""
    return _SessionFactory()


def get_all_tables() -> list[str]:
    """返回当前数据库中所有表名"""
    from sqlalchemy import inspect
    inspector = inspect(_engine)
    return inspector.get_table_names()


def utcnow() -> datetime:
    """返回当前 UTC 时间（naive datetime，保持与历史数据一致）。

    替代 Python 3.12+ 已弃用的 ``utcnow()``。
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ═══════════════════════════════════════════════
# ORM 模型
# ═══════════════════════════════════════════════


class Employee(Base):
    """员工信息表"""
    __tablename__ = "employee"

    employee_id = Column(String(32), primary_key=True, comment="员工工号")
    name = Column(String(64), nullable=False, comment="姓名")
    department = Column(String(128), comment="部门")
    role = Column(String(32), default="员工", comment="角色: 员工/审批领导/财务复核/出纳打款/系统管理员")
    created_at = Column(DateTime, default=utcnow)

    def __repr__(self) -> str:
        return f"<Employee {self.employee_id} {self.name}>"


class Reimbursement(Base):
    """报销单主表"""
    __tablename__ = "reimbursement"

    request_id = Column(String(64), primary_key=True, comment="报销单号")
    employee_id = Column(String(32), nullable=False, index=True, comment="申请人")
    apply_amount = Column(Float, nullable=False, comment="申请金额")
    apply_date = Column(Date, nullable=False, comment="申请日期")
    reason = Column(String(256), comment="报销事由")
    expense_category = Column(String(32), comment="费用分类")
    ai_status = Column(String(16), default="待校验", index=True,
                       comment="AI校验状态: 通过/预警/拦截/错误")
    workflow_status = Column(String(16), default="待审批", index=True,
                              comment="工作流状态: 待审批/审批中/已通过/已驳回/已归档")
    remark = Column(String(256), comment="备注")
    archived_by = Column(String(32), comment="归档人(财务复核岗工号)")
    paid_by = Column(String(32), comment="打款人(出纳岗工号)")
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    def __repr__(self) -> str:
        return f"<Reimbursement {self.request_id} [{self.ai_status}]>"


class InvoiceRecord(Base):
    """发票数据表"""
    __tablename__ = "invoice_record"

    id = Column(Integer, primary_key=True, autoincrement=True)
    request_id = Column(String(64), nullable=False, index=True, comment="关联报销单")
    invoice_type = Column(String(32), comment="发票类型")
    invoice_code = Column(String(32), comment="发票代码")
    invoice_number = Column(String(32), nullable=False, index=True, comment="发票号码")
    invoice_date = Column(Date, comment="开票日期")
    invoice_amount = Column(Float, comment="发票金额")
    seller_name = Column(String(256), comment="销售方名称")
    seller_tax_id = Column(String(64), comment="销售方税号")
    buyer_name = Column(String(256), comment="购买方名称")
    buyer_tax_id = Column(String(64), comment="购买方税号")
    tax_amount = Column(Float, comment="税额")
    file_path = Column(String(512), comment="票据文件路径(加密)")
    ocr_raw = Column(JSON, comment="OCR 原始结果 JSON")
    created_at = Column(DateTime, default=utcnow)

    def __repr__(self) -> str:
        return f"<InvoiceRecord #{self.invoice_number}>"


class InvoiceHistory(Base):
    """已报销发票历史（防重）"""
    __tablename__ = "invoice_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    invoice_number = Column(String(32), nullable=False, unique=True, index=True,
                             comment="发票号码(唯一索引)")
    request_id = Column(String(64), comment="关联报销单号")
    reimbursed_date = Column(Date, comment="报销日期")
    amount = Column(Float, comment="报销金额")
    created_at = Column(DateTime, default=utcnow)

    def __repr__(self) -> str:
        return f"<InvoiceHistory #{self.invoice_number}>"


class ApprovalRecord(Base):
    """审批记录表"""
    __tablename__ = "approval_record"

    id = Column(Integer, primary_key=True, autoincrement=True)
    request_id = Column(String(64), nullable=False, index=True, comment="关联报销单")
    approver_id = Column(String(32), comment="审批人工号")
    approver_name = Column(String(64), comment="审批人姓名")
    approval_node = Column(String(32), comment="审批节点: 直属领导/部门总监/VP/CEO/财务复核/出纳打款")
    action = Column(String(16), comment="动作: 通过/驳回/转审")
    comment = Column(String(512), comment="审批意见")
    action_time = Column(DateTime, default=utcnow)

    def __repr__(self) -> str:
        return f"<ApprovalRecord {self.request_id} {self.action}>"


class AICheckResult(Base):
    """AI 校验结果表"""
    __tablename__ = "ai_check_result"

    id = Column(Integer, primary_key=True, autoincrement=True)
    request_id = Column(String(64), nullable=False, index=True, comment="关联报销单")
    check_type = Column(String(32), comment="检查类型: OCR/异常检测/分类限额")
    status = Column(String(16), comment="结论: 通过/预警/拦截/错误")
    detail = Column(JSON, comment="检查详情 JSON")
    check_time = Column(DateTime, default=utcnow)

    def __repr__(self) -> str:
        return f"<AICheckResult {self.request_id} {self.check_type}>"


class SystemConfig(Base):
    """系统配置表（系统管理员维护的费用限额 / 异常规则 / 审批权限）

    以 key 为主键，value 存 JSON。配置整体以 key="system" 的单条记录保存。
    对应 design.md §17.4.4 / prototype.html 系统配置 Tab。
    """

    __tablename__ = "system_config"

    key = Column(String(64), primary_key=True, comment="配置键")
    value = Column(JSON, comment="配置值（JSON）")
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    def __repr__(self) -> str:
        return f"<SystemConfig {self.key}>"


class AuditLog(Base):
    """审计日志表（仅追加，不可删）

    记录所有用户操作、AI 校验、系统配置变更，支持追溯与合规审查。
    对应 design.md §19 / prototype.html 审计日志 Tab。
    """

    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    time = Column(DateTime, default=utcnow, comment="操作时间")
    user = Column(String(64), comment="操作人")
    role = Column(String(32), comment="角色")
    action = Column(String(32), comment="操作类型")
    target = Column(String(512), comment="操作对象")
    request_id = Column(String(64), default="", comment="报销单号")
    result = Column(String(16), default="成功", comment="结果: 成功/失败")
    ip = Column(String(64), comment="来源 IP")
    created_at = Column(DateTime, default=utcnow)

    def __repr__(self) -> str:
        return f"<AuditLog {self.id} {self.action} {self.user}>"


class ApiUsage(Base):
    """DeepSeek API 用量统计表

    记录每次 DeepSeek / Vision API 调用的 token 消耗、延迟与状态，
    用于系统管理员「用量统计」面板。
    对应 design.md §18 / prototype.html 用量统计 Tab。
    """

    __tablename__ = "api_usage"

    id = Column(Integer, primary_key=True, autoincrement=True)
    time = Column(DateTime, default=utcnow, comment="调用时间")
    request_id = Column(String(32), comment="关联请求 ID")
    call_type = Column(String(32), comment="调用类型")
    model = Column(String(64), comment="模型")
    prompt_tokens = Column(Integer, default=0, comment="输入 token")
    completion_tokens = Column(Integer, default=0, comment="输出 token")
    latency_ms = Column(Integer, default=0, comment="延迟(ms)")
    status = Column(String(16), default="成功", comment="状态: 成功/失败")
    created_at = Column(DateTime, default=utcnow)

    def __repr__(self) -> str:
        return f"<ApiUsage {self.id} {self.call_type} {self.status}>"


def init_db() -> None:
    """初始化数据库：创建所有表（含系统配置 / 审计日志 / 用量统计）

    先 ``dispose`` 清除连接池中可能持有过时 schema 缓存的连接，
    确保 ``checkfirst`` 能准确判断表是否已存在。
    """
    _engine.dispose()
    Base.metadata.create_all(_engine, checkfirst=True)

    # 迁移：为已有 audit_log 表补充 request_id 列（报销单号）
    from sqlalchemy import inspect, text
    _insp = inspect(_engine)
    if "audit_log" in _insp.get_table_names():
        cols = [c["name"] for c in _insp.get_columns("audit_log")]
        if "request_id" not in cols:
            with _engine.begin() as conn:
                conn.execute(text("ALTER TABLE audit_log ADD COLUMN request_id VARCHAR(64) DEFAULT ''"))

    # 迁移：为已有 reimbursement 表补充财务职责分离字段（归档人 / 打款人）
    if "reimbursement" in _insp.get_table_names():
        r_cols = [c["name"] for c in _insp.get_columns("reimbursement")]
        for col in ("archived_by", "paid_by"):
            if col not in r_cols:
                with _engine.begin() as conn:
                    conn.execute(text(f"ALTER TABLE reimbursement ADD COLUMN {col} VARCHAR(32)"))
