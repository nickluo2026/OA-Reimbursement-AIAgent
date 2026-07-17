# -*- coding: utf-8 -*-
"""DeepSeek AI Agent Skill — 发票报销智能校验

三功能架构：
  功能1  ocr_extract_invoice   — OCR 提取发票全部内容
  功能3  detect_anomaly        — 异常输入检查（前置拦截）
  功能2  classify_and_check_limit — 金额>100 时限额与费用分类

执行编排见 agent.run_reimbursement_skill()
"""

from .agent import run_reimbursement_skill

__all__ = ["run_reimbursement_skill"]
__version__ = "1.0.0"
