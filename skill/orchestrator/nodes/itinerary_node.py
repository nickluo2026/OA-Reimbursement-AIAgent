"""行程单 Agent 节点

V1.5 重构：节点逻辑已迁移至 ``skill/agents/itinerary_agent.py`` 的
``ItineraryAgent``（通过 ``@register_agent`` 注册）。
本节点作为 StateGraph 节点入口，委托注册中心 Agent 执行，保持图编排与业务解耦。

工具与持久化函数的导入位于 ``itinerary_agent.py`` 模块，
测试 ``mock.patch`` 路径为 ``skill.agents.itinerary_agent.<tool>``。
"""

from __future__ import annotations

import logging
from typing import Any

from ..state import ReimbursementState

logger = logging.getLogger(__name__)


def itinerary_node(state: ReimbursementState) -> dict[str, Any]:
    """行程单节点：委托注册中心的 ItineraryAgent 执行

    Agent 注册在 ``skill/agents/`` 包导入时自动完成（由 ``graph.py`` 触发）。
    此处通过 ``get_agent`` 获取已注册实例并调用 ``run``。
    """
    from ..registry import get_agent

    agent = get_agent("itinerary")
    return agent.run(dict(state))
