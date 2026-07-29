#!/usr/bin/env python3
"""移动端全链路端到端演示脚本。

链路：新建发票图片 → 员工移动端登录/上传(/api/auth/login,/upload)
      → AI 检查(模拟 OCR/异常/分类) → 提交审批(/api/reimbursement/<id>/update)
      → 主管审批(/api/approve) → 财务归档(/api/finance 归档)
      → 出纳打款(/api/finance 打款)。

说明：
- 真实调用 Flask 路由、真实落库(SQLite 临时库)、真实审批工作流引擎。
- 本地无 DeepSeek Key 且无 tesseract，故三个 AI 节点以确定性 mock 返回，
  返回数据与生成的发票图片完全一致（参考 tests/test_e2e.py）。
"""

import os
import sys
import tempfile
from unittest.mock import patch

# 将项目根目录加入路径，保证 skill / web 可导入
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# 使用独立临时数据库，避免污染真实 oa_agent.db
_TMP_DB = os.path.join(tempfile.gettempdir(), "oa_mobile_e2e.db")
if os.path.exists(_TMP_DB):
    os.unlink(_TMP_DB)
os.environ["OA_DB_PATH"] = _TMP_DB

from PIL import Image, ImageDraw, ImageFont

# ───────────────── 发票基础数据（与下方 mock 完全一致） ─────────────────
INVOICE_NO = "2531700000012345678"  # 20 位，符合 8-20 位格式
INVOICE_CODE = "044001900111"
INVOICE_DATE = "2026-07-20"
SELLER = "北京中关村智选酒店管理有限公司"
BUYER = "XX科技有限公司"
AMOUNT_NO_TAX = "830.19"
TAX_RATE = "6%"
TAX = "49.81"
TOTAL = "880.00"
APPLY_AMOUNT = 880.00
APPLY_DATE = "2026-07-29"  # 申请日，发票未过期(距开票 <180 天)
EXPENSE_CATEGORY = "餐饮"  # 餐饮限额 1000 元，880<1000 → 通过
REASON = "北京出差团队工作餐"

# 生成的发票图片保存路径（真实上传文件）
GEN_IMAGE = os.path.join(ROOT, "scripts", "invoice_mobile_e2e.png")

FONT_PATH = "/System/Library/Fonts/PingFang.ttc"


def _font(size, idx=0):
    return ImageFont.truetype(FONT_PATH, size, index=idx)


