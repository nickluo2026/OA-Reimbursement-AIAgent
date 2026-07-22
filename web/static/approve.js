/* ============================================
   审批领导工作台 — 前端交互
   ============================================ */
(function () {
    'use strict';

    /* 工号 → 姓名（与 web/app.py DEMO_ACCOUNTS 对齐，详情页展示 姓名（编码）） */
    var NAME_MAP = {
        'FIN-001': '王会计', 'FIN-002': '李出纳',
        'APR-001': '李总', 'EMP-2026': '张三', 'ADM-001': '赵管理'
    };
    function displayName(id) {
        if (!id) return '';
        return NAME_MAP[id] ? NAME_MAP[id] + '（' + id + '）' : id;
    }

    var AI_MAP = {
        '通过': { cls: 'ai-pass', icon: '✓' },
        '预警': { cls: 'ai-warn', icon: '⚠️' },
        '拦截': { cls: 'ai-block', icon: '⛔' },
        '错误': { cls: 'ai-block', icon: '❌' },
    };
    var WS_MAP = {
        '待审批': { cls: 'status-pending', text: '⏳ 待审' },
        '审批中': { cls: 'status-inreview', text: '🔄 审批中(会签)' },
        '待复核': { cls: 'status-paid', text: '✓ 待复核' },
        '已驳回': { cls: 'status-rejected', text: '✕ 已驳回' },
        '已复核并归档': { cls: 'status-archived', text: '📦 已复核并归档' },
        '已打款': { cls: 'status-paid', text: '💰 已打款' },
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
        fetch('/api/approve/list').then(function (r) { return r.json(); })        .then(function (data) {
            if (data.error) { document.getElementById('approveList').innerHTML = '<div class="empty-state"><div class="empty-text">' + esc(data.error) + '</div></div>'; return; }
            renderList(data.items || []);
            document.getElementById('approvePendingCount').textContent = data.count;
            if (document.getElementById('approveDoneCount')) {
                document.getElementById('approveDoneCount').textContent = data.done_this_month || 0;
            }
        }).catch(function () {
            document.getElementById('approveList').innerHTML = '<div class="empty-state"><div class="empty-text">加载失败</div></div>';
        });
    }

    function renderList(items) {
        var box = document.getElementById('approveList');
        if (!items.length) {
            box.innerHTML = '<div class="empty-state"><div class="empty-icon">📭</div><div class="empty-text">暂无待审报销单</div></div>';
            return;
        }
        box.innerHTML = items.map(renderItem).join('');
    }

    function renderItem(it) {
        var route = it.route || {};
        var level = route['审批级别'] || 1;
        var levelText = '🧭 审批层级：' + esc(route['审批人'] || '审批领导') +
            (route['级别描述'] ? '（' + esc(route['级别描述']) + '）' : '');
        var cosign = it.needs_countersign
            ? '<span class="tag cosign">🤝 需会签' +
              (it.countersign_passed ? '（' + it.countersign_passed + ' 人已签）' : '') + '</span>'
            : '';
        var typeTag = it.ticket_type === '行程单'
            ? '<span class="tag itinerary">🚕 行程单</span>'
            : '<span class="tag invoice">🧾 发票</span>';

        var transferredTag = it.transferred ? '<span class="tag status-transferred">↪️ 已转审</span>' : '';

        var actions = '<button class="btn-mini primary" onclick="viewDetail(\'' + esc(it.request_id) + '\')">📄 查看明细</button>';
        if (!it.transferred) {
            actions += '<button class="btn-mini warning" onclick="openAction(\'' + esc(it.request_id) + '\',\'转审\')">↪️ 转审</button>' +
                '<button class="btn-mini danger" onclick="openAction(\'' + esc(it.request_id) + '\',\'驳回\')">✕ 驳回</button>' +
                '<button class="btn-mini success" onclick="openAction(\'' + esc(it.request_id) + '\',\'通过\')">✓ 通过</button>';
        }

        return '<div class="reimburse-item">' +
            '<div class="reimburse-item-head">' +
                '<div><div class="reimburse-item-id">报销单号：' + esc(it.request_id) + '</div>' +
                '<div>' + esc(it.reason) + '</div></div>' +
                '<div class="reimburse-item-amount">' + money(it.apply_amount) + '</div>' +
            '</div>' +
            '<div class="reimburse-item-meta">' +
                '<span><span class="meta-key">提交人:</span><span class="meta-value">' + esc(it.employee_name) + '</span></span>' +
                '<span><span class="meta-key">提交时间:</span><span class="meta-value">' + esc(it.created_at) + '</span></span>' +
                '<span><span class="meta-key">费用类型:</span><span class="meta-value">' + esc(it.expense_category || '—') + '</span></span>' +
            '</div>' +
            '<div class="ai-summary-box">🤖 <strong>AI 校验：</strong>' + esc(it.ai_summary) + '</div>' +
            '<div class="approval-level-bar level-' + level + '">' + levelText + '</div>' +
            '<div class="reimburse-item-footer">' +
                '<div class="reimburse-item-tags">' + typeTag + aiTag(it.ai_status) + wsTag(it.workflow_status) + cosign + transferredTag + '</div>' +
                '<div class="reimburse-item-actions">' + actions + '</div>' +
            '</div>' +
        '</div>';
    }

    /* ── 详情弹窗 ── */
    window.viewDetail = function (requestId) {
        fetch('/api/reimbursement/' + encodeURIComponent(requestId)).then(function (r) { return r.json(); }).then(function (d) {
            if (d.error) { alert(d.error); return; }
            renderDetail(d);
            document.getElementById('detailModal').style.display = 'flex';
        });
    };

    function renderDetail(d) {
        var route = d.route || {};
        // 审批人：取首条「通过」记录的审批人编码
        var approverId = '';
        if (d.approval_records && d.approval_records.length) {
            for (var i = 0; i < d.approval_records.length; i++) {
                if (d.approval_records[i].action === '通过') {
                    approverId = d.approval_records[i].approver_id;
                    break;
                }
            }
        }
        var html = '<div class="info-grid">' +
            infoItem('报销单号', d.request_id) +
            infoItem('票据类型', d.ticket_type || '发票') +
            infoItem('申请金额', money(d.apply_amount)) +
            infoItem('费用类型', d.expense_category || '—') +
            infoItem('提交人', displayName(d.employee_id)) +
            infoItem('审批人', approverId ? displayName(approverId) : '—') +
            infoItem('复核人', d.archived_by ? displayName(d.archived_by) : '—') +
            infoItem('打款人', d.paid_by ? displayName(d.paid_by) : '—') +
            infoItem('工作流状态', d.workflow_status) +
            infoItem('审批层级', (route['审批人'] || '—') + (route['需要会签'] ? '（需会签）' : '')) +
        '</div>';

        // 发票
        if (d.invoices && d.invoices.length) {
            html += '<h3 style="margin:18px 0 8px;font-size:15px;">🧾 关联发票</h3><div class="info-grid">';
            d.invoices.forEach(function (inv) {
                var invNo = inv.invoice_number;
                var invLabel = invNo ? '发票号 ' + esc(invNo) : '发票号：缺失';
                html += infoItem(invLabel, money(inv.invoice_amount));
            });
            html += '</div>';
        }

        // 审批记录
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

    /* ── 审批操作弹窗 ── */
    var pendingAction = null;
    window.openAction = function (requestId, action) {
        pendingAction = { requestId: requestId, action: action };
        var hint = {
            '通过': '确认通过报销单（报销单号：' + requestId + '）？',
            '驳回': '确认驳回报销单（报销单号：' + requestId + '）？请填写驳回意见。',
            '转审': '确认将报销单（报销单号：' + requestId + '）转交上级审批？请填写转审说明。',
        }[action] || '';
        document.getElementById('actionTitle').textContent = '审批 · ' + action;
        document.getElementById('actionHint').textContent = hint;
        document.getElementById('actionComment').value = '';
        document.getElementById('actionConfirm').textContent = '确认' + action;
        document.getElementById('actionModal').style.display = 'flex';
    };

    document.getElementById('actionConfirm').addEventListener('click', function () {
        if (!pendingAction) return;
        var comment = document.getElementById('actionComment').value.trim();
        if ((pendingAction.action === '驳回' || pendingAction.action === '转审') && !comment) {
            alert(pendingAction.action + '必须填写意见');
            return;
        }
        var btn = this;
        btn.disabled = true;
        var csrfMeta = document.querySelector('meta[name="csrf-token"]');
        var csrfToken = csrfMeta ? csrfMeta.content : '';
        fetch('/api/approve', {
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

    /* ── 弹窗关闭 ── */
    window.closeModal = function (id) {
        document.getElementById(id).style.display = 'none';
    };
    document.querySelectorAll('.modal-mask').forEach(function (mask) {
        mask.addEventListener('click', function (e) { if (e.target === mask) mask.style.display = 'none'; });
    });
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') { document.querySelectorAll('.modal-mask').forEach(function (m) { m.style.display = 'none'; }); }
    });

    /* ── 初始化 ── */
    fetchList();
})();
