"""编排节点：StateGraph 各节点实现

每个节点为 ``def node(state) -> dict``，返回状态更新（部分字段），
由 LangGraph 自动合并入全局状态。节点内部封装工具调用与持久化副作用。

注：本包**不在 __init__ 中重导出节点函数**，以避免与同名子模块属性冲突——
若执行 ``from .classify_node import classify_node``，会把 ``nodes.classify_node``
属性从子模块覆盖为函数，导致 ``mock.patch('...nodes.classify_node.xxx')``
解析到函数而非模块、抛出 AttributeError。

各调用方直接从子模块导入即可，例如::

    from skill.orchestrator.nodes.classify_node import classify_node
"""

# 刻意保持为空：不重导出节点函数，保留子模块属性（见上方说明）。