def generate_invoice_png(path):
    """用 PIL 绘制一张清晰、可读的增值税普通发票 PNG。"""
    W, H = 860, 1180  # noqa: N806 (图像宽/高，惯例大写)
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)

    # 外边框
    d.rectangle([12, 12, W - 12, H - 12], outline="#c0392b", width=3)
    d.rectangle([22, 22, W - 22, H - 22], outline="#888888", width=1)

    # 标题
    d.text((W // 2, 48), "增值税普通发票", fill="#c0392b", font=_font(40), anchor="mm")
    d.text((W // 2, 96), "发票联", fill="#888888", font=_font(20), anchor="mm")

    # 发票代码 / 号码 / 日期（右上）
    d.text((W - 40, 140), f"发票代码：{INVOICE_CODE}", fill="black", font=_font(20), anchor="rm")
    d.text((W - 40, 172), f"发票号码：{INVOICE_NO}", fill="black", font=_font(20), anchor="rm")
    d.text((W - 40, 204), f"开票日期：{INVOICE_DATE}", fill="black", font=_font(20), anchor="rm")

    # 购买方
    y = 250
    d.rectangle([40, y, W - 40, y + 90], outline="#888888", width=1)
    d.text((52, y + 12), "购买方", fill="black", font=_font(20))
    d.text((160, y + 12), f"名称：{BUYER}", fill="black", font=_font(20))
    d.text((160, y + 50), "纳税人识别号：91110108MA01XXXXX", fill="black", font=_font(18))

    # 销售方
    y = 360
    d.rectangle([40, y, W - 40, y + 90], outline="#888888", width=1)
    d.text((52, y + 12), "销售方", fill="black", font=_font(20))
    d.text((160, y + 12), f"名称：{SELLER}", fill="black", font=_font(20))
    d.text((160, y + 50), "纳税人识别号：91110108MA02YYYYY", fill="black", font=_font(18))

    # 货物或应税劳务、服务名称 表头
    y = 480
    d.rectangle([40, y, W - 40, y + 40], outline="#888888", width=1)
    headers = [("项目名称", 40, 360), ("金额", 360, 560), ("税率", 560, 680), ("税额", 680, W - 40)]
    for name, x0, x1 in headers:
        d.text(((x0 + x1) // 2, y + 20), name, fill="black", font=_font(20), anchor="mm")
        d.rectangle([x0, y, x0, y + 40], outline="#888888", width=1)
    # 明细行
    y2 = y + 40
    d.rectangle([40, y2, W - 40, y2 + 60], outline="#888888", width=1)
    d.text((200, y2 + 30), "餐饮服务*餐费", fill="black", font=_font(20), anchor="mm")
    d.text((460, y2 + 30), AMOUNT_NO_TAX, fill="black", font=_font(20), anchor="mm")
    d.text((620, y2 + 30), TAX_RATE, fill="black", font=_font(20), anchor="mm")
    d.text((760, y2 + 30), TAX, fill="black", font=_font(20), anchor="mm")
    for x0, x1 in [(360, 560), (560, 680), (680, W - 40)]:
        d.rectangle([x0, y2, x0, y2 + 60], outline="#888888", width=1)

    # 价税合计
    y3 = y2 + 60
    d.rectangle([40, y3, W - 40, y3 + 50], outline="#888888", width=1)
    d.text(
        (52, y3 + 25), "价税合计（大写）：捌佰捌拾元整", fill="black", font=_font(20), anchor="lm"
    )
    d.text((W - 200, y3 + 25), f"¥{TOTAL}", fill="#c0392b", font=_font(22), anchor="mm")

    # 销售方盖章区
    y4 = y3 + 90
    d.ellipse([W - 250, y4, W - 110, y4 + 140], outline="#c0392b", width=3)
    d.text((W - 180, y4 + 70), "发票专用章", fill="#c0392b", font=_font(18), anchor="mm")

    # 备注
    d.text(
        (40, y4 + 10),
        f"费用分类：{EXPENSE_CATEGORY}   申请金额：¥{TOTAL}",
        fill="#555555",
        font=_font(18),
    )

    img.save(path, "PNG")
    return path


# ───────────────── 三个 AI 节点的确定性 mock 返回 ─────────────────
SAMPLE_OCR = {
    "发票类型": "增值税普通发票",
    "发票号码": INVOICE_NO,
    "发票代码": INVOICE_CODE,
    "开票日期": INVOICE_DATE,
    "购买方名称": BUYER,
    "销售方名称": SELLER,
    "发票金额": APPLY_AMOUNT,
    "税额": TAX,
    "价税合计_小写": float(TOTAL),
}
SAMPLE_ANOMALY = {
    "总体结论": "通过",
    "异常明细": [],
    "检查摘要": "无重复报销 / 未过期 / 字段完整 / 金额正常",
}
SAMPLE_CLASSIFY = {
    "费用分类": EXPENSE_CATEGORY,
    "分类依据": "餐饮服务餐费",
    "发票金额": APPLY_AMOUNT,
    "分类限额": 1000,
    "是否超限": False,
    "校验结果": "通过",
}


def _hr(title):
    print("\n" + "=" * 60)
    print("  " + title)
    print("=" * 60)


def main():
    # 0) 建表 + 生成发票图片
    from skill.database import init_db

    init_db()

    _hr("① 新建发票图片")
    img_path = generate_invoice_png(GEN_IMAGE)
    print(f"已生成发票图片：{img_path}")
    print(f"  发票号码={INVOICE_NO}  价税合计=¥{TOTAL}  费用分类={EXPENSE_CATEGORY}")

    from web.app import app

    app.config["TESTING"] = True

    # 在 mock 生效期间完成全部请求（三个 AI 节点确定性返回）
    with (
        patch("skill.orchestrator.nodes.ocr_node.ocr_extract_invoice") as m_ocr,
        patch("skill.orchestrator.nodes.anomaly_node.detect_anomaly") as m_anom,
        patch("skill.orchestrator.nodes.classify_node.classify_and_check_limit") as m_cls,
    ):
        m_ocr.return_value = SAMPLE_OCR
        m_anom.return_value = SAMPLE_ANOMALY
        m_cls.return_value = SAMPLE_CLASSIFY

        c = app.test_client()

        # ② 员工移动端登录
        _hr("② 员工移动端登录（EMP-2026 / 123456）")
        r = c.post("/api/auth/login", json={"account": "EMP-2026", "password": "123456"})
        j = r.get_json()
        assert r.status_code == 200 and j.get("ok"), f"登录失败: {j}"
        print(f"  ok={j['ok']} role={j['role']} name={j['name']}")

        # ③ 员工上传发票（移动端 /upload，FormData，发票图片为真实文件）
        _hr("③ 员工上传发票 → AI 检查")
        with open(img_path, "rb") as fp:
            r = c.post(
                "/upload",
                data={
                    "file": (fp, "invoice.png"),
                    "apply_amount": str(APPLY_AMOUNT),
                    "apply_date": APPLY_DATE,
                    "reason": REASON,
                    "expense_category": EXPENSE_CATEGORY,
                    "ticket_type": "发票",
                },
                content_type="multipart/form-data",
            )
        j = r.get_json()
        assert r.status_code == 200, f"上传失败: {j}"
        rid = j["_request_id"]
        print(f"  上传状态={j['status']}  request_id={rid}")
        print(f"  AI-OCR 发票号码={j.get('ocr_result', {}).get('发票号码')}")
        print(f"  AI-异常结论={j.get('anomaly_result', {}).get('总体结论')}")
        cr = j.get("classify_result", {})
        print(f"  AI-分类={cr.get('费用分类')} 限额={cr.get('分类限额')} 超限={cr.get('是否超限')}")

        # ④ 员工提交审批（移动端 /api/reimbursement/<id>/update）
        _hr("④ 员工提交审批（建单，进入待审）")
        r = c.post(
            f"/api/reimbursement/{rid}/update",
            json={
                "apply_amount": str(APPLY_AMOUNT),
                "apply_date": APPLY_DATE,
                "expense_category": EXPENSE_CATEGORY,
                "reason": REASON,
            },
        )
        j = r.get_json()
        assert r.status_code == 200, f"提交失败: {j}"
        print(f"  workflow_status={j['workflow_status']}  ai_status={j['ai_status']}")

        # ⑤ 主管移动端登录 + 审批通过
        _hr("⑤ 主管移动端登录（APR-001）并审批「通过」")
        r = c.post("/api/auth/login", json={"account": "APR-001", "password": "123456"})
        assert r.get_json().get("ok")
        r = c.post(
            "/api/approve", json={"request_id": rid, "action": "通过", "comment": "同意，票据合规"}
        )
        j = r.get_json()
        assert r.status_code == 200 and j.get("status") == "ok", f"审批失败: {j}"
        print(f"  workflow_status={j['data']['workflow_status']}  审批人=李总")

        # ⑥ 财务归档（FIN-001，职责分离：仅归档）
        _hr("⑥ 财务归档（FIN-001）")
        r = c.post("/api/auth/login", json={"account": "FIN-001", "password": "123456"})
        assert r.get_json().get("ok")
        r = c.post("/api/finance", json={"request_id": rid, "action": "归档"})
        j = r.get_json()
        assert r.status_code == 200 and j.get("status") == "ok", f"归档失败: {j}"
        print(f"  workflow_status={j['data']['workflow_status']}  操作人=王会计")

        # ⑦ 出纳打款（FIN-002，须与归档人不同账号）
        _hr("⑦ 出纳打款（FIN-002）")
        r = c.post("/api/auth/login", json={"account": "FIN-002", "password": "123456"})
        assert r.get_json().get("ok")
        r = c.post("/api/finance", json={"request_id": rid, "action": "打款"})
        j = r.get_json()
        assert r.status_code == 200 and j.get("status") == "ok", f"打款失败: {j}"
        print(f"  workflow_status={j['data']['workflow_status']}  操作人=李出纳")

    # ⑧ 终态核对
    _hr("⑧ 全链路终态核对")
    from skill.utils.db_store import check_duplicate_invoice, get_reimbursement

    reb = get_reimbursement(rid)
    print(f"  报销单号={rid}")
    print(f"  工作流终态={reb.workflow_status}")
    print(f"  员工={reb.employee_id}  AI结论={reb.ai_status}  金额={reb.apply_amount}")
    print(f"  发票已标记报销(防重)={check_duplicate_invoice(INVOICE_NO)}")
    _hr("✅ 移动端全链路（上传→AI检查→审批→归档→打款）执行成功")


if __name__ == "__main__":
    main()
