"""结构化日志模块：支持 JSON 格式日志和 request_id 追踪

符合宪章 §2.3「审计可追溯」——每次 AI 调用需记录完整日志。
"""

from __future__ import annotations

import logging
import os
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone

import structlog

# ── request_id 上下文变量 ──
_request_id_var: ContextVar[str] = ContextVar("request_id", default="unknown")


def set_request_id(rid: str | None = None) -> str:
    """设置当前请求的 request_id，并返回该 ID"""
    rid = rid or uuid.uuid4().hex[:12]
    _request_id_var.set(rid)
    return rid


def get_request_id() -> str:
    """获取当前请求的 request_id"""
    return _request_id_var.get()


# ═══════════════════════════════════════════════
# structlog 配置
# ═══════════════════════════════════════════════


def _add_request_id(
    logger: logging.Logger, method_name: str, event_dict: structlog.types.EventDict
) -> structlog.types.EventDict:
    """为每条日志注入 request_id"""
    event_dict["request_id"] = get_request_id()
    return event_dict


def _add_timestamp(
    logger: logging.Logger, method_name: str, event_dict: structlog.types.EventDict
) -> structlog.types.EventDict:
    """注入 ISO 8601 时间戳"""
    event_dict["timestamp"] = datetime.now(timezone.utc).isoformat()
    return event_dict


structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        _add_request_id,
        _add_timestamp,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        (
            structlog.dev.ConsoleRenderer()
            if os.environ.get("OA_ENV", "production") == "development"
            else structlog.processors.JSONRenderer()
        ),
    ],
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """获取结构化日志实例"""
    return structlog.get_logger(name or __name__)


# ── 兼容标准 logging 的快捷方式 ──
audit_log = get_logger("oa_agent.audit")
