"""[P1/ADR-006] 票据文件存储抽象：本地目录（默认）/ S3 兼容对象存储。

上传的票据原件默认持久化保留在 uploads/invoices/，满足「票据影像可追溯」合规要求。
开启归档后，还会将原件复制到归档后端（本地/S3）作为冗余备份。

环境变量：
    OA_ARCHIVE_UPLOADS   1 开启归档（默认 0，开启后额外复制到归档后端）
    OA_STORAGE_BACKEND   local（默认）/ s3
    OA_STORAGE_DIR       本地归档目录（默认 <项目根>/uploads/archive）
    OA_S3_ENDPOINT       S3 兼容端点（阿里云 OSS / 腾讯云 COS / MinIO 均可）
    OA_S3_BUCKET         桶名
    OA_S3_ACCESS_KEY / OA_S3_SECRET_KEY
    OA_S3_PREFIX         对象 key 前缀（默认 invoices/）
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

_BASE_DIR = Path(__file__).resolve().parent.parent.parent

# ── 发票原件持久化目录 (默认保存，用于影像展示) ──
INVOICE_DIR = Path(os.environ.get("OA_INVOICE_DIR", str(_BASE_DIR / "uploads" / "invoices")))
INVOICE_DIR.mkdir(parents=True, exist_ok=True)


def archive_enabled() -> bool:
    return os.environ.get("OA_ARCHIVE_UPLOADS", "0").strip().lower() in ("1", "true", "yes", "on")


def archive_file(local_path: str | Path, key: str) -> str:
    """将本地文件归档到配置的后端，返回归档位置标识。

    - local 后端：返回归档文件绝对路径
    - s3 后端：返回 "s3://<bucket>/<key>"
    失败时抛出异常，由调用方决定是否阻断（当前调用方仅记日志不阻断）。
    """
    backend = os.environ.get("OA_STORAGE_BACKEND", "local").strip().lower()
    if backend == "s3":
        return _archive_s3(Path(local_path), key)
    return _archive_local(Path(local_path), key)


def _archive_local(local_path: Path, key: str) -> str:
    target_dir = Path(os.environ.get("OA_STORAGE_DIR", str(_BASE_DIR / "uploads" / "archive")))
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / key
    shutil.copy2(local_path, target)
    logger.info("票据已归档(local): %s", target)
    return str(target)


def _archive_s3(local_path: Path, key: str) -> str:
    """S3 兼容对象存储归档（boto3 按需导入，未安装时报错提示）。"""
    try:
        import boto3  # type: ignore
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("使用 s3 存储后端需安装 boto3：pip install boto3") from e

    bucket = os.environ.get("OA_S3_BUCKET", "")
    if not bucket:
        raise RuntimeError("使用 s3 存储后端必须设置 OA_S3_BUCKET")
    prefix = os.environ.get("OA_S3_PREFIX", "invoices/")
    object_key = f"{prefix}{key}"

    client = boto3.client(
        "s3",
        endpoint_url=os.environ.get("OA_S3_ENDPOINT") or None,
        aws_access_key_id=os.environ.get("OA_S3_ACCESS_KEY") or None,
        aws_secret_access_key=os.environ.get("OA_S3_SECRET_KEY") or None,
    )
    # ServerSideEncryption：满足「票据 AES-256 加密存储」合规项
    client.upload_file(
        str(local_path), bucket, object_key, ExtraArgs={"ServerSideEncryption": "AES256"}
    )
    location = f"s3://{bucket}/{object_key}"
    logger.info("票据已归档(s3): %s", location)
    return location


# ── 影像渲染：PyMuPDF 渲染 PDF/图片 → 缩略图 + 整页 PNG ──
THUMB_DPI = 36  # 缩略图低分辨率
THUMB_WIDTH = 120  # 缩略图目标宽度
THUMB_HEIGHT = 160  # 缩略图目标高度
PAGE_DPI = 120  # 整页中分辨率
PAGE_WIDTH = 1020  # 整页渲染目标宽度（按比例缩放）


def _render_pdf_pages(src_path: Path, out_prefix: str, out_dir: Path) -> int:
    """用 PyMuPDF 把 PDF 每一页渲染成 PNG。

    - 第 1 页：额外生成缩略图 ``{prefix}_t.png``（低 DPI）
    - 每页：生成 ``{prefix}_p{1..N}.png``（中 DPI）
    - 返回总页数
    """
    try:
        import fitz  # type: ignore
    except ImportError:
        logger.warning("PyMuPDF 不可用，无法渲染 PDF 影像: %s", src_path)
        return 0

    doc = fitz.open(str(src_path))
    page_count = doc.page_count
    if page_count == 0:
        doc.close()
        return 0

    # 缩略图：第 1 页，极低 DPI
    page = doc[0]
    pix = page.get_pixmap(dpi=THUMB_DPI)
    _save_pixmap_rgb(pix, out_dir / f"{out_prefix}_t.png")

    # 整页图：逐页渲染
    for i in range(page_count):
        if i > 0:
            page = doc[i]
        pix = page.get_pixmap(dpi=PAGE_DPI)
        _save_pixmap_rgb(pix, out_dir / f"{out_prefix}_p{i + 1}.png")

    doc.close()
    return page_count


def _save_pixmap_rgb(pixmap, dest: Path) -> None:
    """将 PyMuPDF pixmap 保存为 RGB PNG（处理 CMYK/Alpha 转换）。"""
    import fitz  # type: ignore

    n = pixmap.n
    alpha = pixmap.alpha
    # 检测是否为非 RGB 空间（如 CMYK, n=4 且无 alpha）
    if pixmap.colorspace and n - alpha > 3:
        pixmap = fitz.Pixmap(fitz.csRGB, pixmap)
    # 若带 alpha 但不是 RGBA，转为 RGB
    if alpha:
        pixmap = fitz.Pixmap(fitz.csRGB, pixmap)
    pixmap.save(str(dest))


def _render_image(src_path: Path, out_prefix: str, out_dir: Path) -> int:
    """图片文件（JPG/PNG）：直接缩放为缩略图，整页图即为原图副本。返回 1（单页）。"""
    try:
        from PIL import Image  # type: ignore

        img = Image.open(str(src_path))
    except ImportError:
        # PIL 不可用：退化为文件复制
        logger.warning("PIL 不可用，影像图片将直接复制: %s", src_path)
        shutil.copy2(src_path, out_dir / f"{out_prefix}_p1.png")
        shutil.copy2(src_path, out_dir / f"{out_prefix}_t.png")
        return 1

    # 缩略图
    thumb = img.copy()
    thumb.thumbnail((THUMB_WIDTH, THUMB_HEIGHT), Image.LANCZOS)
    thumb.save(out_dir / f"{out_prefix}_t.png", "PNG")
    # 整页（保持比例缩放到 PAGE_WIDTH 宽度）
    page = img.copy()
    w, h = page.size
    if w > PAGE_WIDTH:
        ratio = PAGE_WIDTH / w
        page = page.resize((PAGE_WIDTH, int(h * ratio)), Image.LANCZOS)
    page.save(out_dir / f"{out_prefix}_p1.png", "PNG")
    return 1


def render_invoice_images(src_path: str, request_id: str, idx: int, out_dir: Path = None) -> dict:
    """为发票原件渲染缩略图与整页 PNG，输出到 out_dir。

    Args:
        src_path: 发票原件路径（PDF/JPG/PNG）
        request_id: 报销单号
        idx: 该单第几张发票（从 0 开始）
        out_dir: 输出目录，默认 INVOICE_DIR

    Returns:
        {'page_count': int, 'has_image': True}，渲染失败返回 {'page_count': 0, 'has_image': False}
    """
    if out_dir is None:
        out_dir = INVOICE_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    src = Path(src_path)
    if not src.exists():
        logger.warning("源文件不存在，无法渲染影像: %s", src_path)
        return {"page_count": 0, "has_image": False}

    out_prefix = f"{request_id}_{idx}"
    ext = src.suffix.lower()

    try:
        if ext == ".pdf":
            page_count = _render_pdf_pages(src, out_prefix, out_dir)
        else:
            page_count = _render_image(src, out_prefix, out_dir)
    except Exception:
        logger.exception("渲染发票影像失败 request_id=%s idx=%d", request_id, idx)
        return {"page_count": 0, "has_image": False}

    logger.info(
        "发票影像渲染完成 request_id=%s idx=%d pages=%d",
        request_id,
        idx,
        page_count,
    )
    return {"page_count": page_count, "has_image": page_count > 0}
