"""数据库层：SQLAlchemy ORM 模型与会话管理

基于设计文档 §6 ER 模型，定义 5 张核心表：
    - employee        : 员工信息
    - reimbursement   : 报销单主表
    - invoice_record  : 发票数据
    - approval_record : 审批记录
    - ai_check_result : AI 校验结果
"""

from __future__ import annotations

import os
from datetime import date, datetime
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
_SessionFactory = sessionmaker(bind=_engine)


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


# ═══════════════════════════════════════════════
# ORM 模型
# ═══════════════════════════════════════════════


class Employee(Base):
    """员工信息表"""
    __tablename__ = "employee"

    employee_id = Column(String(32), primary_key=True, comment="员工工号")
    name = Column(String(64), nullable=False, comment="姓名")
    department = Column(String(128), comment="部门")
    role = Column(String(32), default="员工", comment="角色: 员工/领导/财务")
    created_at = Column(DateTime, default=datetime.utcnow)

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
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

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
    created_at = Column(DateTime, default=datetime.utcnow)

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
    created_at = Column(DateTime, default=datetime.utcnow)

    def __repr__(self) -> str:
        return f"<InvoiceHistory #{self.invoice_number}>"


class ApprovalRecord(Base):
    """审批记录表"""
    __tablename__ = "approval_record"

    id = Column(Integer, primary_key=True, autoincrement=True)
    request_id = Column(String(64), nullable=False, index=True, comment="关联报销单")
    approver_id = Column(String(32), comment="审批人工号")
    approver_name = Column(String(64), comment="审批人姓名")
    approval_node = Column(String(32), comment="审批节点: 直属领导/部门总监/VP/CEO/财务")
    action = Column(String(16), comment="动作: 通过/驳回/转审")
    comment = Column(String(512), comment="审批意见")
    action_time = Column(DateTime, default=datetime.utcnow)

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
    check_time = Column(DateTime, default=datetime.utcnow)

    def __repr__(self) -> str:
        return f"<AICheckResult {self.request_id} {self.check_type}>"


def init_db() -> None:
    """初始化数据库：创建所有表"""
    Base.metadata.create_all(_engine)
