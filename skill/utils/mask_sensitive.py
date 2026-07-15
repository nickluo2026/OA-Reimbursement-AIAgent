"""敏感数据脱敏工具 — 用于 API 响应层，对 OCR 结果中的隐私字段进行脱敏。

数据库始终保留完整数据（审计追溯），仅在返回前端时脱敏。
"""

import copy
from typing import Any


def mask_phone(phone: str) -> str:
    """手机号脱敏：保留前3后4，中间用****替代。13812345678 → 138****5678"""
    if not phone or len(phone) < 7:
        return phone or ""
    return phone[:3] + "****" + phone[-4:]


def mask_tax_id(tax_id: str) -> str:
    """统一社会信用代码/税号脱敏：保留前4后4，中间用*替代。
    91110108MA01ABCD23 → 9111***********CD23"""
    if not tax_id or len(tax_id) < 8:
        return tax_id or ""
    return tax_id[:4] + "*" * (len(tax_id) - 8) + tax_id[-4:]


def mask_ip(ip: str) -> str:
    """IP 地址脱敏：IPv4 保留前两段，后两段以 *** 替代。
    192.168.1.100 → 192.168.***.***
    非 IPv4 格式（IPv6 / 空值等）统一返回 ***。"""
    if not ip:
        return ""
    parts = ip.split(".")
    if len(parts) == 4 and all(p.isdigit() for p in parts):
        return f"{parts[0]}.{parts[1]}.***.***"
    return "***"


# 需要脱敏的字段名 → 对应的脱敏函数
_MASK_RULES = {
    "手机号": mask_phone,
    "购买方税号": mask_tax_id,
    "销售方税号": mask_tax_id,
}


def mask_ocr_result(ocr: dict[str, Any] | None) -> dict[str, Any] | None:
    """对发票/行程单 OCR 结果进行脱敏（深拷贝后替换，不影响原始数据）。"""
    if not ocr:
        return ocr
    masked = copy.deepcopy(ocr)
    for field, mask_fn in _MASK_RULES.items():
        if field in masked and isinstance(masked[field], str):
            masked[field] = mask_fn(masked[field])
    return masked
