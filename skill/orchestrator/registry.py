"""Agent 注册中心

对应 design.md §16.5 / ADR-008。通过注册机制实现 Agent 插件化扩展，
V1.4 仅提供基础设施，V1.5 起行程单 Agent 等将通过注册中心接入编排。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..agents.base_agent import AgentMeta, BaseAgent

_AGENT_REGISTRY: dict[str, BaseAgent] = {}


def register_agent(agent):
    """注册 Agent 到注册中心。

    既可注册 Agent 实例，也可作为类装饰器使用（装饰类时会实例化后注册，
    并返回原类以保持装饰器语义）。
    """
    if isinstance(agent, type):  # 装饰类：实例化后注册，返回原类
        instance = agent()
        _do_register(instance)
        return agent
    _do_register(agent)
    return agent


def _do_register(agent: BaseAgent) -> None:
    info = agent.meta()
    if info.name in _AGENT_REGISTRY:
        raise ValueError(f"Agent 已注册: {info.name}")
    _AGENT_REGISTRY[info.name] = agent


def get_agent(name: str) -> BaseAgent:
    """按名称获取 Agent"""
    if name not in _AGENT_REGISTRY:
        raise KeyError(f"未注册的 Agent: {name}，已注册: {list(_AGENT_REGISTRY)}")
    return _AGENT_REGISTRY[name]


def list_agents() -> list[AgentMeta]:
    """列出所有已注册 Agent 元信息"""
    return [a.meta() for a in _AGENT_REGISTRY.values()]


def clear_registry() -> None:
    """清空注册中心（测试用）"""
    _AGENT_REGISTRY.clear()
