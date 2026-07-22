/* 系统管理员工作台逻辑：系统配置 / 审计日志 / 用量统计
 * 数据全部来自后端 API（/api/admin/*），与原型 MOCK 解耦。
 *
 * [S-003] 所有动态数据拼接 innerHTML 前必须经 esc() 转义，防存储型 XSS。
 */

// ── Tab 切换 ──
function switchTab(tabId) {
    document.querySelectorAll('.tab-btn').forEach(function (b) {
        b.classList.toggle('active', b.dataset.tab === tabId);
    });
    document.querySelectorAll('.tab-panel').forEach(function (p) {
        p.style.display = (p.id === tabId) ? 'block' : 'none';
    });
    if (tabId === 'tab-audit') loadAudit();
    if (tabId === 'tab-usage') startUsageAutoRefresh();
    else stopUsageAutoRefresh();
}

// ── 用量统计自动刷新 ──
// DeepSeek / Vision API 调用在其它流程（如员工上传报销）完成后，用量统计
// 列表的时间与明细应同步更新，故在「用量统计」Tab 激活时定时拉取最新数据。
var USAGE_REFRESH_MS = 5000;
var usageRefreshTimer = null;

function startUsageAutoRefresh() {
    stopUsageAutoRefresh();
    loadUsage();
    usageRefreshTimer = setInterval(loadUsage, USAGE_REFRESH_MS);
}

function stopUsageAutoRefresh() {
    if (usageRefreshTimer) {
        clearInterval(usageRefreshTimer);
        usageRefreshTimer = null;
    }
}

// ── 工具函数 ──
var PRICE_INPUT_PER_1K = 0.001;
var PRICE_OUTPUT_PER_1K = 0.002;

function calcCostCny(promptTokens, completionTokens) {
    return (promptTokens / 1000) * PRICE_INPUT_PER_1K + (completionTokens / 1000) * PRICE_OUTPUT_PER_1K;
}

function formatTokens(n) {
    if (n >= 1000000) return (n / 1000000).toFixed(2) + 'M';
    if (n >= 1000) return (n / 1000).toFixed(1) + 'K';
    return String(n);
}

function fmtInt(n) {
    return Number(n || 0).toLocaleString();
}

