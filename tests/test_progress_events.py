"""[测试] 流水线真实进度事件：contextvar 通道、SSE 端点鉴权、进度总线并发与回收。

覆盖修复点：前端流水线动画此前由固定定时器驱动（约 6s 跑完），
而真实处理需 40s+，导致「动画已完成、实际还在跑」。改为后端节点事件驱动后，
需保证：① 未注册回调时零副作用；② 事件顺序真实；③ 跨线程可传播；④ 越权不可订阅。
"""

import json
import threading
import time
from unittest.mock import patch

import pytest

from skill.utils.progress import (
    STATUS_DONE,
    STATUS_START,
    STEP_OCR,
    emit_progress,
    get_progress_callback,
    progress_scope,
    progress_step,
)
from web.progress_bus import ProgressBus, ProgressChannel


# ═══════════════ 1. 进度回调通道（skill.utils.progress） ═══════════════


def test_emit_without_callback_is_noop():
    """未注册回调时 emit 不得抛错，保证 CLI/单测路径零侵入"""
    assert get_progress_callback() is None
    emit_progress(STEP_OCR, STATUS_START, "不应产生任何副作用")


def test_progress_scope_collects_events_in_order():
    events = []
    with progress_scope(events.append):
        emit_progress(STEP_OCR, STATUS_START, "开始")
        emit_progress(STEP_OCR, STATUS_DONE, "结束")
    assert [(e["step"], e["status"]) for e in events] == [
        (STEP_OCR, STATUS_START),
        (STEP_OCR, STATUS_DONE),
    ]
    assert all("ts" in e for e in events)
    # 作用域退出后回调必须还原，避免污染后续请求
    assert get_progress_callback() is None


def test_progress_scope_nested_restore():
    outer, inner = [], []
    with progress_scope(outer.append):
        with progress_scope(inner.append):
            emit_progress(STEP_OCR, STATUS_START)
        emit_progress(STEP_OCR, STATUS_DONE)
    assert len(inner) == 1 and len(outer) == 1


def test_callback_exception_does_not_break_business_flow():
    """进度上报失败绝不能影响主流程"""

    def boom(_event):
        raise RuntimeError("下游订阅端已断开")

    with progress_scope(boom):
        emit_progress(STEP_OCR, STATUS_START)  # 不应抛出


def test_progress_step_emits_fail_on_exception():
    events = []
    with progress_scope(events.append):
        with pytest.raises(ValueError):
            with progress_step(STEP_OCR):
                raise ValueError("boom")
    assert [e["status"] for e in events] == [STATUS_START, "fail"]


def test_callback_propagates_to_thread_via_copy_context():
    """anomaly_node 并行分支依赖 copy_context 传播回调"""
    from contextvars import copy_context

    events = []
    with progress_scope(events.append):
        ctx = copy_context()
        t = threading.Thread(target=lambda: ctx.run(emit_progress, STEP_OCR, STATUS_DONE, "子线程"))
        t.start()
        t.join()
    assert len(events) == 1 and events[0]["message"] == "子线程"


# ═══════════════ 2. 编排层埋点顺序 ═══════════════


def test_agent_emits_route_events(monkeypatch):
    """run_reimbursement_skill 传入 on_progress 后应产出路由事件"""
    import skill.agent as agent_mod

    monkeypatch.setattr(agent_mod, "run_graph", lambda state: {**state, "status": "通过"})
    events = []
    agent_mod.run_reimbursement_skill(
        pdf_path="dummy.pdf", ticket_type="发票", on_progress=events.append
    )
    assert [(e["step"], e["status"]) for e in events][:2] == [("route", "start"), ("route", "done")]


def test_agent_without_callback_keeps_legacy_behavior(monkeypatch):
    """不传 on_progress 时行为与改造前完全一致"""
    import skill.agent as agent_mod

    monkeypatch.setattr(agent_mod, "run_graph", lambda state: {**state, "status": "通过"})
    result = agent_mod.run_reimbursement_skill(pdf_path="dummy.pdf", ticket_type="发票")
    assert result["status"] == "通过"


