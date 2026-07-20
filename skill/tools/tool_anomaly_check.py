"""功能3：异常输入检查（前置拦截）

流程：规则引擎本地检查 + DeepSeek Function Call 语义检查
检测类型：字段缺失 / 格式错误 / 票据过期 / 金额异常 / 日期异常
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from ..config import get_anomaly_rules
from ..schemas.anomaly_schema import ANOMALY_CHECK_TOOL
from ..utils.http_client import call_deepseek_function

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "你是发票异常检测助手。\n"
    "\n"
    "工作流程：\n"
    "1. 审查用户提供的发票数据，检测以下异常：\n"
    "   - 字段缺失：必填字段（发票号码/开票日期/发票金额/销售方名称/购买方名称）为空\n"
    "   - 格式错误：发票号码长度不符、日期格式错误\n"
    "   - 票据过期：开票日期距申请日超过规定天数\n"
    "   - 金额异常：发票金额超过异常阈值\n"
    "   - 日期异常：开票日期晚于当前日期等逻辑错误\n"
    "2. 根据异常严重程度给出总体结论：拦截/预警/通过\n"
    "3. 必须调用 detect_anomaly 函数返回结构化结果\n"
    "4. 无异常时「异常明细」为空数组，「总体结论」为「通过」"
)


def _rule_based_check(
    invoice: dict[str, Any],
    apply_amount: float | None = None,
    apply_date: str | None = None,
) -> list[dict[str, str]]:
    """本地规则引擎前置检查，返回异常明细列表

    先做确定性规则检查，再将结果与发票数据一起交给 DeepSeek 做语义补充。
    """
    rules = get_anomaly_rules()
    anomalies: list[dict[str, str]] = []

    # --- 字段缺失检查 ---
    for field in rules.get("required_fields", []):
        val = invoice.get(field)
        if val is None or val == "" or val == 0:
            anomalies.append(
                {
                    "异常类型": "字段缺失",
                    "异常描述": f"必填字段「{field}」缺失或为空",
                    "严重程度": "严重",
                }
            )

    # --- 发票号码格式检查 ---
    invoice_no = str(invoice.get("发票号码", "")).strip()
    if invoice_no:
        min_len = rules.get("invoice_number_min_length", 8)
        max_len = rules.get("invoice_number_max_length", 20)
        if not (min_len <= len(invoice_no) <= max_len):
            anomalies.append(
                {
                    "异常类型": "格式错误",
                    "异常描述": f"发票号码长度 {len(invoice_no)} 不在允许范围 "
                    f"[{min_len}, {max_len}]",
                    "严重程度": "严重",
                }
            )

    # --- 日期检查 ---
    invoice_date_str = str(invoice.get("开票日期", "")).strip()
    if invoice_date_str:
        try:
            invoice_date = datetime.strptime(invoice_date_str, "%Y-%m-%d").date()
        except ValueError:
            anomalies.append(
                {
                    "异常类型": "格式错误",
                    "异常描述": f"开票日期格式错误: {invoice_date_str}，应为 YYYY-MM-DD",
                    "严重程度": "严重",
                }
            )
            invoice_date = None
    else:
        invoice_date = None

    # 票据过期检查
    if invoice_date:
        ref_date = date.today()
        if apply_date:
            try:
                ref_date = datetime.strptime(apply_date, "%Y-%m-%d").date()
            except ValueError:
                pass
        age_days = (ref_date - invoice_date).days
        max_age = rules.get("max_invoice_age_days", 180)
        if age_days > max_age:
            anomalies.append(
                {
                    "异常类型": "票据过期",
                    "异常描述": f"开票日期距申请日 {age_days} 天，超过 {max_age} 天上限",
                    "严重程度": "严重",
                }
            )
        # 日期异常：开票日期晚于申请日
        elif age_days < 0:
            anomalies.append(
                {
                    "异常类型": "日期异常",
                    "异常描述": f"开票日期 {invoice_date_str} 晚于申请日 {ref_date}",
                    "严重程度": "严重",
                }
            )
        # 即将过期预警（剩余 30 天内）
        elif age_days > max_age - 30:
            anomalies.append(
                {
                    "异常类型": "即将过期",
                    "异常描述": f"票据将在 {max_age - age_days} 天后过期，请及时报销",
                    "严重程度": "警告",
                }
            )

    # --- 金额异常检查（高额阈值）---
    amount = invoice.get("发票金额", 0)
    if isinstance(amount, (int, float)) and amount > 0 and rules.get("enable_amount_check", True):
        threshold = rules.get("amount_anomaly_threshold", 10000)
        if amount > threshold:
            anomalies.append(
                {
                    "异常类型": "金额异常",
                    "异常描述": f"发票金额 {amount} 元超过异常阈值 {threshold} 元",
                    "严重程度": "严重",
                }
            )

    # --- 申请金额校验（发票金额 ≤ 申请金额）---
    if (
        isinstance(amount, (int, float))
        and amount > 0
        and apply_amount is not None
        and apply_amount > 0
    ):
        if amount > apply_amount:
            diff = amount - apply_amount
            anomalies.append(
                {
                    "异常类型": "金额异常",
                    "异常描述": f"发票金额 {amount} 元超过申请金额 {apply_amount} 元，"
                    f"超出 {diff} 元，需修改申请金额或核实票据",
                    "严重程度": "严重",
                }
            )

    return anomalies


def _summarize(anomalies: list[dict[str, str]]) -> tuple[str, str]:
    """根据规则检查结果确定总体结论与摘要"""
    if not anomalies:
        return "通过", "规则检查未发现异常"

    has_severe = any(a["严重程度"] == "严重" for a in anomalies)
    if has_severe:
        conclusion = "拦截"
    else:
        conclusion = "预警"

    types = [a["异常类型"] for a in anomalies]
    summary = f"检测到 {len(anomalies)} 项异常: {', '.join(types)}"
    return conclusion, summary


def detect_anomaly(
    invoice: dict[str, Any],
    apply_amount: float | None = None,
    apply_date: str | None = None,
) -> dict[str, Any]:
    """功能3：异常输入检查（前置拦截）

    Args:
        invoice: 功能1 提取的发票数据
        apply_amount: 用户申请报销金额
        apply_date: 申请日期，格式 YYYY-MM-DD，默认今天

    Returns:
        异常检查结果，包含「总体结论」「异常明细」「检查摘要」；
        总体结论为"拦截"时，agent 将跳过后续功能。
    """
    # ① 本地规则引擎检查
    rule_anomalies = _rule_based_check(invoice, apply_amount, apply_date)

    # 若规则检查已发现严重异常，直接返回拦截（快速失败）
    conclusion, summary = _summarize(rule_anomalies)
    if conclusion == "拦截":
        logger.warning("规则引擎拦截: %s", summary)
        return {
            "总体结论": conclusion,
            "异常明细": rule_anomalies,
            "检查摘要": f"[规则引擎] {summary}",
        }

    # ② 交给 DeepSeek 做语义补充检查（检测规则未覆盖的隐性异常）
    # F5: 管理员可通过 rule_deepseek_semantic 关闭语义检查
    rules = get_anomaly_rules()
    if not rules.get("enable_deepseek_semantic", True):
        logger.info("DeepSeek 语义检查已被管理员关闭，仅使用规则引擎结果")
        return {
            "总体结论": conclusion,
            "异常明细": rule_anomalies,
            "检查摘要": f"[规则引擎] {summary}",
        }

    import json

    invoice_json = json.dumps(invoice, ensure_ascii=False, indent=2)
    user_content = (
        f"申请金额: {apply_amount}\n"
        f"申请日期: {apply_date or '今天'}\n\n"
        f"发票数据:\n{invoice_json}\n\n"
        f"规则引擎已检测到的异常（请合并到结果中）:\n"
        f"{json.dumps(rule_anomalies, ensure_ascii=False)}"
    )

    result = call_deepseek_function(
        system_prompt=SYSTEM_PROMPT,
        user_content=user_content,
        tools=ANOMALY_CHECK_TOOL,
        call_type="异常检测",
    )

    # ③ 合并规则引擎结果
    # DeepSeek 被停用（_disabled）或调用失败/兜底时，使用规则引擎结果
    if "_error" in result or "_warning" in result or result.get("_disabled"):
        # DeepSeek 调用失败，使用规则引擎结果
        logger.warning("DeepSeek 异常检查失败，使用规则引擎结果")
        return {
            "总体结论": conclusion,
            "异常明细": rule_anomalies,
            "检查摘要": f"[规则引擎兜底] {summary}",
        }

    # 确保 DeepSeek 结果中包含规则引擎发现的异常
    deepseek_anomalies = result.get("异常明细", [])
    existing_descs = {a.get("异常描述") for a in deepseek_anomalies}
    for ra in rule_anomalies:
        if ra.get("异常描述") not in existing_descs:
            deepseek_anomalies.append(ra)

    # 重新判定总体结论（取规则引擎与 DeepSeek 中更严格的结果）
    ds_conclusion = result.get("总体结论", "通过")
    priority = {"通过": 0, "预警": 1, "拦截": 2}
    final_conclusion = (
        ds_conclusion
        if priority.get(ds_conclusion, 0) >= priority.get(conclusion, 0)
        else conclusion
    )

    result["异常明细"] = deepseek_anomalies
    result["总体结论"] = final_conclusion
    return result