// [S-003] HTML 转义函数：防止存储型 XSS
function esc(s) {
    if (s === null || s === undefined) return '';
    return String(s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

// ═══════════════════════════════════════════════
// 系统配置
// ═══════════════════════════════════════════════
var CONFIG_SCHEMA = [];
var CONFIG_VALUES = {};

function loadConfig() {
    fetch('/api/admin/config')
        .then(function (r) { return r.json(); })
        .then(function (data) {
            CONFIG_SCHEMA = data.schema || [];
            CONFIG_VALUES = data.config || {};
            renderConfig();
        })
        .catch(function (e) { console.error('加载配置失败', e); });
}

function renderConfig() {
    var wrap = document.getElementById('configSections');
    var html = '';
    CONFIG_SCHEMA.forEach(function (grp) {
        html += '<div class="config-section"><h4>' + esc(grp.group) + '</h4>';
        grp.items.forEach(function (item) {
            var val = CONFIG_VALUES[item.key];
            html += '<div class="config-row" data-key="' + esc(item.key) + '" data-type="' + esc(item.type) + '">';
            var labelHtml = '<div class="cfg-label">' + esc(item.label);
            if (item.env) labelHtml += ' <span class="cfg-env">' + esc(item.env) + '</span>';
            labelHtml += '</div>';
            html += labelHtml;
            if (item.type === 'number') {
                html += '<input type="number" class="cfg-input" value="' + (val != null ? esc(val) : '') + '">';
                if (item.unit) html += '<span class="cfg-suffix">' + esc(item.unit) + '</span>';
            } else if (item.type === 'toggle') {
                var on = val ? ' on' : '';
                html += '<div class="cfg-toggle' + on + '" onclick="this.classList.toggle(\'on\')"></div>';
            } else if (item.type === 'secret') {
                html += '<div class="cfg-secret-wrap">';
                html += '<input type="password" class="cfg-input wide cfg-secret" value="' + (val != null ? esc(val) : '') + '" placeholder="留空则使用环境变量默认值">';
                html += '<button type="button" class="cfg-secret-btn" onclick="toggleSecretVis(this)" title="显示/隐藏">👁</button>';
                html += '</div>';
            } else if (item.type === 'text') {
                html += '<input type="text" class="cfg-input wide" value="' + (val != null ? esc(val) : '') + '">';
            }
            html += '</div>';
        });
        html += '</div>';
    });
    wrap.innerHTML = html;
}

function toggleSecretVis(btn) {
    var inp = btn.parentNode.querySelector('.cfg-secret');
    if (!inp) return;
    if (inp.type === 'password') {
        inp.type = 'text';
        btn.textContent = '🙈';
    } else {
        inp.type = 'password';
        btn.textContent = '👁';
    }
}

function collectConfig() {
    var items = {};
    document.querySelectorAll('#configSections .config-row').forEach(function (row) {
        var key = row.dataset.key;
        if (row.dataset.type === 'number') {
            var inp = row.querySelector('.cfg-input');
            items[key] = inp.value === '' ? null : Number(inp.value);
        } else if (row.dataset.type === 'toggle') {
            items[key] = row.querySelector('.cfg-toggle').classList.contains('on');
        } else if (row.dataset.type === 'text' || row.dataset.type === 'secret') {
            var t = row.querySelector('.cfg-input');
            items[key] = t ? t.value : '';
        }
    });
    return items;
}

function getCsrfToken() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.content : '';
}

function saveConfig() {
    var items = collectConfig();
    fetch('/api/admin/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': getCsrfToken() },
        body: JSON.stringify({ items: items }),
    })
        .then(function (r) { return r.json(); })
        .then(function (data) {
            if (data.status === 'ok') {
                CONFIG_VALUES = data.config;
                // 审计日志中已记录，刷新审计面板数据（下次打开时加载）
                alert('配置已保存，变更已记入审计日志。');
            } else {
                alert('保存失败：' + (data.error || '未知错误'));
            }
        })
        .catch(function (e) { alert('保存失败：' + e); });
}

function resetConfig() {
    if (!confirm('确认恢复所有配置为默认值？')) return;
    fetch('/api/admin/config/reset', { method: 'POST', headers: { 'X-CSRF-Token': getCsrfToken() } })
        .then(function (r) { return r.json(); })
        .then(function (data) {
            if (data.status === 'ok') {
                CONFIG_VALUES = data.config;
                renderConfig();
                alert('已恢复默认值，变更已记入审计日志。');
            } else {
                alert('恢复失败：' + (data.error || '未知错误'));
            }
        })
        .catch(function (e) { alert('恢复失败：' + e); });
}

// ═══════════════════════════════════════════════
// 审计日志
// ═══════════════════════════════════════════════
var AUDIT_ACTION_LABELS = {
    'SUBMIT': '📤 提交报销',
    'APPROVE': '✓ 审批通过',
    'REJECT': '✕ 审批驳回',
    'TRANSFER': '↪️ 转审',
    'ARCHIVE': '📦 归档',
    'PAYMENT_INIT': '💰 发起打款',
    'LOGIN': '🔓 登录',
    'LOGIN_FAILED': '⚠️ 登录失败',
    'CONFIG_UPDATE': '⚙️ 配置更新',
    'RULE_TOGGLE': '🚦 规则切换',
    'PERMISSION_GRANT': '👥 权限授予'
};

function loadAudit() {
    fetch('/api/admin/audit')
        .then(function (r) { return r.json(); })
        .then(function (data) { renderAuditLog(data.items || []); })
        .catch(function (e) { console.error('加载审计日志失败', e); });
}

