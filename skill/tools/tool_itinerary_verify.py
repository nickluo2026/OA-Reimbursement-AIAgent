"""行程单合理性校验工具（纯规则计算，无 DeepSeek 调用）

校验项（对齐原型）：
    1. 总金额匹配：sum(行程详情.金额) 与 总金额_元 一致，且 ≤ 申请金额
    2. 行程天数：结束日期 - 开始日期 + 1
    3. 单笔最高金额：max(行程详情.金额)，校验是否超阈值
    4. 日期合理性：所有行程上车时间在 [开始日期, 结束日期] 内
    5. 行程连续性：按时间排序检查间隔合理性
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from ..config import get_itinerary_rules

logger = logging.getLogger(__name__)


def _parse_date(s: str) -> date | None:
    """解析 YYYY-MM-DD 日期，失败返回 None"""
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _parse_datetime(s: str) -> datetime | None:
    """解析 YYYY-MM-DD HH:MM 日期时间，失败尝试纯日期，再失败返回 None"""
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except (ValueError, TypeError):
            continue
    return None


def _to_float(val: Any) -> float | None:
    """安全转换为 float"""
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def verify_itinerary(
    itinerary: dict[str, Any],
    apply_amount: float | None = None,
) -> dict[str, Any]:
    """行程单合理性校验（纯规则计算）

    Args:
        itinerary: OCR 提取的行程单数据
        apply_amount: 用户申请报销金额

    Returns:
        合理性校验结果，结构对齐原型：
        ``{校验结论, 总金额校验, 行程天数, 单笔最高金额, 日期合理性, 行程连续性, 校验明细[]}``
    """
    rules = get_itinerary_rules()
    single_threshold = rules.get("itinerary_single_amount_threshold", 500)

    details = itinerary.get("行程详情")
    details_list = details if isinstance(details, list) else []

    start_date = _parse_date(str(itinerary.get("行程开始日期", "")).strip())
    end_date = _parse_date(str(itinerary.get("行程结束日期", "")).strip())
    total_amount = _to_float(itinerary.get("总金额_元"))
    apply_amt = apply_amount if isinstance(apply_amount, (int, float)) else None

    # —— 计算明细合计与单笔最高 ——
    amounts: list[float] = []
    for item in details_list:
        if not isinstance(item, dict):
            continue
        amt = _to_float(item.get("金额_元"))
        if amt is not None:
            amounts.append(amt)
    sum_amount = sum(amounts) if amounts else 0.0
    max_amount = max(amounts) if amounts else 0.0

    details_items: list[dict[str, str]] = []
    has_block = False
    has_warning = False

    # —— 1. 总金额校验 ——
    if total_amount is None:
        amount_check = "总金额缺失，无法校验"
        amount_result = "拦截"
        has_block = True
    elif amounts and abs(total_amount - sum_amount) > 0.01:
        amount_check = (
            f"总金额 {total_amount} 元与明细合计 {sum_amount:.2f} 元不一致，"
            f"差额 {total_amount - sum_amount:.2f} 元"
        )
        amount_result = "拦截"
        has_block = True
    elif apply_amt and total_amount and total_amount > apply_amt:
        amount_check = f"总金额 {total_amount} 元超过申请金额 {apply_amt} 元"
        amount_result = "拦截"
        has_block = True
    else:
        amount_check = f"总金额 {total_amount} 元与明细合计 {sum_amount:.2f} 元一致" + (
            f"，不超过申请金额 {apply_amt} 元" if apply_amt else ""
        )
        amount_result = "通过"
    details_items.append(
        {
            "校验项目": "总金额匹配",
            "校验结果": amount_result,
            "说明": amount_check,
        }
    )

    # —— 2. 行程天数 ——
    if start_date and end_date:
        days = (end_date - start_date).days + 1
        if days < 0:
            days_check = "行程天数异常：开始日期晚于结束日期"
            days_result = "拦截"
            has_block = True
        elif days > 90:
            days_check = f"行程天数 {days} 天偏长，请核实"
            days_result = "预警"
            has_warning = True
        else:
            days_check = f"行程天数 {days} 天"
            days_result = "通过"
        days_value = days if days >= 0 else 0
    else:
        days_check = "行程开始/结束日期缺失，无法计算天数"
        days_result = "拦截"
        has_block = True
        days_value = 0
    details_items.append(
        {
            "校验项目": "行程天数",
            "校验结果": days_result,
            "说明": days_check,
        }
    )

    # —— 3. 单笔最高金额 ——
    if amounts:
        if max_amount > single_threshold:
            single_check = f"单笔最高金额 {max_amount} 元超过阈值 {single_threshold} 元"
            single_result = "预警"
            has_warning = True
        else:
            single_check = f"单笔最高金额 {max_amount} 元，未超阈值 {single_threshold} 元"
            single_result = "通过"
    else:
        single_check = "无行程明细，无法计算单笔金额"
        single_result = "拦截"
        has_block = True
    details_items.append(
        {
            "校验项目": "单笔最高金额",
            "校验结果": single_result,
            "说明": single_check,
        }
    )

    # —— 4. 日期合理性 ——
    if start_date and end_date and details_list:
        out_of_range = []
        for item in details_list:
            if not isinstance(item, dict):
                continue
            board_str = str(item.get("上车时间", "")).strip()
            if not board_str:
                continue
            board_dt = _parse_datetime(board_str)
            if board_dt is None:
                continue
            board_date = board_dt.date()
            if board_date < start_date or board_date > end_date:
                out_of_range.append(f"第 {item.get('序号', '?')} 行 {board_str}")
        if out_of_range:
            date_check = f"以下行程上车时间不在行程日期范围内: {', '.join(out_of_range[:5])}"
            date_result = "拦截"
            has_block = True
        else:
            date_check = "所有行程上车时间均在行程日期范围内"
            date_result = "通过"
    else:
        date_check = "行程日期或明细缺失，无法校验日期合理性"
        date_result = "拦截"
        has_block = True
    details_items.append(
        {
            "校验项目": "日期合理性",
            "校验结果": date_result,
            "说明": date_check,
        }
    )

    # —— 5. 行程连续性 ——
    if details_list:
        board_times: list[tuple[int, datetime]] = []
        for item in details_list:
            if not isinstance(item, dict):
                continue
            board_str = str(item.get("上车时间", "")).strip()
            if not board_str:
                continue
            board_dt = _parse_datetime(board_str)
            if board_dt is not None:
                idx = item.get("序号", 0)
                try:
                    idx_int = int(idx)
                except (ValueError, TypeError):
                    idx_int = 0
                board_times.append((idx_int, board_dt))

        if len(board_times) >= 2:
            board_times.sort(key=lambda x: x[1])
            gaps_ok = True
            big_gaps: list[str] = []
            for i in range(1, len(board_times)):
                gap = (board_times[i][1] - board_times[i - 1][1]).total_seconds()
                # 间隔超过 72 小时视为不连续
                if gap > 72 * 3600:
                    gaps_ok = False
                    big_gaps.append(
                        f"第{board_times[i][0]}行与第{board_times[i-1][0]}行间隔超过72小时"
                    )
            if gaps_ok:
                cont_check = "行程按时间排序连续性合理"
                cont_result = "通过"
            else:
                cont_check = "行程存在较大间隔: " + "; ".join(big_gaps[:3])
                cont_result = "预警"
                has_warning = True
        else:
            cont_check = "行程记录不足，无法校验连续性"
            cont_result = "通过"
    else:
        cont_check = "无行程明细，无法校验连续性"
        cont_result = "拦截"
        has_block = True
    details_items.append(
        {
            "校验项目": "行程连续性",
            "校验结果": cont_result,
            "说明": cont_check,
        }
    )

    # —— 总体结论 ——
    if has_block:
        conclusion = "拦截"
    elif has_warning:
        conclusion = "预警"
    else:
        conclusion = "通过"

    logger.info("行程单合理性校验完成: %s", conclusion)

    return {
        "校验结论": conclusion,
        "总金额校验": amount_check,
        "行程天数": days_value,
        "单笔最高金额": single_check,
        "日期合理性": date_check,
        "行程连续性": cont_check,
        "校验明细": details_items,
    }
