"""单张票据端到端 AI 识别与校验性能测试（需求 N3）

验证方式：用固定延迟 stub 替换底层 DeepSeek 客户端
（skill.utils.http_client 的 call_deepseek_function / call_deepseek_vision 在各工具模块中的引用），
每个底层 AI 调用注入固定延迟，测量 ``run_reimbursement_skill`` 从调用到返回的端到端总耗时。

覆盖三种路径（贴近真机路由）：
  - pdf         ：文本层 PDF → 文本管线（3 次 AI 调用：OCR → 异常检测 → 分类限额）
  - image_text  ：图片 + 本地 OCR 可用 → 本地 OCR 抽文本 → 文本管线（3 次 AI 调用）
  - image_vision：图片 + 本地 OCR 不可用 → DeepSeek Vision 降级（4 次 AI 调用：
                   Vision 首轮 + 聚焦重试 + 异常检测 + 分类限额）

发票分支当前为串行编排，端到端理论耗时 ≈ AI 调用次数 × 单点延迟 + 本地编排开销。

用法：
  pytest tests/perf_e2e_latency.py -s -v                 # 自动化验证（PDF + 图片三路径基线）
  python tests/perf_e2e_latency.py [AI延迟ms] [次数]      # 默认 PDF 基线，打印耗时
  python tests/perf_e2e_latency.py --kind image_vision --delay 2500 --runs 11   # 真机慢路径基线
  python tests/perf_e2e_latency.py --kind image_text  --delay 2500 --runs 11   # 装本地 OCR 后对照
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path
from unittest.mock import patch

# 让脚本在 tests/ 目录下直接运行时也能 import skill 包
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# N3 阈值：端到端总耗时 P95 ≤ 10 秒
THRESHOLD_E2E_MS = 10000

# 真实发票样本（贴近真机图片路径）
REAL_IMAGE = (
    Path(__file__).resolve().parent.parent / "scripts" / "test_assets" / "new_invoice_test.png"
)

# 模拟「本地 OCR 可用」时返回的识别文本（仅用于 image_text 路径的路由触发）
_FAKE_OCR_TEXT = (
    "增值税电子普通发票\n"
    "发票号码: 12345678\n"
    "开票日期: 2026-06-01\n"
    "价税合计: 300.00\n"
    "销售方名称: YY公司\n"
    "购买方名称: XX公司\n"
)

OCR_SAMPLE = {
    "发票类型": "增值税普通发票",
    "发票号码": "12345678",
    "发票金额": 300.0,
    "开票日期": "2026-06-01",
    "销售方名称": "YY公司",
    "购买方名称": "XX公司",
}
# Vision 首轮故意遗漏一个关键字段（贴近真机模型偶尔漏字段的行为），
# 触发「聚焦重试」，从而完整建模 image_vision 的慢路径（4 次 AI 调用）。
OCR_SAMPLE_VISION_FIRST = {**OCR_SAMPLE, "购买方名称": ""}

ANOMALY_PASS = {"总体结论": "通过", "异常明细": [], "检查摘要": "无异常"}
CLASSIFY_PASS = {
    "费用分类": "差旅",
    "分类依据": "住宿费",
    "分类限额": 1000,
    "是否超限": False,
    "校验结果": "通过",
}


def _generate_test_pdf(num_pages: int = 3) -> str:
    """用 PyMuPDF 生成多页测试 PDF（含发票关键字段文本）"""
    import fitz

    doc = fitz.open()
    for i in range(num_pages):
        page = doc.new_page()
        page.insert_text(
            (50, 72),
            (
                f"发票测试页面 {i + 1}\n"
                f"发票号码: {12345678 + i}\n"
                f"开票日期: 2026-06-01\n"
                f"发票金额: 300.00\n"
                f"销售方名称: 测试酒店管理有限公司\n"
                f"购买方名称: 测试科技有限公司\n"
            ),
        )
    path = tempfile.mktemp(suffix=".pdf")
    doc.save(path)
    doc.close()
    return path


def _resolve_test_image() -> tuple[str, bool]:
    """返回真实发票样本 PNG 路径；若不存在则用 PIL 现生成一张临时 PNG。

    Returns:
        (路径, 是否临时文件) —— 临时文件需在测试后清理。
    """
    if REAL_IMAGE.exists():
        return str(REAL_IMAGE), False
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (800, 600), "white")
    d = ImageDraw.Draw(img)
    for i, line in enumerate(
        [
            "增值税电子普通发票",
            "发票号码: 12345678",
            "开票日期: 2026-06-01",
            "价税合计: 300.00",
            "销售方名称: YY公司",
            "购买方名称: XX公司",
        ]
    ):
        d.text((40, 40 + i * 60), line, fill="black")
    path = tempfile.mktemp(suffix=".png")
    img.save(path)
    return path, True


def _make_ai_stub(delay_ms: float):
    """返回一个带固定延迟、且按 call_type 返回合理结构的 stub，
    用于替换各工具模块中的 ``call_deepseek_function`` / ``call_deepseek_vision``。

    注意：Vision 调用以关键字传入 ``image_data_url`` / ``text_hint``，
    故签名须带 ``**kwargs`` 吸收这些额外参数。
    """
    calls: list = []

    def _stub(
        system_prompt=None,
        user_content=None,
        tools=None,
        tool_choice="auto",
        call_type=None,
        **kwargs,
    ):
        time.sleep(delay_ms / 1000.0)
        calls.append(call_type)
        if call_type == "发票OCR提取":
            return dict(OCR_SAMPLE)
        if call_type == "发票OCR提取(vision)":
            # 首轮 Vision 漏掉一个关键字段，触发聚焦重试（贴近真机模型行为）
            return dict(OCR_SAMPLE_VISION_FIRST)
        if call_type == "发票OCR提取(vision·重试)":
            return dict(OCR_SAMPLE)
        if call_type == "异常检测":
            return dict(ANOMALY_PASS)
        if call_type == "分类限额":
            return dict(CLASSIFY_PASS)
        return {"_warning": f"未知 call_type: {call_type}"}

    _stub.calls = calls
    return _stub


def _percentile(values: list[float], p: float) -> float:
    """计算百分位数（线性就近法）"""
    if not values:
        return 0.0
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round(p / 100.0 * (len(s) - 1)))))
    return s[k]


def _start_ai_patches(stub, kind: str, force_vision: bool) -> list:
    """启动对底层 AI 客户端所有引用点的 patch（避免 from-import 陷阱）"""
    targets = [
        "skill.tools.tool_ocr_extract.call_deepseek_function",
        "skill.tools.tool_classify_limit.call_deepseek_function",
        "skill.tools.tool_anomaly_check.call_deepseek_function",
        "skill.tools.tool_itinerary_ocr.call_deepseek_function",
        # 覆盖图片 Vision 降级路径（call_deepseek_vision 在 tool_ocr_extract 内为局部 import，
        # patch 其源模块属性即可生效）
        "skill.utils.http_client.call_deepseek_vision",
    ]
    patchers = [patch(t, side_effect=stub) for t in targets]

    # 图片路径：按 kind 模拟「本地 OCR 是否可用」
    if kind == "image":
        if force_vision:
            # 镜像真机无本地 OCR：extract_image_text 抛 ImportError → 降级 Vision
            patchers.append(
                patch(
                    "skill.utils.image_ocr.extract_image_text",
                    side_effect=ImportError("本地 OCR 不可用（测试模拟）"),
                )
            )
        else:
            # 镜像已安装本地 OCR：extract_image_text 返回识别文本 → 走文本管线
            patchers.append(
                patch(
                    "skill.utils.image_ocr.extract_image_text",
                    return_value=_FAKE_OCR_TEXT,
                )
            )

    for p in patchers:
        p.start()
    return patchers


def run_benchmark(
    ai_delay_ms: float = 2000.0, runs: int = 11, kind: str = "pdf", force_vision: bool = False
) -> dict:
    """运行端到端基准测试，返回耗时统计。

    Args:
        ai_delay_ms: 单点 AI 调用固定延迟（毫秒）
        runs: 测量采样次数（不含预热）
        kind: 票据路径 —— "pdf" / "image"
        force_vision: 仅当 kind="image" 时生效；
                      True 模拟无本地 OCR（Vision 降级），False 模拟本地 OCR 可用
    """
    from skill.agent import run_reimbursement_skill

    if kind == "pdf":
        path = _generate_test_pdf(num_pages=3)
        is_temp = True
    else:
        path, is_temp = _resolve_test_image()

    samples: list[float] = []
    last_status = None
    stub = _make_ai_stub(ai_delay_ms)
    patchers = _start_ai_patches(stub, kind, force_vision)
    try:
        # 预热 1 次（避免首次图构建/导入抖动影响采样）
        run_reimbursement_skill(
            pdf_path=path, apply_amount=500, apply_date="2026-06-10", ticket_type="发票"
        )
        stub.calls.clear()  # 丢弃预热调用的计数，仅统计正式采样

        for _ in range(runs):
            t0 = time.perf_counter()
            res = run_reimbursement_skill(
                pdf_path=path, apply_amount=500, apply_date="2026-06-10", ticket_type="发票"
            )
            samples.append((time.perf_counter() - t0) * 1000)
            last_status = res.get("status")
    finally:
        for p in patchers:
            p.stop()
        if is_temp:
            try:
                os.unlink(path)
            except OSError:
                pass

    p95 = _percentile(samples, 95)
    breakdown = dict(Counter(c for c in stub.calls if c))
    # 每轮平均 AI 调用次数（同一次 run_reimbursement_skill 内触发几次底层 AI 调用）
    calls_per_run = (len(stub.calls) / runs) if runs else 0

    return {
        "kind": kind,
        "force_vision": force_vision,
        "path": path,
        "ai_delay_ms": ai_delay_ms,
        "runs": len(samples),
        "ai_calls": len(stub.calls),
        "calls_per_run": calls_per_run,
        "ai_call_breakdown": breakdown,
        "min_ms": min(samples),
        "max_ms": max(samples),
        "avg_ms": sum(samples) / len(samples),
        "p95_ms": p95,
        "status": last_status,
        "threshold_ms": THRESHOLD_E2E_MS,
        "pass": p95 <= THRESHOLD_E2E_MS,
    }


def _print_stats(stats: dict) -> None:
    kind_label = {
        ("pdf", False): "PDF（文本层）",
        ("image", False): "图片（本地 OCR 可用 → 文本管线）",
        ("image", True): "图片（无本地 OCR → Vision 降级）",
    }.get((stats.get("kind"), stats.get("force_vision")), stats.get("kind"))
    print("\n" + "=" * 64)
    print("需求 N3 · 单张票据端到端 AI 识别与校验性能测试")
    print("=" * 64)
    print(f"  测试路径           : {kind_label}")
    print(f"  单点 AI 延迟 (stub) : {stats['ai_delay_ms']:.0f} ms")
    print(f"  采样次数           : {stats['runs']}")
    print(f"  平均 AI 调用/轮    : {stats['calls_per_run']:.1f}")
    print(f"  最小耗时           : {stats['min_ms']:.1f} ms")
    print(f"  最大耗时           : {stats['max_ms']:.1f} ms")
    print(f"  平均耗时           : {stats['avg_ms']:.1f} ms")
    print(f"  P95 耗时           : {stats['p95_ms']:.1f} ms")
    print(f"  N3 阈值 (P95 ≤)    : {stats['threshold_ms']} ms")
    print(f"  AI 调用次数        : {stats.get('ai_calls')} {stats.get('ai_call_breakdown')}")
    print(f"  最终状态           : {stats['status']}")
    print(f"  判定               : {'通过' if stats['pass'] else '不通过'}")
    print("=" * 64)


def test_e2e_latency_with_stub_ai():
    """N3 自动化验证：PDF 路径端到端总耗时 P95 ≤ 10 秒"""
    stats = run_benchmark(ai_delay_ms=2000.0, runs=11, kind="pdf", force_vision=False)
    _print_stats(stats)
    assert stats["status"] in ("通过", "预警", "拦截"), f"返回状态异常: {stats['status']}"
    assert stats[
        "pass"
    ], f"端到端 P95 耗时 {stats['p95_ms']:.1f}ms 超过 N3 阈值 {THRESHOLD_E2E_MS}ms"


def test_e2e_latency_image_text_with_stub_ai():
    """图片路径（本地 OCR 可用）性能基线：3 次 AI 调用 + P95 ≤ 10 秒"""
    stats = run_benchmark(ai_delay_ms=2000.0, runs=11, kind="image", force_vision=False)
    _print_stats(stats)
    assert stats["status"] in ("通过", "预警", "拦截"), f"返回状态异常: {stats['status']}"
    # 本地 OCR 可用：文本管线 1（OCR）+ 异常检测 1 + 分类限额 1 = 3 次/轮
    assert (
        stats["ai_calls"] == 11 * 3
    ), f"图片(本地OCR)路径 AI 调用次数异常: {stats['ai_call_breakdown']}"
    assert stats[
        "pass"
    ], f"端到端 P95 耗时 {stats['p95_ms']:.1f}ms 超过 N3 阈值 {THRESHOLD_E2E_MS}ms"


def test_e2e_latency_image_vision_with_stub_ai():
    """图片路径（无本地 OCR → Vision 降级）性能基线（锁定优化后路径）。

    优化后（收敛 Vision 重试触发字段）：首轮 Vision 仅漏「购买方名称」这类非校验必需字段
    时不再整图重跑，故典型路径 = Vision 首轮 1 + 异常检测 1 + 分类限额 1 = 3 次 AI 调用/轮。
    该基线锁定「调用结构」，便于后续 A/B/C 层优化后做回归对照；
    仅当校验必需字段（号码/日期/金额）缺失时才会触发聚焦重试（4 次/轮，安全兜底）。
    """
    stats = run_benchmark(ai_delay_ms=2000.0, runs=11, kind="image", force_vision=True)
    _print_stats(stats)
    assert stats["status"] in ("通过", "预警", "拦截"), f"返回状态异常: {stats['status']}"
    # 优化后典型路径：Vision 首轮 + 异常检测 + 分类限额 = 3 次/轮
    # （首轮仅漏购买方名称，不触发聚焦重试；聚焦重试仅在缺校验必需字段时触发）
    assert (
        stats["ai_calls"] == 11 * 3
    ), f"图片(Vision 降级)路径 AI 调用次数异常: {stats['ai_call_breakdown']}"


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="发票智能体流水线端到端延迟基线测量")
    ap.add_argument(
        "delay",
        nargs="?",
        type=float,
        default=2000.0,
        help="单点 AI 调用固定延迟（毫秒），默认 2000",
    )
    ap.add_argument(
        "runs",
        nargs="?",
        type=int,
        default=11,
        help="采样次数（不含预热），默认 11",
    )
    ap.add_argument(
        "--kind",
        choices=["pdf", "image_text", "image_vision"],
        default="pdf",
        help="票据路径：pdf / image_text(本地OCR可用) / image_vision(无本地OCR→Vision降级)",
    )
    ap.add_argument(
        "--delay-flag",
        dest="delay_flag",
        type=float,
        default=None,
        help="与 --kind 配合使用的延迟覆盖（优先级高于位置参数）",
    )
    args = ap.parse_args()

    force_vision = args.kind == "image_vision"
    kind = "pdf" if args.kind == "pdf" else "image"
    ai_delay = args.delay_flag if args.delay_flag is not None else args.delay

    stats = run_benchmark(
        ai_delay_ms=ai_delay, runs=args.runs, kind=kind, force_vision=force_vision
    )
    _print_stats(stats)
    sys.exit(0 if stats["pass"] else 1)
