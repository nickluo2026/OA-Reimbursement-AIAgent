"""工作流功能测试：审批流转 + 财务发放（纯逻辑，不依赖 Web）

覆盖 design.md §2.5（主管）/ §2.6（财务终审与发放）与
approval_authority.yaml 的金额阶梯、会签规则。
"""

import pytest

from skill import workflow as wf
from skill.utils.db_store import (
    check_duplicate_invoice,
    save_invoice,
    save_reimbursement,
)


def _make(rid, amount, employee="EMP-2026"):
    save_reimbursement(
        request_id=rid,
        employee_id=employee,
        apply_amount=amount,
        apply_date="2026-07-14",
        reason="测试报销",
        expense_category="差旅",
    )
    save_invoice({"发票号码": "INV-" + rid, "发票金额": amount, "销售方名称": "X"}, rid, "")


# ── 审批路由（金额阶梯 + 会签） ──
class TestComputeRoute:
    def test_small_amount_level1(self):
        r = wf.compute_route(2000)
        assert r["审批级别"] == 1
        assert r["审批人"] == "直属领导"
        assert r["需要会签"] is False

    def test_mid_amount_level2(self):
        r = wf.compute_route(5000)
        assert r["审批级别"] == 2
        assert r["审批人"] == "部门总监"

    def test_high_amount_level3(self):
        r = wf.compute_route(30000)
        assert r["审批级别"] == 3
        assert r["审批人"] == "VP/分管副总"

    def test_countersign_threshold(self):
        # 恰好 10000：触发会签（>= 阈值）
        r = wf.compute_route(10000)
        assert r["需要会签"] is True
        assert r["最少签核人数"] == 2

    def test_no_countersign_below_threshold(self):
        r = wf.compute_route(9999)
        assert r["需要会签"] is False

    def test_ceo_level4(self):
        r = wf.compute_route(120000)
        assert r["审批级别"] == 4
        assert r["审批人"] == "CEO"


# ── 审批决策 ──
class TestSubmitApproval:
    def test_pass(self, sample_reimbursement):
        result = wf.submit_approval(sample_reimbursement, "APR-001", "李总", action="通过")
        assert result["workflow_status"] == wf.WS_APPROVED
        assert result["transferred"] is False

    def test_reject(self, sample_reimbursement):
        result = wf.submit_approval(
            sample_reimbursement, "APR-001", "李总", action="驳回", comment="票据不全"
        )
        assert result["workflow_status"] == wf.WS_REJECTED

    def test_transfer_moves_out_of_pending(self, sample_reimbursement):
        # 转审前出现在主管待审批列表
        assert any(
            r.request_id == sample_reimbursement for r in wf.list_pending()
        )
        result = wf.submit_approval(
            sample_reimbursement, "APR-001", "李总", action="转审", comment="转上级"
        )
        # 转审后工作流状态置为「已转审」，并从主管待审批列表移除
        assert result["workflow_status"] == wf.WS_TRANSFERRED
        assert result["transferred"] is True
        assert not any(
            r.request_id == sample_reimbursement for r in wf.list_pending()
        )
        # 转审后不可被原主管重复审批
        with pytest.raises(ValueError):
            wf.submit_approval(
                sample_reimbursement, "APR-001", "李总", action="通过"
            )

    def test_reject_then_approve_raises(self, sample_reimbursement):
        wf.submit_approval(sample_reimbursement, "APR-001", "李总", action="驳回")
        with pytest.raises(ValueError):
            wf.submit_approval(sample_reimbursement, "APR-001", "李总", action="通过")

    def test_unknown_action_raises(self, sample_reimbursement):
        with pytest.raises(ValueError):
            wf.submit_approval(sample_reimbursement, "APR-001", "李总", action="未知")

    def test_missing_request_raises(self, fresh_db):
        with pytest.raises(ValueError):
            wf.submit_approval("REQ-NOPE", "APR-001", "李总", action="通过")


# ── 会签流程 ──
class TestCountersign:
    def test_two_signers_required(self, fresh_db):
        rid = "REQ-CS-1"
        _make(rid, 60000)  # >= 10000 触发会签
        first = wf.submit_approval(rid, "APR-001", "李总", action="通过")
        assert first["workflow_status"] == wf.WS_IN_REVIEW
        assert first["countersign_passed"] == 1

        second = wf.submit_approval(rid, "APR-002", "王总", action="通过")
        assert second["workflow_status"] == wf.WS_APPROVED
        assert second["countersign_passed"] == 2

    def test_single_signer_stays_in_review(self, fresh_db):
        rid = "REQ-CS-2"
        _make(rid, 80000)
        wf.submit_approval(rid, "APR-001", "李总", action="通过")
        # 仅一人签，仍在审批中，不在财务列表
        assert wf.list_for_finance() == []


