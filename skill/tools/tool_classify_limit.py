"""功能2：费用分类与限额校验

流程：DeepSeek Function Call 分类 → 本地规则引擎校验限额
仅对发票金额超过 100 元的发票执行（小额免审）。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from ..config import SMALL_AMOUNT_THRESHOLD, get_category_limits
from ..schemas.classify_schema import CLASSIFY_LIMIT_TOOL
from ..utils.http_client import call_deepseek_function

logger = logging.getLogger(__name__)

# 「其他」分类的兜底限额（仅在 YAML 未配置「其他」时使用，避免魔法数字散落）
DEFAULT_OTHER_LIMIT = 200.0

SYSTEM_PROMPT = (
    "你是费用分类助手。\n"
    "\n"
    "工作流程：\n"
    "1. 根据发票内容（项目名称、销售方、商品明细）判断费用分类\n"
    "2. 费用分类必须从以下选项中选择：差旅、餐饮、住宿、交通、办公、其他\n"
    "3. 给出分类依据（说明判断理由）\n"
    "4. 必须调用 classify_and_check_limit 函数返回结构化结果\n"
    "5. 分类依据要具体，引用发票中的实际字段值"
)


def classify_and_check_limit(
    invoice: dict[str, Any],
) -> dict[str, Any]:
    """功能2：费用分类与限额校验

    Args:
        invoice: 功能1 提取的发票数据

    Returns:
        分类与限额校验结果，包含「费用分类」「分类依据」「是否超限」「校验结果」等；
        若发票金额 ≤ 100 元，返回小额免审结果。
    """
    amount = invoice.get("发票金额", 0)
    if not isinstance(amount, (int, float)):
        amount = 0

    limits = get_category_limits()
    other_limit = limits.get("其他", DEFAULT_OTHER_LIMIT)

    # 小额免审：金额 ≤ 100 元跳过分类限额校验
    if amount <= SMALL_AMOUNT_THRESHOLD:
        logger.info("发票金额 %.2f ≤ %.0f 元，小额免审", amount, SMALL_AMOUNT_THRESHOLD)
        return {
            "费用分类": "其他",
            "分类依据": f"金额 {amount} 元 ≤ {SMALL_AMOUNT_THRESHOLD} 元，小额免审",
            "发票金额": amount,
            "分类限额": SMALL_AMOUNT_THRESHOLD,
            "是否超限": False,
            "校验结果": f"金额 {amount} 元 ≤ 小额免审阈值 {SMALL_AMOUNT_THRESHOLD} 元，"
            f"免于限额校验",
        }

    # ① 调用 DeepSeek 做费用分类
    invoice_json = json.dumps(invoice, ensure_ascii=False, indent=2)
    user_content = f"发票数据:\n{invoice_json}"

    result = call_deepseek_function(
        system_prompt=SYSTEM_PROMPT,
        user_content=user_content,
        tools=CLASSIFY_LIMIT_TOOL,
        call_type="分类限额",
    )

    if "_error" in result or "_warning" in result:
        logger.warning("DeepSeek 分类失败: %s", result)
        return {
            "费用分类": "其他",
            "分类依据": "AI 分类失败，默认归为其他",
            "发票金额": amount,
            "分类限额": other_limit,
            "是否超限": amount > other_limit,
            "校验结果": f"AI 分类失败，发票金额 {amount} 元，按'其他'类限额校验",
            **result,
        }

    # ② 本地规则引擎校验限额（确保确定性，不依赖模型判断金额）
    category = result.get("费用分类", "其他")
    limit = limits.get(category, other_limit)

    result["发票金额"] = amount
    result["分类限额"] = limit
    result["是否超限"] = amount > limit

    if amount > limit:
        result["校验结果"] = (
            f"金额 {amount} 元 > {category}类限额 {limit} 元，"
            f"超出 {amount - limit:.2f} 元，需人工审批"
        )
    else:
        result["校验结果"] = f"金额 {amount} 元 ≤ {category}类限额 {limit} 元，通过"

    return result
