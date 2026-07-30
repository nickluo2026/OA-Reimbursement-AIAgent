"""流水线真实进度事件（Progress Events）。

背景
────
`/upload` 是同步阻塞接口：整条报销流水线跑完（实测约 40s+）才返回 JSON，
而前端流水线动画此前由固定定时器驱动（每步 0.9~1.2s），导致「动画早已全部打勾、
实际智能体还在跑」的严重不同步。

本模块提供一个**上下文级**的进度回调通道：
- 编排层/节点/工具在关键边界调用 :func:`emit_progress` 发出事件；
- Web 层通过 :func:`progress_scope` 注册回调，把事件推入 SSE 通道实时下发前端；
- 未注册回调时（CLI、单测）所有调用均为**零副作用空操作**，不影响既有行为。

为什么用 ContextVar 而不是显式参数
──────────────────────────────────
本项目已有 ``skill.utils.structured_log.set_request_id`` 同样基于 ContextVar，
并被深层工具读取，说明 LangGraph 节点调用链中 ContextVar 可正常传播。
沿用同一机制可避免在 graph → node → tool 的每一层塞 ``on_progress`` 参数。

注意：``ThreadPoolExecutor`` 默认**不会**复制上下文，需要在 ``submit`` 时用
``contextvars.copy_context().run`` 包裹（本项目 anomaly_node 已如此处理）。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

logger = logging.getLogger(__name__)

# 进度回调签名：callback(event: dict) -> None
ProgressCallback = Callable[[dict[str, Any]], None]

_progress_cb: ContextVar[ProgressCallback | None] = ContextVar("progress_cb", default=None)

# ── 事件状态常量 ──
STATUS_START = "start"
STATUS_DONE = "done"
STATUS_SKIP = "skip"
STATUS_FAIL = "fail"
STATUS_INFO = "info"  # 步骤内部子进度（不改变步骤状态，仅更新描述文案）

# ── 步骤 ID（需与前端 upload.js / mobile.js 的 step.key 严格一致）──
STEP_ROUTE = "route"
STEP_OCR = "ocr"
STEP_ANOMALY = "anomaly"
STEP_CLASSIFY = "classify"
STEP_ITINERARY_OCR = "itinerary_ocr"
STEP_ITINERARY_ANOMALY = "itinerary_anomaly"
STEP_ITINERARY_VERIFY = "itinerary_verify"

# 终止哨兵：Web 层在流水线结束后补发，前端收到即关闭 EventSource
STEP_DONE_SENTINEL = "__done__"


def set_progress_callback(cb: ProgressCallback | None):
    """设置当前上下文的进度回调，返回可用于还原的 token。"""
    return _progress_cb.set(cb)


def reset_progress_callback(token) -> None:
    """还原 :func:`set_progress_callback` 之前的回调。"""
    try:
        _progress_cb.reset(token)
    except (ValueError, RuntimeError):  # 跨上下文 reset，忽略
        pass


def get_progress_callback() -> ProgressCallback | None:
    """获取当前上下文的进度回调（无则返回 None）。"""
    return _progress_cb.get()


@contextmanager
def progress_scope(cb: ProgressCallback | None) -> Iterator[None]:
    """在 with 作用域内启用进度回调，退出时自动还原。"""
    token = set_progress_callback(cb)
    try:
        yield
    finally:
        reset_progress_callback(token)


def emit_progress(step: str, status: str, message: str = "", **extra: Any) -> None:
    """发出一个进度事件。

    Args:
        step: 步骤 ID，见本模块 ``STEP_*`` 常量。
        status: ``start`` / ``done`` / ``skip`` / ``fail`` / ``info``。
        message: 展示给用户的简短描述（如「本地 OCR 识别中…」）。
        **extra: 附加字段，会原样并入事件（需 JSON 可序列化）。

    未注册回调时直接返回；回调内部异常会被吞掉并记 debug 日志，
    确保**进度上报永远不会影响主流程**。
    """
    cb = _progress_cb.get()
    if cb is None:
        return
    event: dict[str, Any] = {
        "step": step,
        "status": status,
        "message": message,
        "ts": round(time.time(), 3),
    }
    if extra:
        event.update(extra)
    try:
        cb(event)
    except Exception:  # noqa: BLE001 — 进度上报失败不得中断业务流程
        logger.debug("进度事件上报失败 step=%s status=%s", step, status, exc_info=True)


@contextmanager
def progress_step(step: str, start_message: str = "", done_message: str = "") -> Iterator[None]:
    """步骤级上下文管理器：进入发 ``start``，正常退出发 ``done``，异常发 ``fail``。"""
    emit_progress(step, STATUS_START, start_message)
    try:
        yield
    except Exception as e:  # noqa: BLE001
        emit_progress(step, STATUS_FAIL, f"执行异常：{e}")
        raise
    else:
        emit_progress(step, STATUS_DONE, done_message)
