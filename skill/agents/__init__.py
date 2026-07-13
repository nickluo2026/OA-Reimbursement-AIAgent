"""Agent 抽象层

提供 Agent 基类与元信息定义，面向 V1.5+ 多 Agent 扩展。
对应 design.md §16.5。
"""

from .base_agent import AgentMeta, BaseAgent

__all__ = ["BaseAgent", "AgentMeta"]
