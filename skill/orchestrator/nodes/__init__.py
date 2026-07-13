"""编排节点：StateGraph 各节点实现

每个节点为 ``def node(state) -> dict``，返回状态更新（部分字段），
由 LangGraph 自动合并入全局状态。节点内部封装工具调用与持久化副作用。
"""

from .ocr_node import ocr_node
from .anomaly_node import anomaly_node
from .classify_node import classify_node
from .skip_node import skip_node
from .verify_node import verify_node
from .itinerary_node import itinerary_node

__all__ = [
    "ocr_node",
    "anomaly_node",
    "classify_node",
    "skip_node",
    "verify_node",
    "itinerary_node",
]