function renderAuditLog(items) {
    var html = '';
    items.forEach(function (r) {
        var resultPill = r.result === '成功'
            ? '<span class="status-pill success">' + esc(r.result) + '</span>'
            : '<span class="status-pill error">' + esc(r.result) + '</span>';
        var actionLabel = AUDIT_ACTION_LABELS[r.action] || esc(r.action);
        html += '<tr>' +
            '<td class="audit-time">' + esc(r.time) + '</td>' +
            '<td class="audit-user">' + esc(r.user) + '</td>' +
            '<td class="audit-role">' + esc(r.role) + '</td>' +
            '<td><span class="audit-action">' + actionLabel + '</span></td>' +
            '<td class="audit-target" title="' + esc(r.target) + '">' + esc(r.target) + '</td>' +
            '<td>' + resultPill + '</td>' +
            '<td class="audit-ip">' + esc(r.ip) + '</td>' +
        '</tr>';
    });
    if (!items.length) {
        html = '<tr><td colspan="7" style="text-align:center;color:var(--text-light);padding:24px;">暂无审计记录</td></tr>';
    }
    document.getElementById('auditLogBody').innerHTML = html;
}

// ═══════════════════════════════════════════════
// 用量统计
// ═══════════════════════════════════════════════
var USAGE_CACHE = { daily: [], by_type: [], records: [] };

function loadUsage() {
    fetch('/api/admin/usage')
        .then(function (r) { return r.json(); })
        .then(function (data) {
            USAGE_CACHE.daily = data.daily || [];
            USAGE_CACHE.by_type = data.by_type || [];
            USAGE_CACHE.records = data.records || [];
            renderUsageOverview(data.overview || {});
            renderUsageDaily();
            renderUsageByType();
            populateDateFilter();
            renderUsageRecords();
        })
        .catch(function (e) { console.error('加载用量统计失败', e); });
}

function renderUsageOverview(o) {
    var successCount = o.total_calls - o.error_count;
    var html = '';
    html += '<div class="metric-card"><div class="metric-icon">📋</div>' +
        '<div class="metric-value blue">' + esc(fmtInt(o.total_calls)) + '</div>' +
        '<div class="metric-label">总调用次数</div>' +
        '<div class="metric-sub">成功 ' + esc(fmtInt(successCount)) + ' · 失败 ' + esc(String(o.error_count || 0)) + '</div></div>';
    html += '<div class="metric-card"><div class="metric-icon">🔢</div>' +
        '<div class="metric-value purple">' + esc(formatTokens(o.total_tokens || 0)) + '</div>' +
        '<div class="metric-label">Token 总量</div>' +
        '<div class="metric-sub">输入 ' + esc(formatTokens(o.total_prompt_tokens || 0)) + ' · 输出 ' + esc(formatTokens(o.total_completion_tokens || 0)) + '</div></div>';
    html += '<div class="metric-card"><div class="metric-icon">💰</div>' +
        '<div class="metric-value green">¥' + esc((o.estimated_cost_cny || 0).toFixed(2)) + '</div>' +
        '<div class="metric-label">预估费用 (CNY)</div>' +
        '<div class="metric-sub">按 DeepSeek-V4-Flash 定价</div></div>';
    html += '<div class="metric-card"><div class="metric-icon">⚡</div>' +
        '<div class="metric-value orange">' + esc(((o.avg_latency_ms || 0) / 1000).toFixed(1)) + 's</div>' +
        '<div class="metric-label">平均延迟</div>' +
        '<div class="metric-sub">成功率 ' + esc(String(o.success_rate || 0)) + '%</div></div>';
    document.getElementById('usageOverview').innerHTML = html;
}

function renderUsageDaily() {
    var daily = USAGE_CACHE.daily;
    var maxTokens = daily.reduce(function (m, d) { return Math.max(m, d.tokens); }, 0) || 1;
    var barsHtml = '';
    var labelsHtml = '';
    daily.forEach(function (d) {
        var heightPct = Math.max(4, (d.tokens / maxTokens * 100));
        barsHtml += '<div class="chart-bar-wrap">' +
            '<div class="chart-bar" style="height:' + heightPct.toFixed(0) + '%;">' +
                '<span class="bar-calls">' + esc(String(d.calls)) + '</span>' +
                '<span class="bar-tip">' + esc(d.date) + ' · ' + esc(String(d.calls)) + ' 次调用 · ' + esc(formatTokens(d.tokens)) + ' tokens</span>' +
            '</div></div>';
        labelsHtml += '<span>' + esc(d.date) + '</span>';
    });
    document.getElementById('usageDailyBars').innerHTML = barsHtml || '<div style="color:var(--text-light);">暂无数据</div>';
    document.getElementById('usageDailyLabels').innerHTML = labelsHtml;
}