# ── 财务终审与发放 ──
class TestSubmitFinance:
    def test_archive_requires_approved(self, sample_reimbursement):
        with pytest.raises(ValueError):
            wf.submit_finance(sample_reimbursement, "FIN-001", "王会计", action="归档")

    def test_pay_requires_archived(self, sample_reimbursement):
        wf.submit_approval(sample_reimbursement, "APR-001", "李总", action="通过")
        with pytest.raises(ValueError):
            wf.submit_finance(sample_reimbursement, "FIN-001", "王会计", action="打款")

    def test_archive_then_pay(self, sample_reimbursement):
        wf.submit_approval(sample_reimbursement, "APR-001", "李总", action="通过")
        archived = wf.submit_finance(sample_reimbursement, "FIN-001", "王会计", action="归档")
        assert archived["workflow_status"] == wf.WS_ARCHIVED

        # 职责分离：打款须由出纳岗（FIN-002）执行，与归档人（FIN-001）不同
        paid = wf.submit_finance(sample_reimbursement, "FIN-002", "李出纳", action="打款")
        assert paid["workflow_status"] == wf.WS_PAID
        assert paid["archived_by"] == "FIN-001"
        assert paid["paid_by"] == "FIN-002"
        # 发票已标记报销（防重）— sample_reimbursement 创建的发票号为 88886666
        assert check_duplicate_invoice("88886666") is True

    def test_pay_idempotent_invoice(self, sample_reimbursement):
        wf.submit_approval(sample_reimbursement, "APR-001", "李总", action="通过")
        wf.submit_finance(sample_reimbursement, "FIN-001", "王会计", action="归档")
        wf.submit_finance(sample_reimbursement, "FIN-002", "李出纳", action="打款")
        # 再次打款：状态已是已打款，应报错（不可重复打款）
        with pytest.raises(ValueError):
            wf.submit_finance(sample_reimbursement, "FIN-002", "李出纳", action="打款")

    def test_segregation_violation_same_person(self, sample_reimbursement):
        """职责分离：同一人既归档又打款须被拦截（舞弊风险）"""
        wf.submit_approval(sample_reimbursement, "APR-001", "李总", action="通过")
        wf.submit_finance(sample_reimbursement, "FIN-001", "王会计", action="归档")
        with pytest.raises(ValueError) as exc:
            wf.submit_finance(sample_reimbursement, "FIN-001", "王会计", action="打款")
        assert "舞弊" in str(exc.value) or "归档人" in str(exc.value)

    def test_pay_by_different_person(self, sample_reimbursement):
        """跨人打款（归档 FIN-001 / 打款 FIN-002）正常发放并落库"""
        wf.submit_approval(sample_reimbursement, "APR-001", "李总", action="通过")
        wf.submit_finance(sample_reimbursement, "FIN-001", "王会计", action="归档")
        paid = wf.submit_finance(sample_reimbursement, "FIN-002", "李出纳", action="打款")
        assert paid["workflow_status"] == wf.WS_PAID
        assert paid["archived_by"] == "FIN-001"
        assert paid["paid_by"] == "FIN-002"


# ── 列表查询 ──
class TestListQueries:
    def test_pending_excludes_approved(self, sample_reimbursement):
        assert len(wf.list_pending()) == 1
        wf.submit_approval(sample_reimbursement, "APR-001", "李总", action="通过")
        assert wf.list_pending() == []
        assert len(wf.list_for_finance()) == 1

    def test_list_by_employee(self, sample_reimbursement):
        items = wf.list_by_employee("EMP-2026")
        assert len(items) == 1
        assert wf.list_by_employee("EMP-OTHER") == []

    def test_get_detail(self, sample_reimbursement):
        detail = wf.get_detail(sample_reimbursement)
        assert detail["request_id"] == sample_reimbursement
        assert len(detail["invoices"]) == 1
        assert detail["invoices"][0]["invoice_number"] == "88886666"
        assert detail["route"]["审批级别"] == 1
        wf.submit_approval(sample_reimbursement, "APR-001", "李总", action="通过")
        detail2 = wf.get_detail(sample_reimbursement)
        assert len(detail2["approval_records"]) == 1
        assert detail2["approval_records"][0]["action"] == "通过"


# ── 统计 ──
class TestStats:
    def test_count_decisions_this_month(self, sample_reimbursement):
        before = wf.count_decisions_this_month("APR-001")
        wf.submit_approval(sample_reimbursement, "APR-001", "李总", action="通过")
        after = wf.count_decisions_this_month("APR-001")
        assert after == before + 1

    def test_count_by_status(self, sample_reimbursement):
        assert wf.count_by_status(wf.WS_PENDING) == 1
        wf.submit_approval(sample_reimbursement, "APR-001", "李总", action="通过")
        wf.submit_finance(sample_reimbursement, "FIN-001", "王会计", action="归档")
        wf.submit_finance(sample_reimbursement, "FIN-002", "李出纳", action="打款")
        assert wf.count_by_status(wf.WS_PAID) == 1
