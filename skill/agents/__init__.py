"""Agent 抽象层

提供 Agent 基类与元信息定义，面向 V1.5+ 多 Agent 扩展。
对应 design.md §16.5。

导入具体 Agent 子类以触发 ``@register_agent`` 注册：
    - ItineraryAgent：行程单 Agent（V1.5 已接入 StateGraph）
"""

from .base_agent import AgentMeta, BaseAgent

# 导入具体 Agent 触发 @register_agent 注册（必须在 graph 构建前完成）
from .itinerary_agent import ItineraryAgent

__all__ = ["BaseAgent", "AgentMeta", "ItineraryAgent"]
