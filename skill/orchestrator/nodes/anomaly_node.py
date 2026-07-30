"""异常检测节点（前置拦截）+ 方案A 并行分类限额

封装工具 ``detect_anomaly``，拦截时置 ``final_status=BLOCK``，由条件边提前结束。

方案A 优化：当发票金额 > 100 时，异常检测与分类限额彼此独立，本节点用线程池
并行执行 ``detect_anomaly`` 与 ``classify_and_check_limit``，使两次 LLM 调用的
网络等待重叠，关键路径从「两者耗时之和」降为「两者最大值」。分类限额的落库与
最终定级交由 ``post_check`` 合并节点统一处理，以保证「异常拦截优先」。
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from typing import Any

from ...tools.tool_anomaly_check import detect_anomaly
from ...utils.db_store import save_ai_check_result, update_ai_status
from ...config import SMALL_AMOUNT_THRESHOLD
from ..state import CheckStatus, ReimbursementState
from ..nodes import classify_node as classify_node_mod

logger = logging.getLogger(__name__)


def _run_classify(ocr_result: dict) -> dict:
    """在线程池中调用分类限额工具（经由模块引用，确保测试 patch 生效）。"""
    return classify_node_mod.classify_and_check_limit(invoice=ocr_result)


def _run_in_ctx(ctx, fn, *args, **kwargs):
    """在捕获的上下文中执行 fn，确保 request_id 等 contextvar 传播进子线程。

    方案A 使用线程池并行异常检测与分类限额；ThreadPoolExecutor 默认不复制
    上游上下文变量，会导致并行分支内的 request_id 丢失（用量统计无法按单号
    关联、日志/落库缺上下文）。此处用主线程上下文的副本运行，修复该缺陷。

    注意：必须为每个任务使用独立的上下文副本，否则同一 Context 对象无法被
    两个 worker 线程同时进入（RuntimeError: already entered）。
    """
    return ctx.copy().run(fn, *args, **kwargs)


def anomaly_node(state: ReimbursementState) -> dict[str, Any]:
    """功能3：异常输入检查（前置拦截）；金额较大时并行执行分类限额校验。"""
    ocr_result = state.get("ocr_result") or {}
    request_id = state.get("request_id")
    invoice_amount = ocr_result.get("发票金额", 0)
    large = isinstance(invoice_amount, (int, float)) and invoice_amount > SMALL_AMOUNT_THRESHOLD

    # —— 并行分支（方案A）：异常检测 ‖ 分类限额 ——
    if large:
        logger.info("▶ 功能3+功能2: 异常检测 ‖ 分类限额 并行 (金额 %.2f)", invoice_amount)
        ctx = copy_context()  # 捕获主线程上下文（含 request_id），供子线程继承
        with ThreadPoolExecutor(max_workers=2) as ex:
            f_anomaly = ex.submit(
                _run_in_ctx,
                ctx,
                detect_anomaly,
                invoice=ocr_result,
                apply_amount=state.get("apply_amount"),
                apply_date=state.get("apply_date"),
            )
            f_classify = ex.submit(_run_in_ctx, ctx, _run_classify, ocr_result)
            anomaly_result = f_anomaly.result()
            classify_result = f_classify.result()
    else:
        logger.info("▶ 功能3: 异常输入检查")
        anomaly_result = detect_anomaly(
            invoice=ocr_result,
            apply_amount=state.get("apply_amount"),
            apply_date=state.get("apply_date"),
        )
        classify_result = None

    conclusion = anomaly_result.get("总体结论", "通过")
    logger.info("✓ 异常检测完成, 总体结论: %s", conclusion)

    # 拦截：置 BLOCK 状态，由条件边提前结束（分类限额结论丢弃，不落库）
    if conclusion == "拦截":
        summary = (
            f"异常检查拦截: {anomaly_result.get('检查摘要', '存在严重异常')}。"
            f"发票金额 {invoice_amount} 元，未执行分类限额校验。"
        )
        if request_id:
            try:
                update_ai_status(request_id, "拦截")
                save_ai_check_result(request_id, "异常检测", "拦截", anomaly_result)
            except Exception as e:
                logger.warning("持久化异常（非致命）: %s", e)
        return {
            "anomaly_result": anomaly_result,
            "classify_result": classify_result,
            "final_status": CheckStatus.BLOCK,
            "summary": summary,
        }

    # 非拦截：保存异常检测结果；分类限额落库与定级交由 post_check 合并节点
    if request_id:
        try:
            save_ai_check_result(request_id, "异常检测", conclusion, anomaly_result)
        except Exception as e:
            logger.warning("持久化异常（非致命）: %s", e)

    return {"anomaly_result": anomaly_result, "classify_result": classify_result}
