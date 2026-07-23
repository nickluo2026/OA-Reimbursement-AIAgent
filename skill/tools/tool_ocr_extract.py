"""功能1：OCR 提取发票全部内容

流程：
    - PDF → PyMuPDF 提取文本 → DeepSeek Function Call 结构化输出
    - 图片 → DeepSeek Vision API（base64 编码）→ 结构化输出
"""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import Any

from ..schemas.invoice_schema import EXTRACT_INVOICE_TOOL
from ..utils.http_client import call_deepseek_function
from ..utils.pdf_extractor import extract_pdf_text

logger = logging.getLogger(__name__)

# 支持的图片类型
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}

SYSTEM_PROMPT = (
    "你是发票数据提取助手。\n"
    "\n"
    "工作流程：\n"
    "1. 从用户提供的发票文本中精确提取全部字段\n"
    "2. 将「价税合计小写」数值填到「发票金额」字段\n"
    "3. 商品明细逐项提取，放入「商品明细」数组\n"
    "4. 必须调用 extract_invoice 函数返回结构化结果\n"
    '5. 无数据的字段填空字符串 ""，无数据的数字填 0\n'
    "6. 不要编造未在文本中出现的字段值"
)

VISION_SYSTEM_PROMPT = (
    "你是发票数据提取助手，擅长从发票图片中识别和提取信息。\n"
    "\n"
    "工作流程：\n"
    "1. 仔细观察用户提供的发票图片，精确提取全部可见字段\n"
    "2. 将「价税合计小写」数值填到「发票金额」字段\n"
    "3. 商品明细逐项提取，放入「商品明细」数组\n"
    "4. 必须调用 extract_invoice 函数返回结构化结果\n"
    '5. 无数据的字段填空字符串 ""，无数据的数字填 0\n'
    "6. 不要编造未在图片中出现的字段值\n"
    "7. 注意区分金额、税额、价税合计等不同数字"
)


def _is_image_file(file_path: str) -> bool:
    """判断文件是否为图片类型"""
    ext = Path(file_path).suffix.lower()
    return ext in IMAGE_EXTENSIONS


def _encode_image_base64(image_path: str) -> str:
    """将图片文件编码为 base64 data URI"""
    with open(image_path, "rb") as f:
        img_data = base64.b64encode(f.read()).decode("utf-8")
    ext = Path(image_path).suffix.lower().lstrip(".")
    if ext == "jpg":
        ext = "jpeg"
    return f"data:image/{ext};base64,{img_data}"


def ocr_extract_invoice(file_path: str) -> dict[str, Any]:
    """功能1：从发票文件中 OCR 提取全部内容

    Args:
        file_path: 发票文件路径（支持 PDF / JPG / PNG 等）

    Returns:
        结构化发票数据字典，包含发票头、购销方、金额明细、商品明细等；
        若失败，返回包含 ``_error`` 键的错误字典。
    """
    # 路由：图片走 Vision API，PDF 走文本提取 + Function Call
    if _is_image_file(file_path):
        return _ocr_extract_image(file_path)

    return _ocr_extract_pdf(file_path)


def _ocr_extract_pdf(pdf_path: str) -> dict[str, Any]:
    """PDF OCR：PyMuPDF 提取文本 → DeepSeek Function Call"""
    # ① 提取 PDF 文本
    try:
        raw_text = extract_pdf_text(pdf_path)
    except FileNotFoundError as e:
        return {"_error": str(e)}
    except ImportError as e:
        return {"_error": f"依赖缺失: {e}"}
    except RuntimeError as e:
        # 扫描件（无文本层），降级尝试 Vision API
        logger.warning("PDF 无文本层，尝试降级为 Vision API 处理: %s", e)
        return _ocr_extract_image(pdf_path)
    except Exception as e:
        return {"_error": f"PDF 读取失败: {e}"}

    logger.info("提取到 %d 字符, 调用 DeepSeek Function Call ...", len(raw_text))

    # ② 调用 DeepSeek Function Call
    user_content = f"发票文本：\n{raw_text}"
    result = call_deepseek_function(
        system_prompt=SYSTEM_PROMPT,
        user_content=user_content,
        tools=EXTRACT_INVOICE_TOOL,
        call_type="发票OCR提取",
    )

    return result


def _ocr_extract_image(image_path: str) -> dict[str, Any]:
    """图片 OCR：通过 DeepSeek Vision API 识别发票图片"""
    import requests

    from ..config import (
        DEEPSEEK_VISION_MODEL,
        MAX_TOKENS,
        REQUEST_TIMEOUT,
        TEMPERATURE,
        get_deepseek_settings,
    )
    from ..utils.http_client import _get_headers, _now_ms

    logger.info("调用 DeepSeek Vision API 识别图片: %s", image_path)

    # DeepSeek 大模型已停用（系统配置）→ 无法执行图片 OCR
    settings = get_deepseek_settings()
    if not settings["enabled"]:
        from ..config import DEEPSEEK_DISABLED_MSG

        return {
            "_disabled": True,
            "_warning": DEEPSEEK_DISABLED_MSG,
        }

    try:
        img_data_uri = _encode_image_base64(image_path)
    except FileNotFoundError as e:
        return {"_error": str(e)}
    except Exception as e:
        return {"_error": f"图片编码失败: {e}"}

    payload = {
        "model": DEEPSEEK_VISION_MODEL,
        "messages": [
            {"role": "system", "content": VISION_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": img_data_uri},
                    },
                    {
                        "type": "text",
                        "text": "请识别并提取这张发票的全部字段信息，"
                        "调用 extract_invoice 函数返回结果。",
                    },
                ],
            },
        ],
        "tools": EXTRACT_INVOICE_TOOL,
        "tool_choice": "auto",
        "max_tokens": MAX_TOKENS,
        "temperature": TEMPERATURE,
    }

    start = _now_ms()
    try:
        headers = _get_headers(settings["api_key"])
        resp = requests.post(
            settings["base_url"],
            headers=headers,
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()

        message = data["choices"][0]["message"]

        if "tool_calls" in message and message["tool_calls"]:
            tool_call = message["tool_calls"][0]
            func_args_str = tool_call["function"]["arguments"]
            logger.info("Vision API 成功提取发票数据")
            _record_vision_usage(data, "成功", start)
            return json.loads(func_args_str)

        # 兜底
        content = message.get("content", "").strip()
        _record_vision_usage(data, "成功", start)
        return {"_warning": "Vision API 未调用工具函数", "_raw": content}

    except json.JSONDecodeError:
        _record_vision_usage(None, "失败", start)
        return {"_error": "Vision API 返回的 JSON 解析失败"}
    except requests.exceptions.Timeout:
        _record_vision_usage(None, "失败", start)
        return {"_error": "Vision API 调用超时"}
    except Exception as e:
        logger.error("Vision API 调用异常: %s", e)
        _record_vision_usage(None, "失败", start)
        return {"_error": f"Vision API 调用失败: {e}"}


def _record_vision_usage(data: dict | None, status: str, start: int) -> None:
    """记录 Vision API 图片识别的用量（尽力而为）。"""
    try:
        from ..config import DEEPSEEK_VISION_MODEL
        from ..utils.admin_store import record_api_usage
        from ..utils.http_client import _now_ms

        usage = (data or {}).get("usage", {}) or {}
        record_api_usage(
            call_type="Vision API",
            model=DEEPSEEK_VISION_MODEL,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            latency_ms=_now_ms() - start,
            status=status,
        )
    except Exception:  # pragma: no cover
        pass
