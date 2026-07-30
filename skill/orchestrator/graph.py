"""工作流定义（StateGraph）

对应 design.md §16.4。构建并编译报销校验 StateGraph：
票据类型路由 → OCR → 异常检测 → (拦截/分类/小额免审) → 结束。

相对 design.md §16.4 骨架，本实现补充 ``route_after_ocr`` 条件边处理
OCR 失败提前结束（与原 ``agent.py`` 功能等价所必需）。
"""

from __future__ import annotations

import logging
from typing import Any

from langgraph.graph import END, StateGraph

try:  # langgraph 新版（>=0.2）提供 START 常量，入口点改用 add_conditional_edges
    from langgraph.graph import START

    _HAS_START = True
except ImportError:  # 旧版无 START，回退到 set_conditional_entry_point
    START = None  # type: ignore[assignment]
    _HAS_START = False

from ..agents import ItineraryAgent  # noqa: F401 — 触发 @register_agent 注册
from ..config import SMALL_AMOUNT_THRESHOLD
from .nodes.anomaly_node import anomaly_node
from .nodes.itinerary_node import itinerary_node
from .nodes.ocr_node import ocr_node
from .nodes.skip_node import skip_node
from .state import CheckStatus, ReimbursementState
from ..utils.db_store import save_ai_check_result, update_ai_status

logger = logging.getLogger(__name__)


def route_by_ticket_type(state: ReimbursementState) -> str:
    """条件边：按票据类型路由到对应 Agent"""
    return state.get("ticket_type", "发票")


def route_after_ocr(state: ReimbursementState) -> str:
    """条件边：OCR 失败则提前结束；发票金额 > 100 时触发异常检测 ‖ 分类限额 并行。

    对应方案A：金额较大时才需要分类限额校验，此时异常检测与分类限额彼此独立，
    可在 OCR 之后并行执行以缩短关键路径（小额单无需分类，走 anomaly_only 串行分支）。
    """
    if state.get("final_status") == CheckStatus.ERROR:
        return "error"
    invoice_amount = (state.get("ocr_result") or {}).get("发票金额", 0)
    if isinstance(invoice_amount, (int, float)) and invoice_amount > SMALL_AMOUNT_THRESHOLD:
        return "parallel"  # 触发 anomaly ‖ classify 并行
    return "anomaly_only"


def post_check_node(state: ReimbursementState) -> dict[str, Any]:
    """合并节点（方案A）：异常检测 ‖ 分类限额 并行完成后统一落库与定级。

    - 异常拦截优先：保持 BLOCK，且不再写入「分类限额」记录（拦截即结束、跳过查验）。
    - 未拦截：据分类限额结论写入「分类限额」记录并定级（预警/通过）。
    """
    anomaly_result = state.get("anomaly_result") or {}
    classify_result = state.get("classify_result") or {}
    request_id = state.get("request_id")

    # 1) 异常拦截优先
    if anomaly_result.get("总体结论") == "拦截":
        return {"final_status": CheckStatus.BLOCK}

    # 2) 未拦截：基于分类限额结论定级 + 落库
    total_conclusion = anomaly_result.get("总体结论", "通过")
    is_over_limit = classify_result.get("是否超限", False)
    if is_over_limit:
        summary = (
            f"费用超限: {classify_result.get('校验结果', '')}。"
            f"异常检查结论: {total_conclusion}。"
        )
        if request_id:
            try:
                update_ai_status(request_id, "预警")
                save_ai_check_result(request_id, "分类限额", "预警", classify_result)
            except Exception as exc:  # 落库异常不应中断主流程
                logger.warning("post_check 落库异常（非致命）: %s", exc)
        return {"final_status": CheckStatus.WARNING, "summary": summary}

    summary = (
        f"校验通过。费用分类: {classify_result.get('费用分类', '未知')}，"
        f"金额 {classify_result.get('发票金额', 0)} 元 ≤ 限额 "
        f"{classify_result.get('分类限额', 0)} 元。"
        f"异常检查结论: {total_conclusion}。"
    )
    if request_id:
        try:
            update_ai_status(request_id, "通过")
            save_ai_check_result(request_id, "分类限额", "通过", classify_result)
        except Exception as exc:
            logger.warning("post_check 落库异常（非致命）: %s", exc)
    return {"summary": summary}


def route_post_check(state: ReimbursementState) -> str:
    """合并后路由：异常拦截则结束，否则结束（发票真伪查验已移除）。"""
    if (state.get("anomaly_result") or {}).get("总体结论") == "拦截":
        return "block"
    return "proceed"


def route_after_anomaly(state: ReimbursementState) -> str:
    """条件边：异常检测后路由（与 §16.2 架构图一致）

    - 拦截 → 提前结束
    - 通过且金额 > 100 → 分类限额校验
    - 通过且金额 ≤ 100 → 小额免审
    """
    if state.get("final_status") == CheckStatus.BLOCK:
        return "block"
    invoice_amount = (state.get("ocr_result") or {}).get("发票金额", 0)
    if isinstance(invoice_amount, (int, float)) and invoice_amount > SMALL_AMOUNT_THRESHOLD:
        return "classify"
    return "skip"


def build_reimbursement_graph():
    """构建报销校验工作流并编译"""
    workflow: StateGraph = StateGraph(ReimbursementState)

    # —— 注册节点 ——
    workflow.add_node("ocr", ocr_node)
    workflow.add_node("anomaly", anomaly_node)
    workflow.add_node("post_check", post_check_node)
    workflow.add_node("skip", skip_node)
    workflow.add_node("itinerary", itinerary_node)

    # —— 设置入口：按票据类型路由 ——
    _ticket_routing = {
        "发票": "ocr",
        "行程单": "itinerary",
        # 新增票据类型只需在此扩展路由 + 注册新节点
    }
    if _HAS_START:
        # langgraph 新版：用 add_conditional_edges(START, ...)
        workflow.add_conditional_edges(START, route_by_ticket_type, _ticket_routing)
    else:
        # langgraph 旧版：set_conditional_entry_point
        workflow.set_conditional_entry_point(route_by_ticket_type, _ticket_routing)

    # —— 发票分支边 ——
    # OCR 失败→END / 成功→anomaly（金额>100 时 anomaly 节点内部并行跑分类限额）
    workflow.add_conditional_edges(
        "ocr",
        route_after_ocr,
        {"error": END, "parallel": "anomaly", "anomaly_only": "anomaly"},
    )
    # 异常检测后：拦截→END / 金额>100→post_check(合并) / 小额免审→skip
    workflow.add_conditional_edges(
        "anomaly",
        route_after_anomaly,
        {
            "block": END,
            "classify": "post_check",
            "skip": "skip",
        },
    )
    # 合并节点：拦截→END / 否则→END（发票真伪查验步骤已移除）
    workflow.add_conditional_edges(
        "post_check",
        route_post_check,
        {"block": END, "proceed": END},
    )
    workflow.add_edge("skip", END)

    # —— 行程单分支直接结束 ——
    workflow.add_edge("itinerary", END)

    return workflow.compile()


def run_graph(initial_state: dict[str, Any]) -> dict[str, Any]:
    """构建并执行报销校验工作流，返回最终状态"""
    app = build_reimbursement_graph()
    return app.invoke(initial_state)
