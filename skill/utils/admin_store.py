"""系统管理员数据访问层：系统配置 / 审计日志 / 用量统计。

对应 design.md §17.4.4 / §18 / §19 与 prototype.html 系统管理员三大 Tab。
所有写操作均尽力而为，失败仅记录日志，不阻断主流程。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from ..database import ApiUsage, AuditLog, SystemConfig, get_session, utcnow
from ..utils.structured_log import get_request_id
from .mask_sensitive import mask_ip

logger = logging.getLogger(__name__)

# ── DeepSeek-V4-Flash 定价（与原型一致）──
PRICE_INPUT_PER_1K = 0.001
PRICE_OUTPUT_PER_1K = 0.002

CONFIG_KEY = "system"


# ═══════════════════════════════════════════════
# 默认系统配置（与原型 + 现有 YAML 对齐）
# ═══════════════════════════════════════════════
DEFAULT_CONFIG: dict[str, Any] = {
    # 费用限额
    "limit_travel_transport": 3000,
    "limit_travel_hotel": 5000,
    "limit_meal_single": 1000,
    "limit_itinerary_single": 500,
    # 异常检测规则开关
    "rule_amount": True,
    "rule_invoice_auth": True,
    "rule_itinerary_field": True,
    "rule_deepseek_semantic": True,
    # 审批权限开关
    "approval_le_1000": True,
    "approval_1000_5000": True,
    "approval_gt_5000": True,
}

# 配置项元数据：用于前端渲染分组 / 标签 / 控件类型
CONFIG_SCHEMA: list[dict[str, Any]] = [
    {
        "group": "💰 费用限额配置",
        "items": [
            {"key": "limit_travel_transport", "label": "差旅-交通 月度限额", "type": "number", "unit": "元"},
            {"key": "limit_travel_hotel", "label": "差旅-住宿 月度限额", "type": "number", "unit": "元"},
            {"key": "limit_meal_single", "label": "餐饮 单笔上限", "type": "number", "unit": "元"},
            {"key": "limit_itinerary_single", "label": "行程单 单笔金额阈值", "type": "number", "unit": "元"},
        ],
    },
    {
        "group": "🚨 异常检测规则",
        "items": [
            {"key": "rule_amount", "label": "检测金额异常（与申请不一致）", "type": "toggle"},
            {"key": "rule_invoice_auth", "label": "检测发票真伪（国税查验）", "type": "toggle"},
            {"key": "rule_itinerary_field", "label": "行程单字段完整性检查", "type": "toggle"},
            {"key": "rule_deepseek_semantic", "label": "DeepSeek 语义复核", "type": "toggle"},
        ],
    },
    {
        "group": "👥 审批权限分配",
        "items": [
            {"key": "approval_le_1000", "label": "单笔 ≤ 1000 元 — 直属主管审批", "type": "toggle"},
            {"key": "approval_1000_5000", "label": "1000 < 单笔 ≤ 5000 元 — 部门负责人审批", "type": "toggle"},
            {"key": "approval_gt_5000", "label": "单笔 > 5000 元 — 分管领导审批", "type": "toggle"},
        ],
    },
]


# ═══════════════════════════════════════════════
# 系统配置
# ═══════════════════════════════════════════════
def get_system_config() -> dict[str, Any]:
    """返回合并默认值的系统配置"""
    cfg = dict(DEFAULT_CONFIG)
    try:
        with get_session() as s:
            row = s.query(SystemConfig).filter_by(key=CONFIG_KEY).first()
            if row and row.value:
                cfg.update(row.value)
    except Exception as e:  # pragma: no cover - 尽力而为
        logger.warning("读取系统配置失败: %s", e)
    return cfg


def get_config_schema() -> list[dict[str, Any]]:
    """返回配置项元数据（前端渲染用）"""
    return CONFIG_SCHEMA


def save_system_config(
    items: dict[str, Any],
    operator: str = "系统",
    role: str = "系统管理员",
    ip: str = "",
) -> dict[str, Any]:
    """保存系统配置（仅更新 schema 中存在的键），并写审计日志。

    Args:
        items: 待更新的配置键值（任意子集）
        operator / role / ip: 操作人信息，用于审计

    Returns:
        更新后的完整配置。
    """
    valid_keys = {it["key"] for grp in CONFIG_SCHEMA for it in grp["items"]}
    merged = get_system_config()
    changed: list[str] = []
    for k, v in items.items():
        if k in valid_keys:
            merged[k] = v
            changed.append(k)

    try:
        with get_session() as s:
            row = s.query(SystemConfig).filter_by(key=CONFIG_KEY).first()
            if row is None:
                row = SystemConfig(key=CONFIG_KEY, value=merged)
                s.add(row)
            else:
                row.value = merged
            s.commit()
    except Exception as e:  # pragma: no cover
        logger.error("保存系统配置失败: %s", e)
        return merged

    summary = "费用限额 / 异常规则 / 审批权限（%d 项）" % len(changed)
    add_audit_log(operator, role, "CONFIG_UPDATE", summary, "成功", ip)
    return merged


def reset_system_config(
    operator: str = "系统",
    role: str = "系统管理员",
    ip: str = "",
) -> dict[str, Any]:
    """恢复系统配置为默认值，并写审计日志。"""
    return save_system_config(
        dict(DEFAULT_CONFIG), operator=operator, role=role, ip=ip
    )


# ═══════════════════════════════════════════════
# 审计日志
# ═══════════════════════════════════════════════
def add_audit_log(
    user: str,
    role: str,
    action: str,
    target: str,
    result: str = "成功",
    ip: str = "",
    request_id: str = "",
) -> None:
    """追加一条审计日志（仅追加，不可删）。失败仅记录日志。"""
    try:
        with get_session() as s:
            s.add(
                AuditLog(
                    user=user,
                    role=role,
                    action=action,
                    target=target,
                    result=result,
                    ip=ip or "",
                    request_id=request_id or "",
                )
            )
            s.commit()
    except Exception as e:  # pragma: no cover
        logger.error("写入审计日志失败: %s", e)


def list_audit_log(limit: int = 200) -> list[dict[str, Any]]:
    """返回审计日志（按时间倒序）。"""
    try:
        with get_session() as s:
            rows = (
                s.query(AuditLog)
                .order_by(AuditLog.time.desc())
                .limit(limit)
                .all()
            )
            return [_audit_to_dict(r) for r in rows]
    except Exception as e:  # pragma: no cover
        logger.error("读取审计日志失败: %s", e)
        return []


def _audit_to_dict(r: AuditLog) -> dict[str, Any]:
    return {
        "time": r.time.strftime("%Y-%m-%d %H:%M:%S") if r.time else "",
        "user": r.user,
        "role": r.role,
        "action": r.action,
        "target": r.target,
        "request_id": r.request_id or "",
        "result": r.result,
        "ip": mask_ip(r.ip or ""),
    }


# ═══════════════════════════════════════════════
# 用量统计
# ═══════════════════════════════════════════════
def calc_cost_cny(prompt_tokens: int, completion_tokens: int) -> float:
    """按 DeepSeek-V4-Flash 定价估算费用（CNY）。"""
    return (
        prompt_tokens / 1000
    ) * PRICE_INPUT_PER_1K + (
        completion_tokens / 1000
    ) * PRICE_OUTPUT_PER_1K


def record_api_usage(
    call_type: str,
    model: str = "",
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    latency_ms: int = 0,
    status: str = "成功",
    request_id: str | None = None,
) -> None:
    """记录一次 DeepSeek / Vision API 调用（尽力而为）。"""
    try:
        with get_session() as s:
            s.add(
                ApiUsage(
                    request_id=request_id or get_request_id() or "",
                    call_type=call_type,
                    model=model,
                    prompt_tokens=int(prompt_tokens or 0),
                    completion_tokens=int(completion_tokens or 0),
                    latency_ms=int(latency_ms or 0),
                    status=status,
                )
            )
            s.commit()
    except Exception as e:  # pragma: no cover
        logger.debug("写入用量统计失败: %s", e)


def get_usage_overview() -> dict[str, Any]:
    """返回用量概览（聚合自 api_usage 表）。"""
    try:
        with get_session() as s:
            rows = s.query(ApiUsage).all()
            total_calls = len(rows)
            total_prompt = sum(r.prompt_tokens for r in rows)
            total_completion = sum(r.completion_tokens for r in rows)
            error_count = sum(1 for r in rows if r.status != "成功")
            avg_latency = (
                int(sum(r.latency_ms for r in rows) / total_calls)
                if total_calls
                else 0
            )
            total_tokens = total_prompt + total_completion
            success_rate = round(
                (total_calls - error_count) / total_calls * 100, 1
            ) if total_calls else 0.0
            return {
                "total_calls": total_calls,
                "total_prompt_tokens": total_prompt,
                "total_completion_tokens": total_completion,
                "total_tokens": total_tokens,
                "estimated_cost_cny": round(
                    calc_cost_cny(total_prompt, total_completion), 2
                ),
                "avg_latency_ms": avg_latency,
                "success_rate": success_rate,
                "error_count": error_count,
            }
    except Exception as e:  # pragma: no cover
        logger.error("读取用量概览失败: %s", e)
        return {
            "total_calls": 0,
            "total_prompt_tokens": 0,
            "total_completion_tokens": 0,
            "total_tokens": 0,
            "estimated_cost_cny": 0.0,
            "avg_latency_ms": 0,
            "success_rate": 0.0,
            "error_count": 0,
        }


def get_usage_daily(days: int = 7) -> list[dict[str, Any]]:
    """返回近 N 天每日调用趋势（token 消耗 + 调用次数）。"""
    try:
        with get_session() as s:
            rows = s.query(ApiUsage).all()
            buckets: dict[str, dict[str, Any]] = {}
            for r in rows:
                day = r.time.strftime("%m-%d") if r.time else "unknown"
                b = buckets.setdefault(day, {"date": day, "calls": 0, "tokens": 0})
                b["calls"] += 1
                b["tokens"] += r.prompt_tokens + r.completion_tokens
            data = sorted(buckets.values(), key=lambda x: x["date"])
            return data[-days:] if days else data
    except Exception as e:  # pragma: no cover
        logger.error("读取每日用量失败: %s", e)
        return []


def get_usage_by_type() -> list[dict[str, Any]]:
    """返回按调用类型的 token 占比 / 次数 / 费用分布。"""
    try:
        with get_session() as s:
            rows = s.query(ApiUsage).all()
            buckets: dict[str, dict[str, Any]] = {}
            for r in rows:
                b = buckets.setdefault(
                    r.call_type,
                    {
                        "type": r.call_type,
                        "calls": 0,
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "tokens": 0,
                        "cost": 0.0,
                    },
                )
                b["calls"] += 1
                b["prompt_tokens"] += r.prompt_tokens
                b["completion_tokens"] += r.completion_tokens
                b["tokens"] += r.prompt_tokens + r.completion_tokens
                b["cost"] += calc_cost_cny(r.prompt_tokens, r.completion_tokens)
            for b in buckets.values():
                b["cost"] = round(b["cost"], 2)
            return sorted(buckets.values(), key=lambda x: -x["tokens"])
    except Exception as e:  # pragma: no cover
        logger.error("读取按类型用量失败: %s", e)
        return []


def list_usage_records(
    date_filter: str | None = None,
    type_filter: str | None = None,
    status_filter: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """返回调用明细（支持按 日期MM-DD / 类型 / 状态 筛选）。"""
    try:
        with get_session() as s:
            q = s.query(ApiUsage)
            if type_filter:
                q = q.filter(ApiUsage.call_type == type_filter)
            if status_filter:
                q = q.filter(ApiUsage.status == status_filter)
            rows = q.order_by(ApiUsage.time.desc()).limit(limit).all()
            out = []
            for r in rows:
                day = r.time.strftime("%m-%d") if r.time else ""
                if date_filter and day != date_filter:
                    continue
                out.append(_usage_to_dict(r))
            return out
    except Exception as e:  # pragma: no cover
        logger.error("读取调用明细失败: %s", e)
        return []


def _usage_to_dict(r: ApiUsage) -> dict[str, Any]:
    return {
        "time": r.time.strftime("%Y-%m-%d %H:%M:%S") if r.time else "",
        "request_id": r.request_id,
        "call_type": r.call_type,
        "model": r.model,
        "prompt_tokens": r.prompt_tokens,
        "completion_tokens": r.completion_tokens,
        "total_tokens": r.prompt_tokens + r.completion_tokens,
        "latency_ms": r.latency_ms,
        "status": r.status,
        "cost": round(calc_cost_cny(r.prompt_tokens, r.completion_tokens), 4),
    }


# ═══════════════════════════════════════════════
# 演示数据预置（保证页面打开即有内容，与产品演示目的一致）
# ═══════════════════════════════════════════════
# 原型审计日志演示数据（prototype.html MOCK_AUDIT_LOG）
_SEED_AUDIT_LOG = [
    ("赵管理", "系统管理员", "CONFIG_UPDATE", "差旅-住宿 月度限额: 5000 → 6000", "成功", "10.0.1.32", "2026-07-14 14:32:18"),
    ("王会计", "财务人员", "PAYMENT_INIT", "RB-2026-0713-021 · ¥425.80", "成功", "10.0.1.18", "2026-07-14 14:28:05"),
    ("李总", "审批领导", "APPROVE", "RB-2026-0714-004 · ¥4520.00", "成功", "10.0.1.12", "2026-07-14 14:25:42"),
    ("李总", "审批领导", "APPROVE", "RB-2026-0714-002 · ¥186.50", "成功", "10.0.1.12", "2026-07-14 14:20:11"),
    ("王会计", "财务人员", "ARCHIVE", "RB-2026-0713-018 · ¥680.00", "成功", "10.0.1.18", "2026-07-14 14:15:33"),
    ("李总", "审批领导", "REJECT", "RB-2026-0714-005 · ¥3280.00", "成功", "10.0.1.12", "2026-07-14 14:10:08"),
    ("张三", "普通员工", "SUBMIT", "RB-2026-0714-001 · ¥358.50", "成功", "10.0.1.45", "2026-07-14 13:58:22"),
    ("赵管理", "系统管理员", "RULE_TOGGLE", "检测金额异常 → 启用", "成功", "10.0.1.32", "2026-07-14 13:45:17"),
    ("李总", "审批领导", "LOGIN", "工号 APR-001", "成功", "10.0.1.12", "2026-07-14 11:20:05"),
    ("王会计", "财务人员", "PAYMENT_INIT", "RB-2026-0713-015 · ¥1280.00", "成功", "10.0.1.18", "2026-07-14 10:35:41"),
    ("赵管理", "系统管理员", "PERMISSION_GRANT", "王会计 → 财务终审权限", "成功", "10.0.1.32", "2026-07-14 09:18:33"),
    ("李总", "审批领导", "TRANSFER", "RB-2026-0713-019 · ¥5800.00 → 分管领导", "成功", "10.0.1.12", "2026-07-13 18:42:09"),
    ("李四", "普通员工", "SUBMIT", "RB-2026-0714-002 · ¥186.50", "成功", "10.0.1.48", "2026-07-13 17:30:21"),
    ("李总", "审批领导", "LOGIN_FAILED", "工号 APR-001 · 密码错误", "失败", "10.0.1.12", "2026-07-13 16:15:48"),
    ("王会计", "财务人员", "LOGIN", "工号 FIN-001", "成功", "10.0.1.18", "2026-07-13 14:22:11"),
]

# 按类型分布（prototype.html MOCK_BY_TYPE）：calls / 输入token / 输出token / 平均延迟ms
_SEED_USAGE_BY_TYPE = [
    ("发票OCR提取", 520, 1820000, 780000, 2100),
    ("行程单OCR提取", 186, 620000, 310000, 2200),
    ("异常检测", 320, 280000, 156000, 1300),
    ("分类限额", 225, 120000, 84000, 950),
    ("Vision API", 35, 16000, 14000, 3200),
]

# 每日调用分布（prototype.html MOCK_DAILY）：date / calls
_SEED_USAGE_DAILY = [
    ("07-08", 152),
    ("07-09", 198),
    ("07-10", 176),
    ("07-11", 223),
    ("07-12", 165),
    ("07-13", 189),
    ("07-14", 183),
]

# 少量明细样例（prototype.html MOCK_RECORDS 节选，含失败记录）
_SEED_USAGE_RECORDS = [
    ("2026-07-14 14:32:15", "a3f8b2c1e9d4", "发票OCR提取", "deepseek-v4-flash", 3200, 1800, 2100, "成功"),
    ("2026-07-14 14:30:08", "b7e2d9f4a1c6", "异常检测", "deepseek-v4-flash", 850, 420, 1200, "成功"),
    ("2026-07-14 14:28:33", "c1d5a8e3b2f7", "行程单OCR提取", "deepseek-v4-flash", 3100, 1650, 2350, "成功"),
    ("2026-07-14 14:25:51", "d9f3c7b1e6a2", "分类限额", "deepseek-v4-flash", 520, 380, 900, "成功"),
    ("2026-07-14 14:20:17", "e2a6b9d4c8f1", "发票OCR提取", "deepseek-v4-flash", 2800, 1500, 1980, "成功"),
    ("2026-07-14 14:10:05", "a4b7e2d9c6f3", "Vision API", "deepseek-vl", 480, 420, 3200, "成功"),
    ("2026-07-13 17:45:22", "f2c7e5b8a1d4", "发票OCR提取", "deepseek-v4-flash", 3100, 0, 0, "失败"),
    ("2026-07-13 16:30:08", "a8b3f6c1e9d2", "分类限额", "deepseek-v4-flash", 540, 0, 600, "失败"),
]


def _seed_audit_demo() -> None:
    with get_session() as s:
        if s.query(AuditLog).count() > 0:
            return
        for user, role, action, target, result, ip, tstr in _SEED_AUDIT_LOG:
            s.add(
                AuditLog(
                    user=user,
                    role=role,
                    action=action,
                    target=target,
                    result=result,
                    ip=ip,
                    time=datetime.strptime(tstr, "%Y-%m-%d %H:%M:%S"),
                )
            )
        s.commit()


def _seed_usage_demo() -> None:
    with get_session() as s:
        if s.query(ApiUsage).count() > 0:
            return
        # 先写入少量明细样例（含失败记录）
        for tstr, rid, ctype, model, pt, ct, lat, status in _SEED_USAGE_RECORDS:
            s.add(
                ApiUsage(
                    request_id=rid,
                    call_type=ctype,
                    model=model,
                    prompt_tokens=pt,
                    completion_tokens=ct,
                    latency_ms=lat,
                    status=status,
                    time=datetime.strptime(tstr, "%Y-%m-%d %H:%M:%S"),
                )
            )
        s.commit()

        # 构造日期轮转序列（按每日权重展开，使每日分布与原型一致）
        day_cycle: list[str] = []
        for day, calls in _SEED_USAGE_DAILY:
            day_cycle += [day] * calls

        # 按类型逐类生成，确保每类调用次数精确匹配原型
        idx = 0
        for ctype, calls, pt_total, ct_total, avg_latency in _SEED_USAGE_BY_TYPE:
            avg_pt = pt_total // max(calls, 1)
            avg_ct = ct_total // max(calls, 1)
            model = "deepseek-v4-flash" if ctype != "Vision API" else "deepseek-vl"
            for _ in range(calls):
                day = day_cycle[idx % len(day_cycle)]
                idx += 1
                s.add(
                    ApiUsage(
                        request_id="",
                        call_type=ctype,
                        model=model,
                        prompt_tokens=avg_pt,
                        completion_tokens=avg_ct,
                        latency_ms=avg_latency,
                        status="成功",
                        time=datetime(2026, 7, int(day.split("-")[1]), 12, 0, 0),
                    )
                )
        s.commit()


def ensure_seeded() -> None:
    """首次为空时预置演示数据（审计日志 + 用量统计）。"""
    try:
        _seed_audit_demo()
        _seed_usage_demo()
    except Exception as e:  # pragma: no cover
        logger.warning("预置演示数据失败: %s", e)
