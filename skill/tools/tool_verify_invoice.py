"""功能5：发票真伪查验（Provider 抽象 + Mock 实现）

通过可插拔的 Provider 抽象校验发票真伪（是否真实、是否红冲/作废）。
当前版本提供 MockVerifyProvider（默认），真实第三方平台（百望/航信/
票易通等）的接入在 verify_invoice() 中预留扩展点（TODO）。

Provider 由环境变量 INVOICE_VERIFY_PROVIDER 选择（默认 mock）。

输入：功能1 提取的发票数据（含 发票代码/发票号码/开票日期/价税合计小写）
输出：结构化查验结果，含「查验状态」「总体结论」「查验明细」「查验平台」。
"""

from __future__ import annotations

import logging
from typing import Any

from ..config import INVOICE_VERIFY_PROVIDER

logger = logging.getLogger(__name__)

# —— 查验状态枚举 ——
STATUS_NORMAL = "正常"
STATUS_VOID = "作废"
STATUS_RED = "红冲"
STATUS_NOT_FOUND = "查无此票"
STATUS_FAIL = "查验失败"

# —— 总体结论 ——
VERIFY_PASS = "通过"
VERIFY_WARN = "预警"
VERIFY_BLOCK = "拦截"


def _mock_verify(invoice: dict[str, Any]) -> dict[str, Any]:
    """Mock 查验：基于发票号码关键字返回模拟结果（供开发/测试）。

    规则（演示用，可自由调整）：
      - 号码含 "VOID"  → 作废
      - 号码含 "RED"   → 红冲
      - 号码含 "FAKE"  → 查无此票
      - 其余           → 正常
    """
    invoice_no = str(invoice.get("发票号码", "")).strip().upper()
    if "VOID" in invoice_no:
        return {"查验状态": STATUS_VOID, "code": "void"}
    if "RED" in invoice_no:
        return {"查验状态": STATUS_RED, "code": "red"}
    if "FAKE" in invoice_no:
        return {"查验状态": STATUS_NOT_FOUND, "code": "not_found"}
    return {"查验状态": STATUS_NORMAL, "code": "normal"}


# Provider 注册表（预留真实平台扩展点）
_PROVIDERS = {
    "mock": _mock_verify,
    # TODO(P2): 接入真实查验平台时，在此注册
    # "baiwang": _verify_via_baiwang,
    # "hangxin": _verify_via_hangxin,
}


def _resolve_provider() -> str:
    """解析当前启用的 Provider；无效值回退到 mock。"""
    provider = (INVOICE_VERIFY_PROVIDER or "mock").strip().lower()
    if provider not in _PROVIDERS:
        logger.warning("未知查验 Provider '%s'，回退到 mock", provider)
        return "mock"
    return provider


def _classify_conclusion(
    verify_status: str, block_on_fake: bool, block_on_error: bool
) -> str:
    """根据查验状态与拦截策略推导总体结论。"""
    if verify_status in (STATUS_VOID, STATUS_RED, STATUS_NOT_FOUND):
        return VERIFY_BLOCK if block_on_fake else VERIFY_WARN
    if verify_status == STATUS_FAIL:
        return VERIFY_BLOCK if block_on_error else VERIFY_WARN
    return VERIFY_PASS


def verify_invoice(
    invoice: dict[str, Any],
    block_on_fake: bool = True,
    block_on_error: bool = False,
    provider: str | None = None,
) -> dict[str, Any]:
    """功能5：发票真伪查验。

    Args:
        invoice: 功能1 提取的发票数据
        block_on_fake: 查验为假时是否拦截（默认 True）
        block_on_error: 查验平台异常时是否拦截（默认 False）
        provider: 强制指定 Provider（测试用），默认读环境变量

    Returns:
        {
            "查验平台": str,
            "查验状态": str,
            "总体结论": str,         # 通过/预警/拦截
            "查验明细": dict,        # Provider 原始返回（脱敏）
            "查验摘要": str,
        }
    """
    provider_name = provider or _resolve_provider()
    verify_fn = _PROVIDERS.get(provider_name, _mock_verify)
    # 若注册表未命中（回退到 mock），则 provider_name 也回退到 mock
    if verify_fn is _mock_verify and provider_name not in _PROVIDERS:
        provider_name = "mock"

    logger.info("▶ 功能5: 发票查验 via provider=%s", provider_name)
    try:
        raw = verify_fn(invoice)
    except Exception as e:  # 真实平台异常兜底
        logger.error("查验异常: %s", e)
        return {
            "查验平台": provider_name,
            "查验状态": STATUS_FAIL,
            "总体结论": _classify_conclusion(STATUS_FAIL, block_on_fake, block_on_error),
            "查验明细": {"_error": str(e)},
            "查验摘要": f"查验平台调用异常：{e}",
        }

    status = raw.get("查验状态", STATUS_NORMAL)
    conclusion = _classify_conclusion(status, block_on_fake, block_on_error)
    summary = (
        f"查验平台[{provider_name}]返回：{status}"
        + ("，判定为假票并拦截" if conclusion == VERIFY_BLOCK else "")
    )
    return {
        "查验平台": provider_name,
        "查验状态": status,
        "总体结论": conclusion,
        "查验明细": raw,
        "查验摘要": summary,
    }
