"""行程单 Agent

对应 design.md §16.5 / ADR-008。通过 ``@register_agent`` 注册到注册中心，
由 ``itinerary_node`` 委托调用，实现编排与业务解耦。

单 Agent 内部编排 3 个工具：
    1. ocr_extract_itinerary  — OCR 提取行程明细
    2. detect_itinerary_anomaly — 异常检测（拦截则早退）
    3. verify_itinerary         — 行程合理性校验

与发票分支的 route_after_anomaly 语义一致，但行程单无小额免审分支，
故在 Agent 内部完成拦截早退，节点出口直接连 END。
"""

from __future__ import annotations

import logging
from typing import Any

from ..orchestrator.registry import register_agent
from ..orchestrator.state import CheckStatus
from ..tools.tool_itinerary_anomaly import detect_itinerary_anomaly
from ..tools.tool_itinerary_ocr import ocr_extract_itinerary
from ..tools.tool_itinerary_verify import verify_itinerary
from ..utils.db_store import (
    save_ai_check_result,
    save_invoice,
    save_reimbursement,
    update_ai_status,
)
from .base_agent import AgentMeta, BaseAgent

logger = logging.getLogger(__name__)


@register_agent
class ItineraryAgent(BaseAgent):
    """行程单 Agent：OCR 提取 → 异常检测 → 合理性校验"""

    def meta(self) -> AgentMeta:
        return AgentMeta(
            name="itinerary",
            ticket_type="行程单",
            description="行程单 OCR 提取、异常检测与合理性校验",
            input_schema=dict,
            output_schema=dict,
        )

    def run(self, state: dict[str, Any]) -> dict[str, Any]:
        """行程单 Agent 主逻辑：OCR 提取 → 异常检测 → 合理性校验"""
        pdf_path = state["pdf_path"]
        apply_amount = state.get("apply_amount")
        apply_date = state.get("apply_date", "")
        request_id = state.get("request_id")

        # ── 步骤1：OCR 提取行程单 ──
        logger.info("▶ 行程单 Agent 步骤1: OCR 提取行程明细 (%s)", pdf_path)
        ocr_result = ocr_extract_itinerary(pdf_path)

        if "_error" in ocr_result:
            logger.warning("✗ 行程单 OCR 失败: %s", ocr_result["_error"])
            return {
                "ocr_result": ocr_result,
                "final_status": CheckStatus.ERROR,
                "summary": f"行程单 OCR 提取失败: {ocr_result['_error']}",
                "errors": [ocr_result["_error"]],
            }

        total_amount = ocr_result.get("总金额_元")
        trip_count = len(ocr_result.get("行程详情") or [])
        logger.info("✓ 行程单 OCR 完成, 总金额: %s, 行程数: %d", total_amount, trip_count)

        # ── 持久化：保存报销单 + 行程单 OCR 结果 ──
        if request_id and apply_amount is not None:
            try:
                save_reimbursement(
                    request_id=request_id,
                    employee_id=state.get("employee_id", "unknown"),
                    apply_amount=apply_amount,
                    apply_date=apply_date,
                    reason=state.get("reason", ""),
                    expense_category=state.get("expense_category", ""),
                )
                # 行程单复用 InvoiceRecord 表存储 OCR 原始结果
                save_invoice(ocr_result, request_id, pdf_path)
                save_ai_check_result(
                    request_id,
                    "行程单OCR提取",
                    "通过",
                    {
                        "总金额_元": total_amount,
                        "行程数": trip_count,
                    },
                )
            except Exception as e:
                logger.warning("持久化异常（非致命）: %s", e)

        # ── 步骤2：异常检测（前置拦截）──
        logger.info("▶ 行程单 Agent 步骤2: 异常检测")
        anomaly_result = detect_itinerary_anomaly(
            itinerary=ocr_result,
            apply_amount=apply_amount,
            apply_date=apply_date,
        )
        conclusion = anomaly_result.get("总体结论", "通过")
        logger.info("✓ 行程单异常检测完成, 总体结论: %s", conclusion)

        if conclusion == "拦截":
            summary = (
                f"行程单异常检测拦截: {anomaly_result.get('检查摘要', '存在严重异常')}。"
                f"总金额 {total_amount} 元，未执行合理性校验。"
            )
            if request_id:
                try:
                    update_ai_status(request_id, "拦截")
                    save_ai_check_result(request_id, "行程单异常检测", "拦截", anomaly_result)
                except Exception as e:
                    logger.warning("持久化异常（非致命）: %s", e)
            return {
                "ocr_result": ocr_result,
                "anomaly_result": anomaly_result,
                "final_status": CheckStatus.BLOCK,
                "summary": summary,
            }

        # 非拦截：保存检测结果
        if request_id:
            try:
                save_ai_check_result(request_id, "行程单异常检测", conclusion, anomaly_result)
            except Exception as e:
                logger.warning("持久化异常（非致命）: %s", e)

        # ── 步骤3：行程合理性校验 ──
        logger.info("▶ 行程单 Agent 步骤3: 合理性校验")
        itinerary_result = verify_itinerary(
            itinerary=ocr_result,
            apply_amount=apply_amount,
        )
        verify_conclusion = itinerary_result.get("校验结论", "通过")
        logger.info("✓ 行程单合理性校验完成, 校验结论: %s", verify_conclusion)

        if request_id:
            try:
                save_ai_check_result(
                    request_id, "行程单合理性校验", verify_conclusion, itinerary_result
                )
            except Exception as e:
                logger.warning("持久化异常（非致命）: %s", e)

        # ── 综合结论 ──
        if verify_conclusion == "拦截":
            final_status = CheckStatus.BLOCK
        elif verify_conclusion == "预警":
            final_status = CheckStatus.WARNING
        else:
            final_status = CheckStatus.PASS

        summary = (
            f"行程单校验完成：异常检测「{conclusion}」，合理性校验「{verify_conclusion}」。"
            f"总金额 {total_amount} 元，行程 {trip_count} 段。"
        )

        if request_id:
            try:
                update_ai_status(request_id, verify_conclusion)
            except Exception as e:
                logger.warning("持久化异常（非致命）: %s", e)

        return {
            "ocr_result": ocr_result,
            "anomaly_result": anomaly_result,
            "itinerary_result": itinerary_result,
            "final_status": final_status,
            "summary": summary,
        }
