/* ============================================
   财务终审工作台 — 前端交互
   ============================================ */
(function () {
    'use strict';

    var AI_MAP = {
        '通过': { cls: 'ai-pass', icon: '✓' },
        '预警': { cls: 'ai-warn', icon: '⚠️' },
        '拦截': { cls: 'ai-block', icon: '⛔' },
        '错误': { cls: 'ai-block', icon: '❌' },
    };
    var WS_MAP = {
        '待审批': { cls: 'status-pending', text: '⏳ 待审' },
        '审批中': { cls: 'status-inreview', text: '🔄 审批中(会签)' },
        '已通过': { cls: 'status-paid', text: '✓ 已通过(待归档)' },
        '已驳回': { cls: 'status-rejected', text: '✕ 已驳回' },
        '已归档': { cls: 'status-archived', text: '📦 已归档(待打款)' },
        '已发放': { cls: 'status-paid', text: '💰 已发放' },
    };

    function esc(s) {
        if (s === null || s === undefined) return '';
        return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }
    function money(v) {
        return '¥' + (v != null ? Number(v).toFixed(2) : '—');
    }
    function aiTag(status) {
        var m = AI_MAP[status] || { cls: '', icon: '' };
        return '<span class="tag ' + m.cls + '">' + m.icon + ' AI ' + esc(status) + '</span>';
    }
    function wsTag(ws) {
        var m = WS_MAP[ws] || { cls: '', text: esc(ws) };
        return '<span class="tag ' + m.cls + '">' + m.text + '</span>';
    }

    /* ── 列表加载 ── */
    function fetchList() {
        fetch('/api/finance/list').then(function (r) { return r.json(); }).then(function (data) {
            if (data.error) { document.getElementById('financeList').innerHTML = '<div class="empty-state"><div class="empty-text">' + esc(data.error) + '</div></div>'; return; }
            renderList(data.items || []);
            if (document.getElementById('financePendingCount')) document.getElementById('financePendingCount').textContent = data.pending_archive;
            if (document.getElementById('financeArchiveCount')) document.getElementById('financeArchiveCount').textContent = data.archived;
            if (document.getElementById('financePaidCount')) document.getElementById('financePaidCount').textContent = data.paid;
        }).catch(function () {
            document.getElementById('financeList').innerHTML = '<div class="empty-state"><div class="empty-text">加载失败</div></div>';
        });
    }

    function renderList(items) {
        var box = document.getElementById('financeList');
        if (!items.length) {
            box.innerHTML = '<div class="empty-state"><div class="empty-icon">📦</div><div class="empty-text">暂无待终审的报销单</div></div>';
            return;
        }
        box.innerHTML = items.map(renderItem).join('');
    }

    function renderItem(it) {
        var typeTag = it.ticket_type === '行程单'
            ? '<span class="tag itinerary">🚕 行程单</span>'
            : '<span class="tag invoice">🧾 发票</span>';

        var actions = '<button class="btn-mini primary" onclick="viewDetail(\'' + esc(it.request_id) + '\')">📄 查看明细</button>';
        if (it.workflow_status === '已通过') {
            actions += '<button class="btn-mini success" onclick="openAction(\'' + esc(it.request_id) + '\',\'归档\')">📦 确认归档</button>';
        } else if (it.workflow_status === '已归档') {
            actions += '<button class="btn-mini primary" style="border-color:var(--green);color:var(--green);" onclick="openAction(\'' + esc(it.request_id) + '\',\'打款\')">💰 发起打款</button>';
        }

        return '<div class="reimburse-item">' +
            '<div class="reimburse-item-head">' +
                '<div><div class="reimburse-item-id">报销单号：' + esc(it.request_id) + '</div>' +
                '<div>' + esc(it.reason) + '</div></div>' +
                '<div class="reimburse-item-amount">' + money(it.apply_amount) + '</div>' +
            '</div>' +
            '<div class="reimburse-item-meta">' +
                '<span><span class="meta-key">提交人:</span><span class="meta-value">' + esc(it.employee_name) + '</span></span>' +
                '<span><span class="meta-key">费用类型:</span><span class="meta-value">' + esc(it.expense_category || '—') + '</span></span>' +
                '<span><span class="meta-key">AI 状态:</span><span class="meta-value">' + esc(it.ai_status) + '</span></span>' +
            '</div>' +
            '<div class="ai-summary-box">🤖 <strong>AI 复核：</strong>' + esc(it.ai_summary) + '</div>' +
            '<div class="reimburse-item-footer">' +
                '<div class="reimburse-item-tags">' + typeTag + aiTag(it.ai_status) + wsTag(it.workflow_status) + '</div>' +
                '<div class="reimburse-item-actions">' + actions + '</div>' +
            '</div>' +
        '</div>';
    }

    /* ── 详情弹窗（与审批共用结构） ── */
    window.viewDetail = function (requestId) {
        fetch('/api/reimbursement/' + encodeURIComponent(requestId)).then(function (r) { return r.json(); }).then(function (d) {
            if (d.error) { alert(d.error); return; }
            renderDetail(d);
            document.getElementById('detailModal').style.display = 'flex';
        });
    };

    function renderDetail(d) {
        var route = d.route || {};
        var html = '<div class="info-grid">' +
            infoItem('报销单号', d.request_id) +
            infoItem('票据类型', d.ticket_type || '发票') +
            infoItem('申请金额', money(d.apply_amount)) +
            infoItem('费用类型', d.expense_category || '—') +
            infoItem('提交人', d.employee_name) +
            infoItem('工作流状态', d.workflow_status) +
            infoItem('审批层级', (route['审批人'] || '—') + (route['需要会签'] ? '（需会签）' : '')) +
        '</div>';

        if (d.invoices && d.invoices.length) {
            html += '<h3 style="margin:18px 0 8px;font-size:15px;">🧾 关联发票</h3><div class="info-grid">';
            d.invoices.forEach(function (inv) {
                html += infoItem('发票号 ' + esc(inv.invoice_number), money(inv.invoice_amount) + ' · ' + esc(inv.seller_name || ''));
            });
            html += '</div>';
        }

        if (d.approval_records && d.approval_records.length) {
            html += '<h3 style="margin:18px 0 8px;font-size:15px;">📝 审批记录</h3>';
            d.approval_records.forEach(function (a) {
                html += '<div class="anomaly-item info"><span class="anomaly-type">' + esc(a.approver_name || a.approver_id) +
                    '</span><span class="anomaly-desc">' + esc(a.approval_node) + ' · ' + esc(a.action) +
                    (a.comment ? '：' + esc(a.comment) : '') + '</span>' +
                    '<span class="anomaly-severity info">' + esc(a.action_time) + '</span></div>';
            });
        }

        document.getElementById('detailBody').innerHTML = html;
    }

    function infoItem(k, v) {
        return '<div class="info-item"><div class="info-key">' + esc(k) + '</div><div class="info-value">' + esc(v) + '</div></div>';
    }

    /* ── 财务操作弹窗 ── */
    var pendingAction = null;
    window.openAction = function (requestId, action) {
        pendingAction = { requestId: requestId, action: action };
        var hint = {
            '归档': '确认归档报销单（报销单号：' + requestId + '）？归档后方可发起打款。',
            '打款': '确认打款报销单（报销单号：' + requestId + '）？打款后费用将发放给员工。',
        }[action] || '';
        document.getElementById('actionTitle').textContent = '财务 · ' + action;
        document.getElementById('actionHint').textContent = hint;
        document.getElementById('actionComment').value = '';
        document.getElementById('actionConfirm').textContent = '确认' + action;
        document.getElementById('actionModal').style.display = 'flex';
    };

    document.getElementById('actionConfirm').addEventListener('click', function () {
        if (!pendingAction) return;
        var comment = document.getElementById('actionComment').value.trim();
        var btn = this;
        btn.disabled = true;
        var csrfMeta = document.querySelector('meta[name="csrf-token"]');
        var csrfToken = csrfMeta ? csrfMeta.content : '';
        fetch('/api/finance', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken },
            body: JSON.stringify({ request_id: pendingAction.requestId, action: pendingAction.action, comment: comment }),
        }).then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
            .then(function (res) {
                if (!res.ok || res.d.error) {
                    alert('操作失败：' + (res.d.error || '未知错误'));
                } else {
                    closeModal('actionModal');
                    fetchList();
                }
            })
            .catch(function () { alert('请求失败，请重试'); })
            .finally(function () { btn.disabled = false; });
    });

    window.closeModal = function (id) {
        document.getElementById(id).style.display = 'none';
    };
    document.querySelectorAll('.modal-mask').forEach(function (mask) {
        mask.addEventListener('click', function (e) { if (e.target === mask) mask.style.display = 'none'; });
    });
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') { document.querySelectorAll('.modal-mask').forEach(function (m) { m.style.display = 'none'; }); }
    });

    fetchList();
})();
