"""工作流定义（StateGraph）

对应 design.md §16.4。构建并编译报销校验 StateGraph：
票据类型路由 → OCR → 异常检测 → (拦截/分类/小额免审) → 查验 → 结束。

相对 design.md §16.4 骨架，本实现补充 ``route_after_ocr`` 条件边处理
OCR 失败提前结束（与原 ``agent.py`` 功能等价所必需）。
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, StateGraph

try:  # langgraph 新版（>=0.2）提供 START 常量，入口点改用 add_conditional_edges
    from langgraph.graph import START
    _HAS_START = True
except ImportError:  # 旧版无 START，回退到 set_conditional_entry_point
    START = None  # type: ignore[assignment]
    _HAS_START = False

from ..config import SMALL_AMOUNT_THRESHOLD
from .nodes.anomaly_node import anomaly_node
from .nodes.classify_node import classify_node
from .nodes.itinerary_node import itinerary_node
from .nodes.ocr_node import ocr_node
from .nodes.skip_node import skip_node
from .nodes.verify_node import verify_node
from .state import CheckStatus, ReimbursementState


def route_by_ticket_type(state: ReimbursementState) -> str:
    """条件边：按票据类型路由到对应 Agent"""
    return state.get("ticket_type", "发票")


def route_after_ocr(state: ReimbursementState) -> str:
    """条件边：OCR 失败则提前结束"""
    if state.get("final_status") == CheckStatus.ERROR:
        return "error"
    return "ok"


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
    workflow.add_node("classify", classify_node)
    workflow.add_node("skip", skip_node)
    workflow.add_node("itinerary", itinerary_node)
    workflow.add_node("verify", verify_node)

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
    # OCR 失败→END / 成功→anomaly
    workflow.add_conditional_edges(
        "ocr",
        route_after_ocr,
        {"error": END, "ok": "anomaly"},
    )
    # 异常检测后：拦截→END / 金额>100→classify / 小额免审→skip
    workflow.add_conditional_edges(
        "anomaly",
        route_after_anomaly,
        {
            "block": END,
            "classify": "classify",
            "skip": "skip",
        },
    )
    workflow.add_edge("classify", "verify")
    workflow.add_edge("skip", "verify")
    workflow.add_edge("verify", END)

    # —— 行程单分支直接结束 ——
    workflow.add_edge("itinerary", END)

    return workflow.compile()


def run_graph(initial_state: dict[str, Any]) -> dict[str, Any]:
    """构建并执行报销校验工作流，返回最终状态"""
    app = build_reimbursement_graph()
    return app.invoke(initial_state)