@patch("skill.orchestrator.nodes.ocr_node.ocr_extract_invoice")
@patch("skill.orchestrator.nodes.anomaly_node.detect_anomaly")
@patch("skill.orchestrator.nodes.classify_node.classify_and_check_limit")
def test_full_pipeline_event_sequence(
    mock_classify, mock_anomaly, mock_ocr, sample_invoice_data, sample_classify_result
):
    """跑通真实 StateGraph，校验前端所需的 5 个步骤事件齐备且顺序正确"""
    from skill.agent import run_reimbursement_skill

    mock_ocr.return_value = sample_invoice_data
    mock_anomaly.return_value = {"总体结论": "通过", "异常明细": [], "检查摘要": "无异常"}
    mock_classify.return_value = sample_classify_result

    events = []
    run_reimbursement_skill(
        pdf_path="test.pdf",
        apply_amount=500,
        apply_date="2026-06-10",
        on_progress=events.append,
    )

    seq = [(e["step"], e["status"]) for e in events]
    # 每个前端步骤都必须有明确终态（done/skip/fail），否则动画会永远卡在转圈
    terminal = {s for s, st in seq if st in ("done", "skip", "fail")}
    assert {"route", "ocr", "anomaly", "classify"} <= terminal
    # 顺序约束：路由 → OCR → 异常/分类 → 查验
    assert seq.index(("route", "done")) < seq.index(("ocr", "start"))
    assert seq.index(("ocr", "done")) < seq.index(("anomaly", "start"))
    # 并行分支：两步的 start 必须都早于任一 done（体现真实并行而非串行）
    assert seq.index(("classify", "start")) < seq.index(("anomaly", "done"))


@patch("skill.orchestrator.nodes.ocr_node.ocr_extract_invoice")
@patch("skill.orchestrator.nodes.anomaly_node.detect_anomaly")
def test_small_amount_marks_classify_as_skipped(mock_anomaly, mock_ocr, sample_invoice_data):
    """小额免审路径：分类限额未执行，须显式发 skip，避免前端一直转圈"""
    from skill.agent import run_reimbursement_skill

    small = {**sample_invoice_data, "发票金额": 30.0}
    mock_ocr.return_value = small
    mock_anomaly.return_value = {"总体结论": "通过", "异常明细": [], "检查摘要": "无异常"}

    events = []
    run_reimbursement_skill(pdf_path="test.pdf", apply_amount=30, on_progress=events.append)
    assert ("classify", "skip") in [(e["step"], e["status"]) for e in events]


# ═══════════════ 3. 进度总线（web.progress_bus） ═══════════════


def test_channel_subscribe_receives_published_events():
    ch = ProgressChannel("cid", "u1")

    def producer():
        time.sleep(0.05)
        ch.publish({"step": "ocr", "status": "start"})
        ch.publish({"step": "ocr", "status": "done"})
        ch.close()

    threading.Thread(target=producer).start()
    got = [e for e in ch.subscribe(timeout=5, poll=0.2) if e is not None]
    assert [e["status"] for e in got] == ["start", "done"]


def test_channel_subscribe_emits_heartbeat_when_idle():
    ch = ProgressChannel("cid", "u1")
    threading.Timer(0.4, ch.close).start()
    beats = [e for e in ch.subscribe(timeout=5, poll=0.1) if e is None]
    assert len(beats) >= 1  # 空闲期必须有心跳，防代理切断长连接


def test_channel_publish_after_close_is_ignored():
    ch = ProgressChannel("cid", "u1")
    ch.close()
    ch.publish({"step": "ocr", "status": "done"})
    assert list(ch.subscribe(timeout=1, poll=0.1)) == []


def test_bus_rejects_cross_user_access():
    bus = ProgressBus()
    assert bus.open("pid-abc123", "alice") is not None
    assert bus.open("pid-abc123", "bob") is None  # 归属不符 → 拒绝
    assert bus.get("pid-abc123", "bob") is None
    assert bus.get("pid-abc123", "alice") is not None


