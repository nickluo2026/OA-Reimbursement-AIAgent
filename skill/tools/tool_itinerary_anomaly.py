"""行程单异常检测工具（前置拦截，纯规则引擎）

行程单字段确定性强，规则可覆盖；不调用 DeepSeek。
检测项：
    - 字段缺失：申请日期/行程开始日期/行程结束日期/总金额/行程详情 为空
    - 日期格式：开始/结束日期非 YYYY-MM-DD
    - 日期逻辑：开始 > 结束、行程日期晚于申请日
    - 单笔金额异常：单行程金额 > 阈值
    - 总金额异常：总金额 > 上限
    - 行程数异常：行程数为 0 或超过上限
"""

from __future__ import annotations

import logging
from datetime import datetime, date
from typing import Any

from ..config import get_itinerary_rules

logger = logging.getLogger(__name__)


def _parse_date(s: str) -> date | None:
    """解析 YYYY-MM-DD 日期，失败返回 None"""
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _to_float(val: Any) -> float | None:
    """安全转换为 float"""
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _rule_based_check(
    itinerary: dict[str, Any],
    apply_amount: float | None = None,
    apply_date: str | None = None,
) -> list[dict[str, str]]:
    """规则引擎检查，返回异常明细列表"""
    rules = get_itinerary_rules()
    anomalies: list[dict[str, str]] = []

    # --- 字段缺失检查 ---
    for field in rules.get("itinerary_required_fields", []):
        val = itinerary.get(field)
        if val is None or val == "" or val == 0 or val == []:
            anomalies.append({
                "异常类型": "字段缺失",
                "异常描述": f"必填字段「{field}」缺失或为空",
                "严重程度": "严重",
            })

    # --- 日期格式与逻辑检查 ---
    start_str = str(itinerary.get("行程开始日期", "")).strip()
    end_str = str(itinerary.get("行程结束日期", "")).strip()
    apply_str = str(itinerary.get("申请日期", "")).strip()
    if apply_date:
        apply_str = apply_date

    start_date = _parse_date(start_str) if start_str else None
    end_date = _parse_date(end_str) if end_str else None
    apply_date_obj = _parse_date(apply_str) if apply_str else None

    if start_str and not start_date:
        anomalies.append({
            "异常类型": "格式错误",
            "异常描述": f"行程开始日期格式错误: {start_str}，应为 YYYY-MM-DD",
            "严重程度": "严重",
        })
    if end_str and not end_date:
        anomalies.append({
            "异常类型": "格式错误",
            "异常描述": f"行程结束日期格式错误: {end_str}，应为 YYYY-MM-DD",
            "严重程度": "严重",
        })

    # 日期逻辑：开始 > 结束
    if start_date and end_date and start_date > end_date:
        anomalies.append({
            "异常类型": "日期异常",
            "异常描述": f"行程开始日期 {start_str} 晚于结束日期 {end_str}",
            "严重程度": "严重",
        })

    # 日期逻辑：行程日期晚于申请日
    if apply_date_obj and start_date and start_date > apply_date_obj:
        anomalies.append({
            "异常类型": "日期异常",
            "异常描述": f"行程开始日期 {start_str} 晚于申请日期 {apply_str}",
            "严重程度": "严重",
        })
    if apply_date_obj and end_date and end_date > apply_date_obj:
        anomalies.append({
            "异常类型": "日期异常",
            "异常描述": f"行程结束日期 {end_str} 晚于申请日期 {apply_str}",
            "严重程度": "严重",
        })

    # --- 行程详情检查 ---
    details = itinerary.get("行程详情")
    details_list = details if isinstance(details, list) else []

    single_threshold = rules.get("itinerary_single_amount_threshold", 500)
    total_threshold = rules.get("itinerary_total_amount_threshold", 2000)
    max_count = rules.get("itinerary_max_count", 50)

    # 行程数异常
    count = len(details_list)
    if count == 0:
        anomalies.append({
            "异常类型": "行程数异常",
            "异常描述": "行程详情为空，无有效行程记录",
            "严重程度": "严重",
        })
    elif count > max_count:
        anomalies.append({
            "异常类型": "行程数异常",
            "异常描述": f"行程数 {count} 超过上限 {max_count}",
            "严重程度": "严重",
        })

    # 单笔金额异常
    for idx, item in enumerate(details_list):
        if not isinstance(item, dict):
            continue
        amt = _to_float(item.get("金额_元"))
        if amt is not None and amt > single_threshold:
            anomalies.append({
                "异常类型": "金额异常",
                "异常描述": f"第 {item.get('序号', idx + 1)} 行行程金额 {amt} 元超过单笔阈值 {single_threshold} 元",
                "严重程度": "警告",
            })

    # 总金额异常
    total_amount = _to_float(itinerary.get("总金额_元"))
    if total_amount is not None and total_amount > total_threshold:
        anomalies.append({
            "异常类型": "金额异常",
            "异常描述": f"行程单总金额 {total_amount} 元超过上限 {total_threshold} 元",
            "严重程度": "严重",
        })

    # 申请金额校验（总金额 ≤ 申请金额）
    if (total_amount is not None and total_amount > 0
            and apply_amount is not None and apply_amount > 0
            and total_amount > apply_amount):
        diff = total_amount - apply_amount
        anomalies.append({
            "异常类型": "金额异常",
            "异常描述": f"行程单总金额 {total_amount} 元超过申请金额 {apply_amount} 元，超出 {diff} 元",
            "严重程度": "严重",
        })

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


def detect_itinerary_anomaly(
    itinerary: dict[str, Any],
    apply_amount: float | None = None,
    apply_date: str | None = None,
) -> dict[str, Any]:
    """行程单异常输入检查（前置拦截，纯规则引擎）

    Args:
        itinerary: OCR 提取的行程单数据
        apply_amount: 用户申请报销金额
        apply_date: 申请日期，格式 YYYY-MM-DD

    Returns:
        异常检查结果，包含「总体结论」「异常明细」「检查摘要」；
        总体结论为"拦截"时，agent 将跳过后续合理性校验。
    """
    anomalies = _rule_based_check(itinerary, apply_amount, apply_date)
    conclusion, summary = _summarize(anomalies)

    if conclusion == "拦截":
        logger.warning("行程单规则引擎拦截: %s", summary)

    return {
        "总体结论": conclusion,
        "异常明细": anomalies,
        "检查摘要": f"[规则引擎] {summary}",
    }
