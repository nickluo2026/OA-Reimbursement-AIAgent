"""分类限额校验工具封装

方案A 下，分类限额校验由 ``anomaly_node`` 在并行分支中通过本模块暴露的
``classify_and_check_limit`` 调用，落库与定级统一在 ``graph.post_check_node`` 完成。
保留 ``save_ai_check_result`` / ``update_ai_status`` 导入，以便测试通过
``patch.object(classify_node, ...)`` 注入桩。
"""

from __future__ import annotations

import logging

from ...tools.tool_classify_limit import classify_and_check_limit
from ...utils.db_store import (  # noqa: F401  — 保留以便测试 patch.object
    save_ai_check_result,
    update_ai_status,
)

logger = logging.getLogger(__name__)