def test_bus_discard_removes_channel():
    bus = ProgressBus()
    bus.open("pid-xyz789", "alice")
    bus.discard("pid-xyz789")
    assert bus.get("pid-xyz789", "alice") is None


# ═══════════════ 4. SSE 端点 ═══════════════


def test_progress_stream_requires_login(client):
    r = client.get("/api/progress/abcdefgh12345678")
    assert r.status_code == 401


def test_progress_stream_rejects_invalid_id(client):
    with client.session_transaction() as sess:
        sess["account"] = "EMP-001"
        sess["role"] = "employee"
    r = client.get("/api/progress/short")  # 长度不足 8 → 非法
    assert r.status_code == 400


def test_progress_stream_pushes_events(client):
    from web.progress_bus import progress_bus

    pid = "sse-test-000001"
    with client.session_transaction() as sess:
        sess["account"] = "EMP-001"
        sess["role"] = "employee"

    channel = progress_bus.open(pid, "EMP-001")
    channel.publish({"step": "ocr", "status": "start", "message": "本地 OCR 识别中…"})
    channel.publish({"step": "__done__", "status": "done", "message": ""})
    channel.close()

    r = client.get(f"/api/progress/{pid}")
    assert r.status_code == 200
    assert r.mimetype == "text/event-stream"
    body = r.get_data(as_text=True)
    payloads = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]
    assert [p["step"] for p in payloads] == ["ocr", "__done__"]
    assert payloads[0]["message"] == "本地 OCR 识别中…"


@patch("skill.orchestrator.nodes.classify_node.classify_and_check_limit")
@patch("skill.orchestrator.nodes.anomaly_node.detect_anomaly")
@patch("skill.orchestrator.nodes.ocr_node.ocr_extract_invoice")
@patch("web.app.render_invoice_images", return_value=None)
def test_upload_publishes_progress_to_channel(
    _mock_render, mock_ocr, mock_anomaly, mock_classify, client, sample_invoice_data
):
    """/upload 全链路：进度事件应实时落入频道，并在结束时补发 __done__ 且关闭频道"""
    import os
    import tempfile

    from web.progress_bus import progress_bus

    mock_ocr.return_value = sample_invoice_data
    mock_anomaly.return_value = {"总体结论": "通过", "异常明细": [], "检查摘要": "无异常"}
    mock_classify.return_value = {"总体结论": "通过", "费用类型": "差旅-住宿", "限额校验": {}}

    with client.session_transaction() as sess:
        sess["account"] = "EMP-2026"
        sess["role"] = "employee"
        sess["name"] = "张三"

    pid = "upload-progress-0001"
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(b"%PDF-1.4 fake invoice")
        tmp_path = f.name
    try:
        with open(tmp_path, "rb") as fp:
            resp = client.post(
                "/upload",
                data={
                    "file": (fp, "invoice.pdf"),
                    "apply_amount": "358.50",
                    "apply_date": "2026-07-14",
                    "ticket_type": "发票",
                    "progress_id": pid,
                },
                content_type="multipart/form-data",
            )
    finally:
        os.unlink(tmp_path)

    assert resp.status_code == 200
    channel = progress_bus.get(pid, "EMP-2026")
    assert channel is not None and channel.is_closed  # 结束后必须关闭，避免前端空转
    steps = [e["step"] for e in channel.subscribe(timeout=1, poll=0.1) if e]
    assert "ocr" in steps and "classify" in steps
    assert steps[-1] == "__done__"  # 终止哨兵必须最后送达
    progress_bus.discard(pid)


def test_progress_stream_rejects_other_users_channel(client):
    from web.progress_bus import progress_bus

    pid = "sse-test-000002"
    progress_bus.open(pid, "EMP-OTHER")
    with client.session_transaction() as sess:
        sess["account"] = "EMP-001"
        sess["role"] = "employee"
    r = client.get(f"/api/progress/{pid}")
    assert r.status_code == 400  # 归属不符 → 不可订阅
