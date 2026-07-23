"""PDF 文本提取工具（PyMuPDF 封装）

复用自 deepseek_ocr_invoice.py 的 extract_pdf_text 逻辑。
增强：按坐标排序文本块，解决电子发票双栏布局导致的标签与值分离问题。
"""

from __future__ import annotations

import re

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None


def _clean_block_text(text: str) -> str:
    """清理块内文本：合并连续空白，移除首尾换行。"""
    return re.sub(r"[ \t]{2,}", " ", text).strip("\n\r")


def _extract_sorted_page_text(page) -> str:
    """按阅读顺序（先上后下、同排先左后右）提取单页文本。

    PyMuPDF 原生 ``page.get_text()`` 按 PDF 内容流顺序输出，不保证阅读顺序。
    电子发票常采用双栏布局（左边标签、右边值），原生顺序会导致「发票号码：\\n
    开票日期：\\n…26337000000651573239\\n2026年07月07日…」标签与值分离。

    本方法用 ``get_text("blocks")`` 获取带坐标的文本块，按 (y, x) 排序后拼接，
    使相邻区域的文本在输出中也相邻，大幅改善 DeepSeek Function Call 的提取准确率。
    """
    blocks = page.get_text("blocks")
    if not blocks:
        return ""

    # 过滤空块，仅保留含文字内容的块
    text_blocks = [(b[1], b[0], b[4]) for b in blocks if isinstance(b[4], str) and b[4].strip()]
    if not text_blocks:
        return ""

    # 按 y 坐标排序（同排则按 x 排序），符合自上而下、自左而右的阅读习惯
    sorted_blocks = sorted(text_blocks, key=lambda item: (item[0], item[1]))

    lines = [_clean_block_text(t) for _, _, t in sorted_blocks]
    return "\n".join(lines)


def extract_pdf_text(pdf_path: str) -> str:
    """用 PyMuPDF 提取 PDF 全部文本

    Args:
        pdf_path: PDF 文件路径

    Returns:
        各页文本，以 ``\\n--- 分页 ---\\n`` 分隔，
        每页文本块按阅读顺序（坐标）排序。

    Raises:
        ImportError: pymupdf 未安装
        FileNotFoundError: 文件不存在
        RuntimeError: PDF 无可提取文字（扫描件）
    """
    if fitz is None:
        raise ImportError("pymupdf 未安装，请运行: pip install pymupdf")

    import os

    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"文件不存在: {pdf_path}")

    doc = fitz.open(pdf_path)
    pages: list[str] = []
    for page in doc:
        t = _extract_sorted_page_text(page)
        if t.strip():
            pages.append(t)
    doc.close()

    if not pages:
        raise RuntimeError("PDF 无可提取文字（可能是扫描件），需要 OCR 图片识别服务")

    return "\n--- 分页 ---\n".join(pages)
