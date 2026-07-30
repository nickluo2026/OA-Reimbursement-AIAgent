#!/usr/bin/env python3
"""移动端员工上传发票 → OCR + AI 检测 → 审批流 端到端实测 + 发票智能体耗时统计。

说明
----
- 本地 OCR 使用真实 Tesseract 引擎（skill.utils.image_ocr.extract_image_text），
  因此「OCR 引擎耗时」是真实可测量的。
- DeepSeek（异常检测 / 分类限额 / OCR 结构化）在本环境无可用 API Key，
  因此用桩函数模拟，并注入可配置的「模拟 AI 网络延迟」(默认 1.2s/次)，
  以便给出贴近生产的「含 AI 延迟」智能体执行时间。
  日志会明确标注 AI 耗时为模拟值。
- 票据缩略图渲染(render_invoice_images)属纯展示层，置为 no-op 以保证无头环境稳定。

用法
----
    python3 scripts/run_invoice_e2e.py [--image 发票图片.jpg] [--runs 5] [--ai-delay-ms 1200]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
import uuid
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

# ── 0. 隔离 DB / 上传目录（避免污染仓库）──
_TMP = tempfile.mkdtemp(prefix="oa_invoice_e2e_")
os.environ["OA_DB_PATH"] = os.path.join(_TMP, "oa_e2e.db")
os.environ["OA_INVOICE_DIR"] = os.path.join(_TMP, "invoices")
os.environ.setdefault("OA_DEMO_SEED", "1")

from flask import Flask  # noqa: E402  (确保 flask 可用后再导入应用)

from web.app import app as web_app  # noqa: E402
import web.app as web_app_mod  # noqa: E402  (用于 patch 模块级函数)
from skill.database import init_db  # noqa: E402
from skill import workflow as wf  # noqa: E402
from skill.agent import run_reimbursement_skill  # noqa: E402
from skill.tools import tool_ocr_extract, tool_classify_limit, tool_anomaly_check  # noqa: E402
from skill.utils import image_ocr  # noqa: E402

init_db()

# ── 1. 生成一张真实的增值税发票图片（供本地 OCR 识别）──
from PIL import Image, ImageDraw, ImageFont  # noqa: E402


def _find_cjk_font() -> str:
    candidates = [
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/Library/Fonts/Arial Unicode.ttf",
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    # 回退：让 PIL 抛错以便显式提示
    raise FileNotFoundError("未找到可用的中文字体，请在脚本中补充字体路径。")


def generate_invoice_image(path: Path) -> None:
    W, H = 1000, 1414
    img = Image.new("RGB", (W, H), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    font_title = ImageFont.truetype(_find_cjk_font(), 46)
    font = ImageFont.truetype(_find_cjk_font(), 30)
    font_small = ImageFont.truetype(_find_cjk_font(), 24)

    def line(text, y, f=font, x=70):
        draw.text((x, y), text, fill=(0, 0, 0), font=f)

    # 顶部标题
    draw.text((W // 2 - 230, 50), "增值税电子普通发票", fill=(200, 0, 0), font=font_title)
    draw.line([(60, 120), (W - 60, 120)], fill=(180, 0, 0), width=3)

    line("发票代码：044001900211", 150)
    line("发票号码：12345678", 200)
    line("开票日期：2026-06-01", 250)
    line("机器编号：661619982301", 300)

    draw.line([(60, 350), (W - 60, 350)], fill=(120, 120, 120), width=1)
    line("购买方名称：示例企业（上海）有限公司", 370, font_small)
    line("纳税人识别号：91310000MA1FL0XX99", 410, font_small)
    draw.line([(60, 450), (W - 60, 450)], fill=(120, 120, 120), width=1)

    line("销售方名称：沪上餐饮管理有限公司", 470, font_small)
    line("纳税人识别号：91310104MA1FL2YY88", 510, font_small)
    draw.line([(60, 550), (W - 60, 550)], fill=(120, 120, 120), width=1)

    line("项目名称：*餐饮服务*餐费", 580, font_small)
    line("金额：283.02    税率：6%    税额：16.98", 620, font_small)
    draw.line([(60, 660), (W - 60, 660)], fill=(120, 120, 120), width=1)

    line("价税合计（大写）：叁佰圆整", 690, font_small)
    line("价税合计（小写）：¥300.00", 730, font_small)
    draw.line([(60, 770), (W - 60, 770)], fill=(120, 120, 120), width=1)

    line("收款人：王敏    复核：李强    开票人：张伟", 800, font_small)

    # 红色印章
    draw.ellipse([(640, 880), (900, 1140)], outline=(200, 0, 0), width=6)
    draw.text((690, 990), "发票专用章", fill=(200, 0, 0), font=font_small)

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == ".png":
        img.save(path, "PNG")
    else:
        img.save(path, "JPEG", quality=95)
    print(f"[生成] 发票图片 -> {path}  ({path.stat().st_size/1024:.1f} KB)")


# ── 2. DeepSeek 桩函数（含可配置模拟延迟，并记录每次调用耗时）──
AI_CALLS: list[tuple[str, float]] = []
AI_DELAY_MS = 1200


_INVOICE_SEQ = {"n": 0}


def _ocr_sample() -> dict:
    _INVOICE_SEQ["n"] += 1
    inv_no = f"12{_INVOICE_SEQ['n']:06d}"
    return {
        "发票代码": "044001900211",
        "发票号码": inv_no,
        "开票日期": "2026-06-01",
        "机器编号": "661619982301",
        "购买方名称": "示例企业（上海）有限公司",
        "购买方纳税人识别号": "91310000MA1FL0XX99",
        "销售方名称": "沪上餐饮管理有限公司",
        "销售方纳税人识别号": "91310104MA1FL2YY88",
        "项目名称": "餐饮服务*餐费",
        "发票类型": "电子发票",
        "费用分类": "餐饮费",
        "发票金额": 300.0,
        "税率": "6%",
        "税额": 16.98,
        "价税合计(大写)": "叁佰圆整",
        "校验码": "12345678901234567890",
    }


def _anomaly_pass() -> dict:
    return {
        "结论": "通过",
        "总体结论": "通过",
        "risk_level": "low",
        "严重异常": [],
        "中度异常": [],
        "建议": [],
        "说明": "未检出明显异常",
    }


def _classify_pass() -> dict:
    return {
        "报销类型": "差旅/住宿",
        "费用分类": "住宿费",
        "判定结果": "通过",
        "发票金额": 300.0,
        "标准上限": 1000.0,
        "超出金额": 0.0,
        "说明": "在限额内",
    }


def stub_function(system_prompt, user_content, tools, tool_choice="auto", call_type=None, **kwargs):
    t0 = time.perf_counter()
    time.sleep(AI_DELAY_MS / 1000.0)
    if call_type and ("OCR" in call_type or "提取" in call_type):
        result = _ocr_sample()
    elif call_type and ("异常" in call_type):
        result = _anomaly_pass()
    elif call_type and ("分类" in call_type or "限额" in call_type):
        result = _classify_pass()
    else:
        result = {}
    AI_CALLS.append((str(call_type), time.perf_counter() - t0))
    return result


def stub_vision(system_prompt, image_data_url, tools, tool_choice="auto", call_type=None, **kwargs):
    t0 = time.perf_counter()
    time.sleep(AI_DELAY_MS / 1000.0)
    result = _ocr_sample()
    AI_CALLS.append(("vision:" + str(call_type), time.perf_counter() - t0))
    return result


# ── 3. 计时包装：真实 OCR 引擎 + 智能体调用 ──
OCR_TIMES: list[float] = []
AGENT_UPLOAD_TIME: list[float] = []
_orig_extract = image_ocr.extract_image_text


def _timed_extract(image_path, *a, **k):
    t0 = time.perf_counter()
    r = _orig_extract(image_path, *a, **k)
    OCR_TIMES.append(time.perf_counter() - t0)
    return r


_orig_skill = web_app_mod.run_reimbursement_skill


def _timed_skill(*a, **k):
    t0 = time.perf_counter()
    r = _orig_skill(*a, **k)
    AGENT_UPLOAD_TIME.append(time.perf_counter() - t0)
    return r


# ── 4. 工具函数 ──
def _fmt_ms(seconds: float) -> str:
    return f"{seconds * 1000:.1f} ms"


def _stats(times: list[float]) -> dict:
    if not times:
        return {"n": 0, "avg": 0, "min": 0, "max": 0, "p95": 0}
    s = sorted(times)
    n = len(s)
    avg = sum(s) / n

    def p95(v):
        return v[min(n - 1, int(0.95 * n))]

    return {
        "n": n,
        "avg": avg,
        "min": s[0],
        "max": s[-1],
        "p95": p95(s),
    }


def _login(client, account: str, password: str) -> bool:
    r = client.post(
        "/api/auth/login",
        json={"account": account, "password": password},
        headers={"Content-Type": "application/json"},
    )
    if r.status_code != 200:
        print(f"    [登录失败] {account} HTTP={r.status_code} body={r.get_data(as_text=True)[:200]}")
        return False
    data = r.get_json(silent=True) or {}
    if not (data.get("ok") is True or data.get("status") == "ok"):
        print(f"    [登录失败] {account} json={data}")
        return False
    return True


MOBILE_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", default=str(REPO / "发票图片.jpg"))
    ap.add_argument("--runs", type=int, default=5, help="发票智能体多次运行取统计")
    ap.add_argument("--ai-delay-ms", type=int, default=1200, help="模拟 DeepSeek 单次网络延迟(ms)")
    args = ap.parse_args()

    global AI_DELAY_MS
    AI_DELAY_MS = args.ai_delay_ms

    img_path = Path(args.image)
    generate_invoice_image(img_path)

    web_app.testing = True

    # 应用所有桩 / 计时包装
    patchers = [
        patch("skill.tools.tool_ocr_extract.call_deepseek_function", stub_function),
        patch("skill.tools.tool_classify_limit.call_deepseek_function", stub_function),
        patch("skill.tools.tool_anomaly_check.call_deepseek_function", stub_function),
        patch("skill.tools.tool_itinerary_ocr.call_deepseek_function", stub_function),
        patch("skill.utils.http_client.call_deepseek_function", stub_function),
        patch("skill.utils.http_client.call_deepseek_vision", stub_vision),
        patch("skill.utils.image_ocr.extract_image_text", new=_timed_extract),
        patch("web.app.render_invoice_images", new=lambda *a, **k: None),
        patch("web.app.run_reimbursement_skill", new=_timed_skill),
    ]
    for p in patchers:
        p.start()

    print("=" * 70)
    print("移动端员工上传发票 → OCR + AI 检测 → 审批流 端到端测试")
    print("=" * 70)

    try:
        emp = web_app.test_client()
        mgr = web_app.test_client()
        fin = web_app.test_client()
        pay = web_app.test_client()

        # (a) 各角色登录（移动端）
        t = time.perf_counter()
        emp_ok = _login(emp, "EMP-2026", "123456")
        emp_login_ms = (time.perf_counter() - t) * 1000

        t = time.perf_counter()
        mgr_ok = _login(mgr, "APR-001", "123456")
        mgr_login_ms = (time.perf_counter() - t) * 1000

        t = time.perf_counter()
        fin_ok = _login(fin, "FIN-001", "123456")
        fin_login_ms = (time.perf_counter() - t) * 1000

        t = time.perf_counter()
        pay_ok = _login(pay, "FIN-002", "123456")
        pay_login_ms = (time.perf_counter() - t) * 1000
        print(
            f"[登录] 员工={emp_ok} 主管={mgr_ok} 财务={fin_ok} 出纳={pay_ok}  "
            f"(员工 {emp_login_ms:.0f}ms / 主管 {mgr_login_ms:.0f}ms / "
            f"财务 {fin_login_ms:.0f}ms / 出纳 {pay_login_ms:.0f}ms)"
        )

        # (b) 移动端上传发票（触发 OCR + AI 检测 + 智能体）
        request_id = uuid.uuid4().hex
        with open(img_path, "rb") as f:
            upload_ct = "image/png" if img_path.suffix.lower() == ".png" else "image/jpeg"
            t = time.perf_counter()
            resp = emp.post(
                "/upload",
                data={
                    "file": (f, img_path.name, upload_ct),
                    "request_id": request_id,
                    "employee_id": "EMP-2026",
                    "ticket_type": "发票",
                },
                content_type="multipart/form-data",
                headers={"User-Agent": MOBILE_UA},
            )
            upload_http_ms = (time.perf_counter() - t) * 1000

        upload_json = resp.get_json(silent=True) or {}
        agent_time = AGENT_UPLOAD_TIME[-1] if AGENT_UPLOAD_TIME else 0.0
        print(
            f"[上传] HTTP={resp.status_code} 状态={upload_json.get('status')} "
            f"request_id={request_id[:8]}..."
        )
        # 调试：确认发票是否已落库（上传接口会自生成 request_id，需用返回的 _request_id）
        request_id = upload_json.get("_request_id") or request_id
        _inv = wf.get_invoices_for_request(request_id)
        _reb = wf.get_reimbursement(request_id)
        print(f"       调试: 发票记录数={len(_inv)} 报销单是否存在={'是' if _reb else '否'}")
        print(f"       移动端上传端点总耗时: {upload_http_ms:.0f} ms")
        print(f"       >> 发票智能体执行时间(本次上传): {agent_time*1000:.0f} ms")

        # (c) 员工提交审批（建单；AI 态预警单按发票记录建单）
        t = time.perf_counter()
        upd = emp.post(f"/api/reimbursement/{request_id}/update", json={})
        submit_ms = (time.perf_counter() - t) * 1000
        upd_json = upd.get_json(silent=True) or {}
        print(f"[提交审批] HTTP={upd.status_code} -> {upd_json.get('status')} ({submit_ms:.0f} ms)")
        if upd.status_code != 200:
            print(f"     body={upd.get_data(as_text=True)[:200]}")

        # (d) 主管审批
        t = time.perf_counter()
        apr = mgr.post(
            "/api/approve",
            json={"request_id": request_id, "action": "通过", "comment": "同意报销"},
        )
        approve_ms = (time.perf_counter() - t) * 1000
        print(f"[主管审批] HTTP={apr.status_code} ({approve_ms:.0f} ms)")
        if apr.status_code != 200:
            print(f"     body={apr.get_data(as_text=True)[:200]}")

        # (e) 财务归档
        t = time.perf_counter()
        arc = fin.post("/api/finance", json={"request_id": request_id, "action": "archive"})
        archive_ms = (time.perf_counter() - t) * 1000
        print(f"[财务归档] HTTP={arc.status_code} ({archive_ms:.0f} ms)")
        if arc.status_code != 200:
            print(f"     body={arc.get_data(as_text=True)[:200]}")

        # (f) 出纳打款
        t = time.perf_counter()
        py = pay.post("/api/finance", json={"request_id": request_id, "action": "pay"})
        pay_ms = (time.perf_counter() - t) * 1000
        print(f"[出纳打款] HTTP={py.status_code} ({pay_ms:.0f} ms)")
        if py.status_code != 200:
            print(f"     body={py.get_data(as_text=True)[:200]}")

        # 最终状态核对
        reb = wf.get_reimbursement(request_id)
        final_status = reb.workflow_status if reb else "未知"
        print(f"[最终状态] workflow_status = {final_status}")

        # ── 5. 发票智能体执行时间统计（多次真实运行，含真实 OCR）──
        print("\n" + "-" * 70)
        print(f"发票智能体执行时间统计（{args.runs} 次独立运行，真实本地 OCR + 模拟 AI）")
        print("-" * 70)
        agent_loop: list[float] = []
        for i in range(args.runs):
            rid = uuid.uuid4().hex
            t0 = time.perf_counter()
            run_reimbursement_skill(
                request_id=rid,
                employee_id="EMP-2026",
                pdf_path=str(img_path),
                ticket_type="发票",
            )
            agent_loop.append(time.perf_counter() - t0)

        st = _stats(agent_loop)
        ocr_st = _stats(OCR_TIMES)
        ai_total = sum(d for _, d in AI_CALLS)
        ai_calls_n = len(AI_CALLS)
        # 单次上传已计一次 agent，额外 loop 次
        agent_no_ai = [a - (AI_DELAY_MS / 1000.0 * 3) for a in agent_loop]

        print(f"  发票智能体执行时间(含模拟AI延迟 {AI_DELAY_MS}ms×3):")
        print(f"      avg = {st['avg']*1000:.1f} ms | min = {st['min']*1000:.1f} ms | "
              f"max = {st['max']*1000:.1f} ms | p95 = {st['p95']*1000:.1f} ms")
        print(f"  发票智能体执行时间(不含模拟AI, 纯本地计算+真实OCR):")
        print(f"      avg = {sum(agent_no_ai)/len(agent_no_ai)*1000:.1f} ms")
        print(f"  真实 OCR 引擎耗时(本地 Tesseract):")
        print(f"      avg = {ocr_st['avg']*1000:.1f} ms | min = {ocr_st['min']*1000:.1f} ms | "
              f"max = {ocr_st['max']*1000:.1f} ms")
        print(f"  模拟 AI 调用: 共 {ai_calls_n} 次, 累计 {ai_total*1000:.0f} ms "
              f"(单次 {AI_DELAY_MS} ms × 3 调用/次)")
        print(f"\n  单次移动端上传实测: 发票智能体 = {agent_time*1000:.0f} ms, "
              f"端点总耗时 = {upload_http_ms:.0f} ms")

        print("\n结论：")
        print(f"  - 移动端端到端（上传→OCR+AI→主管→财务→出纳）最终状态: {final_status}")
        print(f"  - 发票智能体平均执行时间 ≈ {st['avg']*1000:.0f} ms "
              f"(其中真实OCR ≈ {ocr_st['avg']*1000:.0f} ms, 模拟AI ≈ {AI_DELAY_MS*3} ms)")
        print(f"  - 注：AI 耗时为模拟值；接入真实 DeepSeek 后总耗时以实际网络延迟为准。")

        # 写出 JSON 报告
        report = {
            "request_id": request_id,
            "final_status": final_status,
            "e2e_steps_ms": {
                "employee_login": emp_login_ms,
                "upload_http_total": upload_http_ms,
                "invoice_agent_single": agent_time * 1000,
                "submit_approval": submit_ms,
                "manager_approve": approve_ms,
                "finance_archive": archive_ms,
                "cashier_pay": pay_ms,
            },
            "agent_timing": {
                "runs": args.runs,
                "ai_delay_ms_per_call": AI_DELAY_MS,
                "agent_with_ai_avg_ms": st["avg"] * 1000,
                "agent_with_ai_p95_ms": st["p95"] * 1000,
                "agent_without_ai_avg_ms": sum(agent_no_ai) / len(agent_no_ai) * 1000,
                "ocr_engine_avg_ms": ocr_st["avg"] * 1000,
                "ai_total_ms": ai_total * 1000,
                "ai_calls": ai_calls_n,
            },
        }
        report_path = REPO / "scripts" / "invoice_e2e_report.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n[报告] 已写出 -> {report_path}")
    finally:
        for p in patchers:
            p.stop()


if __name__ == "__main__":
    main()