function renderUsageByType() {
    var list = USAGE_CACHE.by_type;
    var totalTokens = list.reduce(function (s, t) { return s + t.tokens; }, 0) || 1;
    var colors = ['var(--primary)', 'var(--purple)', 'var(--orange)', 'var(--teal)', 'var(--green)'];
    var html = '';
    list.forEach(function (t, i) {
        var pct = (t.tokens / totalTokens * 100).toFixed(1);
        html += '<div class="type-row">' +
            '<div class="type-row-head">' +
                '<span class="type-name">' + esc(t.type) + '</span>' +
                '<span class="type-stats">' + esc(String(t.calls)) + ' 次 · 输入 ' + esc(formatTokens(t.prompt_tokens)) +
                ' · 输出 ' + esc(formatTokens(t.completion_tokens)) + ' · ¥' + esc((t.cost || 0).toFixed(2)) + ' · ' + esc(pct) + '%</span>' +
            '</div>' +
            '<div class="type-bar-track"><div class="type-bar-fill" style="width:' + esc(pct) + '%; background:' + colors[i % colors.length] + ';"></div></div>' +
            '</div>';
    });
    document.getElementById('usageByType').innerHTML = html || '<div style="color:var(--text-light);">暂无数据</div>';
}

function populateDateFilter() {
    var sel = document.getElementById('filterDateRange');
    var current = sel.value;
    sel.innerHTML = '<option value="">全部</option>';
    USAGE_CACHE.daily.forEach(function (d) {
        var opt = document.createElement('option');
        opt.value = d.date;
        opt.textContent = d.date;
        sel.appendChild(opt);
    });
    sel.value = current;
}

function getFilteredRecords() {
    var dateFilter = document.getElementById('filterDateRange').value;
    var typeFilter = document.getElementById('filterCallType').value;
    var statusFilter = document.getElementById('filterStatus').value;
    return USAGE_CACHE.records.filter(function (r) {
        if (dateFilter && r.time.substring(5, 10) !== dateFilter) return false;
        if (typeFilter && r.call_type !== typeFilter) return false;
        if (statusFilter && r.status !== statusFilter) return false;
        return true;
    });
}

function renderUsageRecords() {
    var records = getFilteredRecords();
    var wrap = document.getElementById('usageRecords');
    if (!records.length) {
        wrap.innerHTML = '<div class="usage-empty">📭 没有符合条件的调用记录</div>';
        return;
    }
    var html = '<table class="data-table"><thead><tr>' +
        '<th>时间</th><th>Request ID</th><th>调用类型</th><th>模型</th>' +
        '<th>输入Token</th><th>输出Token</th><th>总Token</th><th>延迟</th><th>状态</th><th>费用</th>' +
        '</tr></thead><tbody>';
    records.forEach(function (r) {
        var cost = calcCostCny(r.prompt_tokens, r.completion_tokens);
        var statusCls = r.status === '成功' ? 'success' : 'error';
        var latency = r.latency_ms === 0 ? '—' : (r.latency_ms + 'ms');
        html += '<tr>' +
            '<td style="white-space:nowrap;">' + esc(r.time) + '</td>' +
            '<td style="font-family:monospace;font-size:12px;">' + esc(r.request_id) + '</td>' +
            '<td>' + esc(r.call_type) + '</td>' +
            '<td>' + esc(r.model) + '</td>' +
            '<td>' + esc(fmtInt(r.prompt_tokens)) + '</td>' +
            '<td>' + esc(fmtInt(r.completion_tokens)) + '</td>' +
            '<td>' + esc(fmtInt(r.total_tokens)) + '</td>' +
            '<td>' + esc(latency) + '</td>' +
            '<td><span class="status-pill ' + statusCls + '">' + esc(r.status) + '</span></td>' +
            '<td style="font-weight:600;color:var(--green);">¥' + esc(cost.toFixed(4)) + '</td>' +
            '</tr>';
    });
    html += '</tbody></table>';
    wrap.innerHTML = html;
}

// ── 初始化 ──
document.addEventListener('DOMContentLoaded', function () {
    switchTab('tab-admin');  // 建立默认标签页（含面板 active 状态）
    loadConfig();
});
