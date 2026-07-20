"""DeepSeek API HTTP 客户端

封装 Function Call 调用逻辑，供三个功能工具复用。
"""

from __future__ import annotations

import json
import logging
from typing import Any

import requests

from ..config import (
    MAX_TOKENS,
    REQUEST_TIMEOUT,
    TEMPERATURE,
    get_deepseek_settings,
)

logger = logging.getLogger(__name__)


def _get_headers(api_key: str) -> dict[str, str]:
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY 未配置，请在 .env 或系统配置中设置")
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def call_deepseek_function(
    system_prompt: str,
    user_content: str,
    tools: list[dict[str, Any]],
    tool_choice: str = "auto",
    call_type: str | None = None,
) -> dict[str, Any]:
    """调用 DeepSeek Chat API 并解析 Function Call 结果

    Args:
        system_prompt: 系统提示词
        user_content: 用户消息内容
        tools: Function Call 工具定义列表
        tool_choice: 工具选择策略，默认 "auto"
        call_type: 调用类型（用于用量统计），如 "发票OCR提取" / "异常检测" 等

    Returns:
        模型通过 tool_calls 返回的结构化参数字典；
        若模型未调用工具，返回 {"_warning": ..., "_raw": ...}；
        若调用失败，返回 {"_error": ...}；
        若 DeepSeek 大模型被系统配置停用，返回 {"_disabled": True, "_warning": ...}，
        由各调用方降级处理（规则引擎兜底 / 提示用户启用）。
    """
    # 运行时凭据与开关来自系统配置（管理员可覆盖环境变量）
    settings = get_deepseek_settings()
    if not settings["enabled"]:
        logger.info(
            "DeepSeek 大模型已停用（系统配置 ds_enabled=False），跳过本次调用: %s", call_type
        )
        return {
            "_disabled": True,
            "_warning": "DeepSeek 大模型已停用（系统配置已关闭 AI 校验），本次调用跳过",
        }

    api_key = settings["api_key"]
    base_url = settings["base_url"]
    model = settings["model"]

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "tools": tools,
        "tool_choice": tool_choice,
        "max_tokens": MAX_TOKENS,
        "temperature": TEMPERATURE,
    }

    start = _now_ms()
    try:
        resp = requests.post(
            base_url,
            headers=_get_headers(api_key),
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()

        message = data["choices"][0]["message"]

        # 用量统计：解析 token 消耗（尽力而为，不影响主流程）
        usage = data.get("usage", {}) or {}
        _record_usage(
            call_type,
            model,
            usage.get("prompt_tokens", 0),
            usage.get("completion_tokens", 0),
            _now_ms() - start,
            "成功",
        )

        # 优先解析 tool_calls
        if "tool_calls" in message and message["tool_calls"]:
            tool_call = message["tool_calls"][0]
            func_name = tool_call["function"]["name"]
            func_args_str = tool_call["function"]["arguments"]
            logger.info("模型调用了工具: %s", func_name)
            result = json.loads(func_args_str)
            return result

        # 兜底：模型未调用工具
        content = message.get("content", "").strip()
        return {
            "_warning": "模型未调用工具函数，返回纯文本",
            "_raw": content,
        }

    except json.JSONDecodeError:
        logger.error("工具参数 JSON 解析失败")
        _record_usage(call_type, model, 0, 0, _now_ms() - start, "失败")
        return {"_error": "工具参数 JSON 解析失败"}
    except requests.exceptions.Timeout:
        logger.error("DeepSeek API 调用超时")
        _record_usage(call_type, model, 0, 0, _now_ms() - start, "失败")
        return {"_error": "DeepSeek API 调用超时"}
    except Exception as e:
        logger.error("DeepSeek API 调用异常: %s", e)
        _record_usage(call_type, model, 0, 0, _now_ms() - start, "失败")
        return {"_error": str(e)}


def _now_ms() -> int:
    """返回当前毫秒时间戳（用于延迟统计）。"""
    from time import time

    return int(time() * 1000)


def _record_usage(
    call_type: str | None,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    latency_ms: int,
    status: str,
) -> None:
    """记录一次 API 用量（仅在有 call_type 时，且尽力而为）。"""
    if not call_type:
        return
    try:
        from .admin_store import record_api_usage

        record_api_usage(
            call_type=call_type,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=latency_ms,
            status=status,
        )
    except Exception:  # pragma: no cover - 用量统计失败不应影响主流程
        pass
