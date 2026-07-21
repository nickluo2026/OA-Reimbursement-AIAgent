"""单张票据端到端 AI 识别与校验性能测试（需求 N3）

验证方式：用固定延迟 stub 替换底层 DeepSeek 客户端
（skill.utils.http_client.call_deepseek_function 在各工具模块中的引用），
每个底层 AI 调用注入固定延迟，测量 ``run_reimbursement_skill`` 从调用到返回的
端到端总耗时。

发票分支串行触发 3 次 AI 调用（OCR → 异常检测 → 分类限额），
端到端理论耗时 ≈ 3 × 单点延迟 + 本地编排开销。

用法：
  pytest tests/perf_e2e_latency.py -s -v              # 自动化验证，断言 P95 ≤ 10s
  python tests/perf_e2e_latency.py [AI延迟ms] [次数]  # 直接运行，打印耗时结果
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from unittest.mock import patch

# 让脚本在 tests/ 目录下直接运行时也能 import skill 包
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# N3 阈值：端到端总耗时 P95 ≤ 10 秒
THRESHOLD_E2E_MS = 10000


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


def _make_ai_stub(delay_ms: float):
    """返回一个带固定延迟、且按 call_type 返回合理结构的 stub，
    用于替换各工具模块中的 ``call_deepseek_function``。
    """
    OCR_SAMPLE = {
        "发票类型": "增值税普通发票",
        "发票号码": "12345678",
        "发票金额": 300.0,
        "开票日期": "2026-06-01",
        "销售方名称": "YY公司",
        "购买方名称": "XX公司",
    }
    ANOMALY_PASS = {"总体结论": "通过", "异常明细": [], "检查摘要": "无异常"}
    CLASSIFY_PASS = {
        "费用分类": "差旅",
        "分类依据": "住宿费",
        "分类限额": 1000,
        "是否超限": False,
        "校验结果": "通过",
    }

    calls: list = []

    def _stub(system_prompt, user_content, tools, tool_choice="auto", call_type=None):
        time.sleep(delay_ms / 1000.0)
        calls.append(call_type)
        if call_type == "发票OCR提取":
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


def _start_ai_patches(stub) -> list:
    """启动对底层 AI 客户端所有引用点的 patch（避免 from-import 陷阱）"""
    targets = [
        "skill.tools.tool_ocr_extract.call_deepseek_function",
        "skill.tools.tool_classify_limit.call_deepseek_function",
        "skill.tools.tool_anomaly_check.call_deepseek_function",
        "skill.tools.tool_itinerary_ocr.call_deepseek_function",
    ]
    patchers = [patch(t, side_effect=stub) for t in targets]
    for p in patchers:
        p.start()
    return patchers


def run_benchmark(ai_delay_ms: float = 2000.0, runs: int = 11) -> dict:
    """运行端到端基准测试，返回耗时统计。

    Args:
        ai_delay_ms: 单点 AI 调用固定延迟（毫秒）
        runs: 测量采样次数（不含预热）
    """
    from skill.agent import run_reimbursement_skill

    pdf_path = _generate_test_pdf(num_pages=3)
    samples: list[float] = []
    last_status = None
    stub = _make_ai_stub(ai_delay_ms)
    patchers = _start_ai_patches(stub)
    try:
        # 预热 1 次（避免首次图构建/导入抖动影响采样）
        run_reimbursement_skill(pdf_path=pdf_path, apply_amount=500, apply_date="2026-06-10")
        for _ in range(runs):
            t0 = time.perf_counter()
            res = run_reimbursement_skill(
                pdf_path=pdf_path, apply_amount=500, apply_date="2026-06-10"
            )
            samples.append((time.perf_counter() - t0) * 1000)
            last_status = res.get("status")
    finally:
        for p in patchers:
            p.stop()
        os.unlink(pdf_path)

    p95 = _percentile(samples, 95)
    from collections import Counter

    return {
        "ai_delay_ms": ai_delay_ms,
        "runs": len(samples),
        "ai_calls": len(stub.calls),
        "ai_call_breakdown": dict(Counter(c for c in stub.calls if c)),
        "min_ms": min(samples),
        "max_ms": max(samples),
        "avg_ms": sum(samples) / len(samples),
        "p95_ms": p95,
        "status": last_status,
        "threshold_ms": THRESHOLD_E2E_MS,
        "pass": p95 <= THRESHOLD_E2E_MS,
    }


def _print_stats(stats: dict) -> None:
    print("\n" + "=" * 64)
    print("需求 N3 · 单张票据端到端 AI 识别与校验性能测试")
    print("=" * 64)
    print(f"  单点 AI 延迟 (stub) : {stats['ai_delay_ms']:.0f} ms")
    print(f"  采样次数           : {stats['runs']}")
    print(f"  最小耗时           : {stats['min_ms']:.1f} ms")
    print(f"  最大耗时           : {stats['max_ms']:.1f} ms")
    print(f"  平均耗时           : {stats['avg_ms']:.1f} ms")
    print(f"  P95 耗时           : {stats['p95_ms']:.1f} ms")
    print(f"  N3 阈值 (P95 ≤)    : {stats['threshold_ms']} ms")
    print(f"  AI 调用次数        : {stats.get('ai_calls')} "
          f"({stats.get('ai_call_breakdown')})")
    print(f"  最终状态           : {stats['status']}")
    print(f"  判定               : {'通过' if stats['pass'] else '不通过'}")
    print("=" * 64)


def test_e2e_latency_with_stub_ai():
    """N3 自动化验证：端到端总耗时 P95 ≤ 10 秒"""
    stats = run_benchmark(ai_delay_ms=2000.0, runs=11)
    _print_stats(stats)
    assert stats["status"] in ("通过", "预警", "拦截"), f"返回状态异常: {stats['status']}"
    assert stats["pass"], (
        f"端到端 P95 耗时 {stats['p95_ms']:.1f}ms 超过 N3 阈值 {THRESHOLD_E2E_MS}ms"
    )


if __name__ == "__main__":
    ai_delay = float(sys.argv[1]) if len(sys.argv) > 1 else 2000.0
    runs = int(sys.argv[2]) if len(sys.argv) > 2 else 11
    stats = run_benchmark(ai_delay_ms=ai_delay, runs=runs)
    _print_stats(stats)
    sys.exit(0 if stats["pass"] else 1)
