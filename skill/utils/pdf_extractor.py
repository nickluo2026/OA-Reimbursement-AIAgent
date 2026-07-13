"""PDF 文本提取工具（PyMuPDF 封装）

复用自 deepseek_ocr_invoice.py 的 extract_pdf_text 逻辑。
"""

from __future__ import annotations

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None


def extract_pdf_text(pdf_path: str) -> str:
    """用 PyMuPDF 提取 PDF 全部文本

    Args:
        pdf_path: PDF 文件路径

    Returns:
        各页文本，以 ``\\n--- 分页 ---\\n`` 分隔

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
        t = page.get_text()
        if t.strip():
            pages.append(t)
    doc.close()

    if not pages:
        raise RuntimeError("PDF 无可提取文字（可能是扫描件），需要 OCR 图片识别服务")

    return "\n--- 分页 ---\n".join(pages)
