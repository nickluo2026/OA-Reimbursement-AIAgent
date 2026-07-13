"""功能1：OCR 提取 — 单元测试"""

import os
import tempfile
from unittest.mock import patch, MagicMock

import pytest

from skill.tools.tool_ocr_extract import ocr_extract_invoice


class TestOcrExtractInvoice:
    """OCR 提取单元测试"""

    def test_file_not_found(self):
        """文件不存在应返回错误"""
        result = ocr_extract_invoice("/nonexistent/file.pdf")
        assert "_error" in result

    @patch("skill.tools.tool_ocr_extract.extract_pdf_text")
    @patch("skill.tools.tool_ocr_extract.call_deepseek_function")
    def test_successful_extraction(self, mock_ds, mock_pdf):
        """正常提取流程"""
        mock_pdf.return_value = "发票号码: 12345678\n金额: 300.00"
        mock_ds.return_value = {
            "发票号码": "12345678",
            "发票金额": 300.00,
            "开票日期": "2026-06-01",
            "购买方名称": "XX公司",
            "销售方名称": "YY公司",
            "商品明细": [],
        }
        result = ocr_extract_invoice("test.pdf")
        assert "_error" not in result
        assert result["发票号码"] == "12345678"
        assert result["发票金额"] == 300.00

    @patch("skill.tools.tool_ocr_extract.extract_pdf_text")
    def test_pdf_read_error(self, mock_pdf):
        """PDF 读取异常应返回错误"""
        mock_pdf.side_effect = RuntimeError("PDF 无可提取文字")
        result = ocr_extract_invoice("test.pdf")
        assert "_error" in result

    @patch("skill.tools.tool_ocr_extract.extract_pdf_text")
    @patch("skill.tools.tool_ocr_extract.call_deepseek_function")
    def test_deepseek_failure_returns_error(self, mock_ds, mock_pdf):
        """DeepSeek 调用失败应返回错误"""
        mock_pdf.return_value = "some text"
        mock_ds.return_value = {"_error": "API 超时"}
        result = ocr_extract_invoice("test.pdf")
        assert "_error" in result
