"""Agent 抽象基类

对应 design.md §16.5。所有 Agent 必须继承并实现 ``meta`` 与 ``run``。
V1.4 发票流程以节点形式直接接入 StateGraph，暂不强制继承本基类；
V1.5 起新增票据类型 Agent 将通过注册中心 + 本基类插件化扩展。

注：``run`` 为同步方法，与当前 StateGraph 的 ``app.invoke()`` 同步执行模式一致。
未来若迁移至异步图（``app.ainvoke()``），可在此添加 ``async def run_async``。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class AgentMeta:
    """Agent 元信息"""

    name: str              # Agent 唯一标识
    ticket_type: str       # 支持的票据类型
    description: str       # 功能描述
    input_schema: type     # 入参 Schema
    output_schema: type    # 出参 Schema


class BaseAgent(ABC):
    """Agent 抽象基类 — 所有 Agent 必须继承并实现 run"""

    @abstractmethod
    def meta(self) -> AgentMeta:
        ...

    @abstractmethod
    def run(self, state: dict[str, Any]) -> dict[str, Any]:
        """执行 Agent 逻辑，返回状态更新（同步，与 StateGraph invoke 一致）"""
        ...
