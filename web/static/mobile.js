/* ============================================
   OA 报销助手 · iOS 移动端（PWA）交互脚本
   接线后端：/api/auth/login · /upload · /api/deepseek/status
            /api/reimbursement/<id>/update · /api/my · /api/reimbursement/<id>
   ============================================ */
(function () {
    'use strict';

    /* ───────────────── 状态时钟 ───────────────── */
    function updateClock() {
        var d = new Date(), h = d.getHours(), m = d.getMinutes();
        var el = document.getElementById('sbTime');
        if (el) el.textContent = h + ':' + (m < 10 ? '0' : '') + m;
    }
    updateClock();
    setInterval(updateClock, 15000);

    /* ───────────────── 全局状态 ───────────────── */
    var currentTicketType = '';
    var lastCheckPassed = false;
    var lastRequestId = '';
    var dsDisabled = false;          // DeepSeek 是否停用
    var isDisabledMode = false;      // 停用态标记：关闭提示 Sheet 后进入人工填写（与 Web 端一致）
    var lastDisabledSummary = '';    // 停用态：保存后端返回的统一停用说明
    // 本地兜底文案：仅当后端未返回 summary 时启用；运行期文案统一由后端 config.DEEPSEEK_DISABLED_MSG 下发
    var DISABLED_MSG_FALLBACK = 'DeepSeek 大模型已停用（系统配置），请联系系统管理员启用DeepSeek大模型或者人工填写报销单';
    var selectedFile = null;
    var isLoggedIn = document.body.getAttribute('data-logged-in') === 'true';
    var currentRole = document.body.getAttribute('data-role') || 'employee';
    var currentAccount = document.body.getAttribute('data-account') || '';

    /* ───────────────── 角色定义（对齐 prototype_ios.html） ───────────────── */
    var ROLES = {
        employee:       { icon: '👤', name: '员工',       account: 'EMP-2026' },
        approver:       { icon: '👔', name: '主管',       account: 'APR-001' },
        finance_review: { icon: '💼', name: '财务',       account: 'FIN-001' },
        finance_pay:    { icon: '🏦', name: '出纳',       account: 'FIN-002' },
        admin:          { icon: '⚙️', name: '系统管理员', account: 'ADM-001' }
    };
    var ROLE_TABS = {
        employee: ['reimburse', 'my'],
        approver: ['approve'],
        finance_review: ['fin-review'],
        finance_pay: ['fin-pay'],
        admin: ['admin', 'audit', 'usage', 'guide']
    };
    var TAB_META = {
        'reimburse': { icon: '📝', label: '报销申请' },
        'my': { icon: '📂', label: '我的报销' },
        'approve': { icon: '📥', label: '待审工作台' },
        'fin-review': { icon: '💼', label: '财务' },
        'fin-pay': { icon: '🏦', label: '出纳' },
        'admin': { icon: '⚙️', label: '系统配置' },
        'audit': { icon: '📜', label: '审计日志' },
        'usage': { icon: '📊', label: '用量统计' },
        'guide': { icon: '📘', label: '使用指南' }
    };
    var ALL_TABS = ['reimburse', 'my', 'approve', 'fin-review', 'fin-pay', 'admin', 'audit', 'usage', 'guide'];

    /* ───────────────── 流水线步骤（与原型一致） ───────────────── */
    var INVOICE_STEPS = [
        { icon: '🔍', name: 'OCR 提取发票内容' },
        { icon: '⚠️', name: '异常检测（规则引擎 + DeepSeek）' },
        { icon: '💰', name: '分类限额校验' },
        { icon: '✅', name: '发票查验' }
    ];
    var ITINERARY_STEPS = [
        { icon: '🔍', name: 'OCR 提取行程明细' },
        { icon: '⚠️', name: '行程单异常检测（日期/金额/字段）' },
        { icon: '🛣️', name: '行程合理性校验（金额匹配/日期范围）' }
    ];

    /* ───────────────── 工具函数 ───────────────── */
    function escHtml(s) {
        if (s == null) return '';
        return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }
    function money(v) {
        if (v == null || v === '') return '—';
        var n = Number(v);
        return isNaN(n) ? '—' : '¥' + n.toFixed(2);
    }
    function fmtDate(v) {
        if (!v) return '—';
        return String(v).replace('T', ' ').split(' ')[0] || '—';
    }
    function fmtTime(iso) {
        if (!iso) return '—';
        var s = String(iso).replace('T', ' ').replace(/\.\d+.*$/, '');
        var parts = s.split(' ');
        if (parts[0] && parts[0].length === 10 && !parts[1]) return s + ' 00:00:00';
        if (parts[1] && parts[1].length >= 5) return parts[0] + ' ' + parts[1].slice(0, 8);
        return s;
    }
    function formatSize(bytes) {
        if (bytes < 1024) return bytes + ' B';
        if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
        return (bytes / 1024 / 1024).toFixed(1) + ' MB';
    }
    function csrfToken() {
        var m = document.querySelector('meta[name="csrf-token"]');
        return m ? m.content : '';
    }
    function infoRow(k, v) {
        return '<div class="row" style="border-top:.5px solid var(--ios-sep);">' +
            '<div class="row-label" style="color:var(--ios-gray);font-weight:500;">' + escHtml(k) + '</div>' +
            '<div style="font-size:15px;font-weight:600;max-width:60%;text-align:right;">' + escHtml(v) + '</div></div>';
    }

    /* ───────────────── 统一弹窗组件（替换原生 alert/confirm） ───────────────── */
    var overlayRoot = null;
    function getOverlayRoot() {
        if (!overlayRoot) {
            overlayRoot = document.createElement('div');
            overlayRoot.id = 'appOverlayRoot';
            var screen = document.querySelector('.screen') || document.body;
            screen.appendChild(overlayRoot);
        }
        return overlayRoot;
    }

    // 轻提示：type ∈ info|success|error|warning，自动消失，可叠放
    function showToast(message, type, duration) {
        type = type || 'info';
        duration = duration || 2600;
        var root = getOverlayRoot();
        var t = document.createElement('div');
        t.className = 'toast toast-' + type;
        var ic = { info: 'ℹ️', success: '✅', error: '⚠️', warning: '⚠️' }[type] || 'ℹ️';
        var icEl = document.createElement('span'); icEl.className = 'toast-ic'; icEl.textContent = ic;
        var msg = document.createElement('span'); msg.className = 'toast-msg'; msg.textContent = message;
        t.appendChild(icEl); t.appendChild(msg);
        root.appendChild(t);
        requestAnimationFrame(function () {
            requestAnimationFrame(function () { t.classList.add('show'); });
        });
        setTimeout(function () {
            t.classList.remove('show');
            setTimeout(function () { if (t.parentNode) t.parentNode.removeChild(t); }, 360);
        }, duration);
        return t;
    }

    // iOS 风格确认弹窗：messageOrOpts 可为字符串或 {title,message,confirmText,cancelText,danger,onConfirm,onCancel}
    function showConfirm(messageOrOpts, onConfirm, opts) {
        var o;
        if (messageOrOpts && typeof messageOrOpts === 'object') {
            o = messageOrOpts;
        } else {
            o = Object.assign({ message: messageOrOpts || '' }, opts || {});
            o.onConfirm = onConfirm;
        }
        var title = o.title || '提示';
        var message = o.message || '';
        var confirmText = o.confirmText || '确定';
        var cancelText = o.cancelText || '取消';
        var danger = !!o.danger;

        var root = getOverlayRoot();
        var overlay = document.createElement('div');
        overlay.className = 'confirm-overlay';
        var box = document.createElement('div');
        box.className = 'confirm-box' + (danger ? ' danger' : '');
        var h = document.createElement('div'); h.className = 'confirm-title'; h.textContent = title;
        box.appendChild(h);
        if (message) {
            var p = document.createElement('div'); p.className = 'confirm-msg'; p.textContent = message;
            box.appendChild(p);
        }
        var actions = document.createElement('div'); actions.className = 'confirm-actions';
        var cancelBtn = document.createElement('button'); cancelBtn.className = 'confirm-btn cancel'; cancelBtn.textContent = cancelText;
        var okBtn = document.createElement('button'); okBtn.className = 'confirm-btn ok' + (danger ? ' danger' : ''); okBtn.textContent = confirmText;
        actions.appendChild(cancelBtn); actions.appendChild(okBtn);
        box.appendChild(actions); overlay.appendChild(box); root.appendChild(overlay);

        function close() {
            overlay.classList.remove('show');
            setTimeout(function () { if (overlay.parentNode) overlay.parentNode.removeChild(overlay); }, 240);
        }
        function onOk() { close(); if (o.onConfirm) o.onConfirm(); }
        function onCancel() { close(); if (o.onCancel) o.onCancel(); }

        requestAnimationFrame(function () {
            requestAnimationFrame(function () { overlay.classList.add('show'); });
        });
        cancelBtn.addEventListener('click', onCancel);
        okBtn.addEventListener('click', onOk);
        overlay.addEventListener('click', function (e) { if (e.target === overlay) onCancel(); });
        return { close: close };
    }
    // iOS 风格提示框（仅「确定」按钮，对应 UIAlertController 的 alert 样式）
    function showAlert(messageOrOpts) {
        var title, message, confirmText;
        if (messageOrOpts && typeof messageOrOpts === 'object') {
            title = messageOrOpts.title || '提示';
            message = messageOrOpts.message || '';
            confirmText = messageOrOpts.confirmText || '确定';
        } else {
            title = '提示';
            message = messageOrOpts || '';
            confirmText = '确定';
        }
        var root = getOverlayRoot();
        var overlay = document.createElement('div');
        overlay.className = 'confirm-overlay';
        var box = document.createElement('div');
        box.className = 'confirm-box';
        var h = document.createElement('div'); h.className = 'confirm-title'; h.textContent = title;
        box.appendChild(h);
        if (message) {
            var p = document.createElement('div'); p.className = 'confirm-msg'; p.textContent = message;
            box.appendChild(p);
        }
        var actions = document.createElement('div'); actions.className = 'confirm-actions';
        var okBtn = document.createElement('button'); okBtn.className = 'confirm-btn ok'; okBtn.textContent = confirmText;
        actions.appendChild(okBtn); box.appendChild(actions); overlay.appendChild(box); root.appendChild(overlay);
        function close() {
            overlay.classList.remove('show');
            setTimeout(function () { if (overlay.parentNode) overlay.parentNode.removeChild(overlay); }, 240);
        }
        requestAnimationFrame(function () {
            requestAnimationFrame(function () { overlay.classList.add('show'); });
        });
        okBtn.addEventListener('click', close);
        return { close: close };
    }
    window.showToast = showToast;
    window.showConfirm = showConfirm;
    window.showAlert = showAlert;

    /* ───────────────── 克制动效工具 ───────────────── */
    // 触发一次性入场动画（重排以重启动画）
    function animateIn(el, cls) {
        if (!el) return;
        cls = cls || 'anim-fade-up';
        el.classList.remove(cls);
        void el.offsetWidth;
        el.classList.add(cls);
    }
    // 为容器内子元素按序设置错峰入场（--i 递增延迟）
    function staggerChildren(container, sel) {
        if (!container) return;
        var els = container.querySelectorAll(sel);
        els.forEach(function (el, i) { el.style.setProperty('--i', i); el.classList.add('stagger-item'); });
    }

    /* ───────────────── 登录 / 退出 ───────────────── */
    var loginRoleSel = document.getElementById('loginRole');
    if (loginRoleSel) {
        loginRoleSel.addEventListener('change', function () {
            var cfg = ROLES[loginRoleSel.value];
            if (cfg) document.getElementById('loginAccount').value = cfg.account;
            document.getElementById('loginPassword').value = '123456';
        });
    }
    var loginForm = document.getElementById('loginForm');
    if (loginForm) {
        loginForm.addEventListener('submit', function (e) {
            e.preventDefault();
            var acc = document.getElementById('loginAccount').value.trim();
            var pwd = document.getElementById('loginPassword').value;
            var errEl = document.getElementById('loginError');
            if (!acc || !pwd) { errEl.textContent = '请输入工号和密码'; return; }
            errEl.textContent = '登录中…';
            fetch('/api/auth/login', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ account: acc, password: pwd })
            }).then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
                .then(function (res) {
                    if (!res.ok || !res.d.ok) {
                        errEl.textContent = (res.d && res.d.error) || '登录失败';
                    } else {
                        // 登录成功：刷新页面，由服务端渲染已登录态
                        location.reload();
                    }
                })
                .catch(function () { errEl.textContent = '网络错误，请重试'; });
        });
    }

    window.logout = function () {
        showConfirm({
            title: '退出登录',
            message: '确定退出当前账号吗？',
            confirmText: '退出',
            cancelText: '取消',
            danger: true,
            onConfirm: function () {
                fetch('/api/auth/logout', {
                    method: 'POST',
                    headers: { 'X-CSRF-Token': csrfToken() }
                }).then(function () { location.reload(); })
                    .catch(function () { location.reload(); });
            }
        });
    };

    /* ───────────────── Tab Bar 动态渲染 / Tab 切换 ───────────────── */
    function renderTabBar(role) {
        var bar = document.getElementById('tabBar');
        if (!bar) return;
        var tabs = ROLE_TABS[role] || ROLE_TABS.employee;
        var html = '';
        tabs.forEach(function (t, i) {
            var m = TAB_META[t];
            var active = (role === 'admin' ? t === 'guide' : i === 0);
            html += '<button class="tab-item' + (active ? ' active' : '') + '" data-tab="' + t + '">' +
                '<span class="ic">' + m.icon + '</span>' +
                '<span class="tab-label">' + m.label + '</span></button>';
        });
        bar.innerHTML = html;
    }

    window.switchTab = function (tab) {
        document.querySelectorAll('.tab-item').forEach(function (b) {
            b.classList.toggle('active', b.getAttribute('data-tab') === tab);
        });
        ALL_TABS.forEach(function (t) {
            var el = document.getElementById('tab-' + t);
            if (el) el.style.display = t === tab ? (t === 'guide' ? 'flex' : 'block') : 'none';
        });
        // Tab 切换：入场动效（面板淡入上滑）
        var panel = document.getElementById('tab-' + tab);
        if (panel) { panel.classList.remove('panel-in'); void panel.offsetWidth; panel.classList.add('panel-in'); }
        var m = TAB_META[tab];
        var cfg = ROLES[currentRole] || ROLES.employee;
        document.getElementById('navTitle').textContent = m ? (cfg.icon + ' ' + m.label) : '登录';
        document.querySelector('.content').scrollTop = 0;
        if (tab === 'my') loadMyList();
        if (tab === 'approve') loadApproveList();
        if (tab === 'fin-review' || tab === 'fin-pay') loadFinanceLists();
        if (tab === 'admin') loadAdminConfig();
        if (tab === 'audit') loadAuditLog();
        if (tab === 'usage') loadUsage();
        if (tab === 'guide') ensureGuideChat();
    };

    /* ───────────────── 票据类型下拉 ───────────────── */
    var ticketSel = document.getElementById('ticketType');
    if (ticketSel) {
        ticketSel.addEventListener('change', function () {
            currentTicketType = ticketSel.value;
            var isIt = currentTicketType === '行程单';
            document.getElementById('uploadIcon').textContent = isIt ? '🚕' : '🧾';
            document.getElementById('uploadHint').textContent = isIt
                ? '支持 PDF / JPG / PNG，行程单类票据（如滴滴行程单）'
                : '支持 PDF / JPG / PNG，发票类票据';
            resetUploadForm();
            document.getElementById('resultCard').style.display = 'none';
            setSubmitMode('check');
        });
    }

    /* ───────────────── 文件选择 ───────────────── */
    var fileInput = document.getElementById('fileInput');
    var uploadZone = document.getElementById('uploadZone');
    if (uploadZone) {
        uploadZone.addEventListener('click', function () {
            if (document.getElementById('uploadPreview').style.display === 'block') return;
            fileInput.click();
        });
    }
    if (fileInput) {
        fileInput.addEventListener('change', function () {
            var f = fileInput.files && fileInput.files[0];
            if (!f) return;
            var ext = '.' + f.name.split('.').pop().toLowerCase();
            if (['.pdf', '.jpg', '.jpeg', '.png'].indexOf(ext) === -1) {
                showToast('仅支持 PDF / JPG / PNG 格式', 'error'); fileInput.value = ''; return;
            }
            if (f.size > 10 * 1024 * 1024) {
                showToast('文件超过 10MB 限制', 'error'); fileInput.value = ''; return;
            }
            selectedFile = f;
            document.getElementById('uploadPlaceholder').style.display = 'none';
            document.getElementById('uploadPreview').style.display = 'block';
            document.getElementById('fileIcon').textContent = ext === '.pdf' ? '📄' : '🖼️';
            document.getElementById('fileName').textContent = f.name;
            document.getElementById('fileSize').textContent = formatSize(f.size);
            var o = document.getElementById('ocrStatus');
            o.className = 'ocr-status show';
            o.innerHTML = '📄 票据已选择，点击「提交校验」后由智能体执行 OCR 提取并回写字段';
        });
    }

    /* ───────────────── 重置上传表单 ───────────────── */
    function resetUploadForm() {
        if (fileInput) fileInput.value = '';
        selectedFile = null;
        document.getElementById('uploadPlaceholder').style.display = 'block';
        document.getElementById('uploadPreview').style.display = 'none';
        var o = document.getElementById('ocrStatus'); o.className = 'ocr-status'; o.innerHTML = '';
        ['apply_amount', 'apply_date', 'invoice_number', 'invoice_date'].forEach(function (id) { document.getElementById(id).value = ''; });
        document.getElementById('expense_category').value = '';
        document.getElementById('reason').value = '';
        document.getElementById('autoFields').style.display = 'none';
        var note = document.getElementById('autoFieldsNote');
        if (note) note.style.display = 'none';
        // 复位 AI/人工徽标（停用态切换回启用态时，恢复"申请金额/日期/费用类型"的 AI 徽标）
        restoreAiBadges();
        // 收起「重新上传」入口（下次拦截时由 finalize 重新显示）
        var rb = document.getElementById('reuploadBtn');
        if (rb) rb.style.display = 'none';
    }
    function resetResultCard() {
        var rc = document.getElementById('resultCard');
        rc.className = 'result-card'; rc.style.display = 'none'; rc.innerHTML = '';
    }
    window.resetUploadForm = resetUploadForm;

    /* ───────────────── 提交按钮模式 ───────────────── */
    function setSubmitMode(mode) {
        var btn = document.getElementById('submitBtn');
        if (mode === 'approve') { btn.textContent = '✅ 提交审批'; btn.classList.add('approve'); }
        else { btn.textContent = '提交审核'; btn.classList.remove('approve'); }
    }
    window.setSubmitMode = setSubmitMode;

    window.onSubmitClick = function () {
        var btn = document.getElementById('submitBtn');
        if (btn.classList.contains('approve')) { submitApprove(); }
        else {
            // 提交校验前：票据类型 / 票据文件为必填，缺失则弹出 iOS 原生提示框
            if (!currentTicketType) {
                showAlert({ title: '请选择票据类型', message: '请先选择票据类型（发票 / 行程单），再提交审核。' });
                return;
            }
            if (document.getElementById('uploadPreview').style.display !== 'block') {
                showAlert({ title: '请上传票据文件', message: '请先上传票据文件（PDF / JPG / PNG），再提交审核。' });
                return;
            }
            // 对齐 prototype_ios.html：点击「提交审核」后弹出 iOS 确认框，确认后再执行 OCR 校验
            showConfirm({
                title: '确认提交审核？',
                message: '提交后由智能体执行 OCR 校验并回写报销字段。',
                confirmText: '提交审核',
                cancelText: '取消',
                onConfirm: function () { runCheck(); }
            });
        }
    };

    /* ───────────────── 智能体流水线 ───────────────── */
    var pipeTimer = null, pipeIdx = 0, pipeStepsData = [], pipeResolved = false, pipelineStarted = false;

    function setupPipeSheet(isIt, steps) {
        pipeStepsData = steps; pipeIdx = 0; pipeResolved = false;
        // 对齐 prototype_ios.html：标题直接使用「emoji + 智能体名」，无「执行流水线」后缀
        document.getElementById('pipeTitle').textContent = isIt ? '🚕 行程单智能体' : '🧾 发票智能体';
        // 原型无 pipeBadge（标题已含 emoji + 智能体名），隐藏避免重复显示
        var badge = document.getElementById('pipeBadge');
        badge.style.display = 'none';
        document.getElementById('pipeSteps').style.display = '';
        var pr = document.getElementById('pipeResult'); pr.style.display = 'none'; pr.className = 'result-card'; pr.style.marginTop = '14px';
        var html = '';
        steps.forEach(function (s, i) {
            html += '<div class="pipe-step" data-i="' + i + '">' +
                '<span class="ps-icon">' + s.icon + '</span>' +
                '<span class="ps-name">' + s.name + '</span>' +
                '<span class="ps-status">等待中</span></div>';
        });
        document.getElementById('pipeSteps').innerHTML = html;
        document.getElementById('pipelineSheet').classList.add('show');
    }
    function startPipelineAnim() {
        advance();
    }
    function advance() {
        if (pipeResolved) return;
        if (pipeIdx >= pipeStepsData.length) return;
        var elems = document.querySelectorAll('.pipe-step');
        var el = elems[pipeIdx];
        if (el) {
            el.classList.add('active');
            el.querySelector('.ps-status').innerHTML = '<span class="ps-spin">⏳</span> 执行中…';
        }
        pipeTimer = setTimeout(function () {
            if (pipeResolved) return;
            if (el) { el.classList.remove('active'); el.classList.add('done'); el.querySelector('.ps-status').innerHTML = '✓ 完成'; }
            pipeIdx++;
            advance();
        }, 900);
    }
    function finishPipeAnim() {
        pipeResolved = true;
        if (pipeTimer) { clearTimeout(pipeTimer); pipeTimer = null; }
        document.querySelectorAll('.pipe-step').forEach(function (el) {
            if (!el.classList.contains('done')) {
                el.classList.remove('active'); el.classList.add('done');
                el.querySelector('.ps-status').innerHTML = '✓ 完成';
            }
        });
    }

    function runCheck() {
        if (!currentTicketType) { showAlert({ title: '请选择票据类型', message: '请先选择票据类型（发票 / 行程单），再提交审核。' }); return; }
        if (!selectedFile) { showAlert({ title: '请上传票据文件', message: '请先上传票据文件（PDF / JPG / PNG），再提交审核。' }); return; }
        var btn = document.getElementById('submitBtn');
        btn.disabled = true;
        isDisabledMode = false;
        pipelineStarted = false;
        var isIt = currentTicketType === '行程单';
        var steps = isIt ? ITINERARY_STEPS : INVOICE_STEPS;

        var statusPromise = fetch('/api/deepseek/status')
            .then(function (r) { return r.json(); })
            .catch(function () { return { enabled: true }; });

        var fd = new FormData();
        fd.append('file', selectedFile);
        fd.append('ticket_type', currentTicketType);
        var uploadPromise = fetch('/upload', {
            method: 'POST', body: fd,
            headers: { 'X-CSRF-Token': csrfToken() }
        }).then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
            .catch(function () { return { ok: false, d: { error: '网络错误，请重试' } }; });

        var resolved = null;
        // 与 Web 端一致：停用 → 不显示流水线，仅提示信息；启用 → 流水线动画 + 结果
        function present(res) {
            btn.disabled = false;
            if (dsDisabled) {
                showDisabledSheet(res);
                return;
            }
            finishPipeAnim();
            if (!res || !res.ok) {
                var msg = (res && res.d && (res.d.summary || res.d.error)) || '请求失败，请重试';
                renderErrorResult(msg);
                lastCheckPassed = false;
                return;
            }
            finalize(res.d);
        }
        statusPromise.then(function (cfg) {
            dsDisabled = !cfg.enabled;
            if (!dsDisabled) {
                // 启用态：弹出流水线 Sheet 并启动动画
                setupPipeSheet(isIt, steps);
                pipelineStarted = true;
                startPipelineAnim();
            }
            if (resolved) present(resolved);
        });
        uploadPromise.then(function (res) {
            resolved = res;
            if (dsDisabled || pipelineStarted) present(res);
        });
    }

    /* ── DeepSeek 停用：对齐 prototype_ios.html，改用 iOS 原生提示框（iosAlert），不再弹流水线替代 Sheet ── */
    function showDisabledSheet(res) {
        isDisabledMode = true;
        var data = (res && res.d) || {};
        lastRequestId = data._request_id || (data._form && data._form['报销单号']) || '';
        lastCheckPassed = false;

        // 优先采用后端返回的统一停用说明（来源：config.DEEPSEEK_DISABLED_MSG）
        var summary = DISABLED_MSG_FALLBACK;
        if (data.summary) {
            summary = String(data.summary).replace(/^OCR 提取失败:\s*/, '');
        }
        lastDisabledSummary = summary;

        // 对齐 prototype：弹 iOS 原生提示框（iosAlert 风格），文案与 prototype 一致
        showAlert({
            title: '⚠️ AI 校验已停用',
            message: 'DeepSeek 大模型已停用（系统配置）。\n' + (summary || '请联系系统管理员启用，或人工填写报销单后直接提交审批。')
        });

        // 显示人工填写区、预填系统日期，并切换为「提交审批」模式（与原型 dsOn()=false 分支一致）
        var af = document.getElementById('autoFields');
        if (af) af.style.display = 'block';
        var dEl = document.getElementById('apply_date');
        if (dEl) dEl.value = new Date().toISOString().slice(0, 10);
        setSubmitMode('approve');
    }

    function finalize(data) {
        lastRequestId = data._request_id || (data._form && data._form['报销单号']) || '';
        var status = data.status || '错误';
        renderResult(data);
        if (status === '通过' || status === '预警') {
            autoFillFromOcr(data);
            lastCheckPassed = true;
            setSubmitMode('approve');
        } else {
            lastCheckPassed = false;
            setSubmitMode('check');
            // 拦截/错误：开放「重新上传」入口，避免员工因字段缺失被卡死
            var rb = document.getElementById('reuploadBtn');
            if (rb) rb.style.display = 'inline-block';
        }
    }

    // 从异常检测结果中解析「字段缺失」类异常，提取缺失的字段名（如「发票号码」）
    function parseMissingFields(anomaly) {
        var missing = [];
        var list = (anomaly && anomaly['异常明细']) || [];
        list.forEach(function (a) {
            if (a && a['异常类型'] === '字段缺失') {
                var m = /[「『](.+?)[」』]/.exec(a['异常描述'] || '');
                if (m && missing.indexOf(m[1]) === -1) missing.push(m[1]);
            }
        });
        return missing;
    }
    function renderResult(data) {
        var meta = {
            '通过': { icon: '✅', label: '校验通过', cls: 'pass' },
            '预警': { icon: '⚠️', label: '校验预警', cls: 'warning' },
            '拦截': { icon: '⛔', label: '校验拦截', cls: 'block' },
            '错误': { icon: '❌', label: '系统错误', cls: 'block' }
        }[data.status] || { icon: '✅', label: '校验通过', cls: 'pass' };
        var rc = document.getElementById('pipeResult');
        rc.className = 'result-card ' + meta.cls; rc.style.display = 'flex';
        // 拦截态：在后端 summary 基础上补充「缺失字段」与可操作的下一步引导（修复员工卡住问题）
        var summary = data.summary || '';
        if (data.status === '拦截') {
            var missing = parseMissingFields(data.anomaly_result);
            if (missing.length) {
                summary = (summary ? summary + '\n' : '') +
                    '缺失字段：' + missing.join('、') +
                    '\n请重新上传清晰的票据原件，或更换后再次校验。';
            } else if (!summary) {
                summary = '校验未通过，请检查票据后重新提交校验。';
            }
        }
        rc.innerHTML = '<div class="rc-icon">' + meta.icon + '</div><div class="rc-body"><div class="rc-label">' + meta.label + '</div><div class="rc-summary">' + escHtml(summary) + '</div></div>';
        var ic = document.getElementById('resultCard');
        ic.className = 'result-card ' + meta.cls; ic.style.display = 'flex';
        ic.innerHTML = rc.innerHTML;
        animateIn(ic, 'anim-fade-up');
    }
    function renderErrorResult(msg) {
        var rc = document.getElementById('pipeResult');
        rc.className = 'result-card block'; rc.style.display = 'flex';
        rc.innerHTML = '<div class="rc-icon">❌</div><div class="rc-body"><div class="rc-label">校验失败</div><div class="rc-summary">' + escHtml(msg) + '</div></div>';
        var ic = document.getElementById('resultCard');
        ic.className = 'result-card block'; ic.style.display = 'flex'; ic.innerHTML = rc.innerHTML;
        animateIn(ic, 'anim-fade-up');
    }

    /* ───────────────── AI 回写字段（启用态） ───────────────── */
    function markAuto(id) {
        var el = document.getElementById(id);
        if (el) el.classList.add('auto-filled');
    }
    function autoFillFromOcr(data) {
        var isIt = currentTicketType === '行程单';
        var ocr = data.ocr_result || {};

        // 1) 发票号码（AI 内部回写，不向员工展示）
        if (!isIt && ocr['发票号码']) {
            document.getElementById('invoice_number').value = ocr['发票号码'];
            markAuto('invoice_number');
        }
        // 2) 开票日期（AI 内部回写，不向员工展示）
        if (!isIt && ocr['开票日期']) {
            document.getElementById('invoice_date').value = String(ocr['开票日期']).slice(0, 10);
            markAuto('invoice_date');
        }
        // 3) 申请金额（启用态 AI 回写）
        var amount = isIt ? ocr['总金额_元'] : (ocr['发票金额'] != null ? ocr['发票金额'] : ocr['价税合计_小写']);
        if (amount != null && amount !== '') { document.getElementById('apply_amount').value = amount; markAuto('apply_amount'); }
        // 4) 申请日期：统一回填为系统日期（本地时区），并标记「📅 系统日期」
        var t = new Date();
        var sysDate = t.getFullYear() + '-' + String(t.getMonth() + 1).padStart(2, '0') + '-' + String(t.getDate()).padStart(2, '0');
        document.getElementById('apply_date').value = sysDate; markAuto('apply_date');
        setFieldBadge('apply_date', '📅 系统日期', 'field-badge ai');
        // 5) 费用类型（启用态 AI 回写）
        var cat = isIt ? '交通' : ((data.classify_result && data.classify_result['费用分类']) || '住宿');
        document.getElementById('expense_category').value = cat; markAuto('expense_category');
        // 6) 报销事由：保留默认 placeholder / 人工填写，标记为人工
        setFieldBadge('reason', '✍️ 人工', 'field-badge manual');
        var af = document.getElementById('autoFields');
        af.style.display = 'block';
        // 字段错峰入场
        staggerChildren(af, '.row');
    }

    /* ── 通用徽标设置：找到字段对应 .row-label 下的 span，替换为指定样式与文案 ── */
    function setFieldBadge(fieldId, text, className) {
        var input = document.getElementById(fieldId);
        if (!input) return;
        var label = input.parentNode.querySelector('.row-label');
        if (!label) return;
        // 移除已有徽标（可能是 ai-dot / field-badge / field-badge.manual 等）
        var old = label.querySelector('.ai-dot, .field-badge');
        if (old) old.remove();
        var span = document.createElement('span');
        span.className = className;
        span.textContent = text;
        label.appendChild(span);
    }

    /* ───────────────── 停用态：人工填写（关闭提示 Sheet 后进入，与 Web 端一致）───────────────── */
    function enableManualMode() {
        setSubmitMode('approve');
        document.getElementById('autoFields').style.display = 'block';
        // 停用态：申请金额 / 申请日期 / 费用类型 → 人工徽标
        setFieldBadge('apply_amount', '✍️ 人工', 'field-badge manual');
        setFieldBadge('apply_date', '✍️ 人工', 'field-badge manual');
        setFieldBadge('expense_category', '✍️ 人工', 'field-badge manual');
        var note = document.getElementById('autoFieldsNote');
        if (note) {
            note.style.display = 'block';
            note.textContent = lastDisabledSummary || DISABLED_MSG_FALLBACK;
        }
    }

    /* ───────────────── 切换回 AI 回写态时复原所有徽标 ───────────────── */
    function restoreAiBadges() {
        setFieldBadge('apply_amount', 'AI', 'ai-dot');
        setFieldBadge('apply_date', 'AI', 'ai-dot');
        setFieldBadge('expense_category', 'AI', 'ai-dot');
        setFieldBadge('reason', '✍️ 人工', 'field-badge manual');
    }

    /* ───────────────── 关闭流水线 Sheet ───────────────── */
    window.closePipelineSheet = function () {
        document.getElementById('pipelineSheet').classList.remove('show');
        // 停用态：关闭提示信息后展开人工填写字段并切换为「提交审批」（与 Web 端 closePipelineModal 一致）
        if (isDisabledMode) { enableManualMode(); }
    };

    /* ───────────────── 提交审批 → 我的报销 ───────────────── */
    function submitApprove() {
        if (!lastRequestId) { showToast('未找到报销单号，请重新提交校验', 'error'); return; }
        var amount = document.getElementById('apply_amount').value.trim();
        var category = document.getElementById('expense_category').value.trim();
        var date = document.getElementById('apply_date').value.trim();
        var reason = document.getElementById('reason').value.trim();
        var invNo = document.getElementById('invoice_number').value.trim();
        var invDate = document.getElementById('invoice_date').value.trim();
        if (!amount || !category) {
            showToast('请先填写「申请金额」与「费用类型」后再提交审批', 'warning');
            return;
        }
        if (!date) {
            showToast('请先填写「申请日期」后再提交审批', 'warning');
            return;
        }
        if (!reason) {
            showToast('请先填写「报销事由」后再提交审批', 'warning');
            return;
        }
        var payload = {
            apply_amount: amount || null,
            apply_date: date || null,
            expense_category: category || null,
            reason: reason || null
        };
        if (invNo) payload.invoice_number = invNo;
        if (invDate) payload.invoice_date = invDate;
        // 与主管审批一致的 iOS 统一确认框
        showConfirm({
            title: '提交审批',
            message: '确认提交报销单 ' + lastRequestId + ' 并送审？',
            confirmText: '提交审批',
            onConfirm: function () {
                fetch('/api/reimbursement/' + encodeURIComponent(lastRequestId) + '/update', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken() },
                    body: JSON.stringify(payload)
                }).then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
                    .then(function (res) {
                        if (!res.ok || res.d.error) {
                            showSuccess(null, (res.d && res.d.error) || '提交失败');
                            return;
                        }
                        showSuccess({
                            requestId: lastRequestId, amount: amount, category: category,
                            reason: reason, invoiceNumber: invNo, invoiceDate: invDate
                        });
                        loadMyList();
                    })
                    .catch(function () { showSuccess(null, '请求失败，请重试'); });
            }
        });
    }

    function showSuccess(item, errorMsg) {
        var body = document.getElementById('successBody');
        if (errorMsg) {
            body.innerHTML = '<div class="result-card block" style="display:flex;">' +
                '<div class="rc-icon">❌</div><div class="rc-body"><div class="rc-label">提交失败</div>' +
                '<div class="rc-summary">' + escHtml(errorMsg) + '</div></div></div>';
        } else {
            var html = infoRow('报销单号', item.requestId)
                + infoRow('申请金额', money(item.amount))
                + infoRow('费用类型', item.category)
                + infoRow('报销事由', item.reason || '—')
                + (item.invoiceNumber ? infoRow('发票号码', item.invoiceNumber) : '')
                + (item.invoiceDate ? infoRow('开票日期', item.invoiceDate) : '')
                + infoRow('当前状态', '待审批');
            body.innerHTML = '<div class="detail-list">' + html + '</div>' +
                '<p class="footnote" style="margin-top:16px;">已路由至主管，可在「我的报销」查看审批进度。</p>';
        }
        document.getElementById('successSheet').classList.add('show');
    }
    window.closeSuccess = function () {
        document.getElementById('successSheet').classList.remove('show');
        switchTab('reimburse'); resetUploadForm(); resetResultCard();
        document.getElementById('resultCard').style.display = 'none';
        setSubmitMode('check');
        isDisabledMode = false;
        lastDisabledSummary = '';
        lastCheckPassed = false;
        lastRequestId = '';
    };

    /* ───────────────── 我的报销列表 ───────────────── */
    function loadMyList() {
        var el = document.getElementById('myList');
        if (!el) return;
        fetch('/api/my').then(function (r) { return r.json(); }).then(function (data) {
            if (data.error) { el.innerHTML = ''; return; }
            var items = data.items || [];
            document.getElementById('myCount').textContent = items.length;
            if (!items.length) {
                el.innerHTML = '<div class="empty"><div class="ei">📭</div><div class="et">暂无报销记录<br>请在「报销申请」中提交</div></div>';
                return;
            }
            el.innerHTML = items.map(function (it, i) {
                var wsMap = {
                    '待审批': '⏳ 待审批', '审批中': '🔄 审批中', '待复核': '✓ 待复核',
                    '已驳回': '✕ 已驳回', '已复核': '📦 已复核', '已打款': '💰 已打款',
                    '已存档备案': '📦 已存档备案', '已转审': '↪️ 已转审'
                };
                var wsClsMap = {
                    '待审批': 'pending', '审批中': 'pending', '待复核': 'approved',
                    '已驳回': 'status-rejected', '已复核': 'approved', '已打款': 'status-paid',
                    '已存档备案': 'status-archived', '已转审': 'status-transferred'
                };
                var ws = wsMap[it.workflow_status] || it.workflow_status;
                var wsCls = wsClsMap[it.workflow_status] || 'pending';
                var typeTag = it.ticket_type === '行程单'
                    ? '<span class="tag itinerary">🚕 行程单</span>'
                    : '<span class="tag invoice">🧾 发票</span>';
                var stTag = '<span class="tag ' + wsCls + '">' + ws + '</span>';
                return '<div class="reimb-item stagger-item" style="--i:' + i + '" data-action="open-detail" data-id="' + escHtml(it.request_id) + '">' +
                    '<div class="ri-head"><div><div class="ri-id">' + escHtml(it.request_id) + '</div>' +
                    '<div class="ri-reason">' + escHtml(it.reason || '—') + '</div></div>' +
                    '<div class="ri-amount">' + money(it.apply_amount) + '</div></div>' +
                    '<div class="ri-meta"><span><span class="mk">提交</span>' + (it.created_at ? escHtml(fmtTime(it.created_at)) : '—') + '</span>' +
                    '<span><span class="mk">类型</span>' + escHtml(it.expense_category || '—') + '</span></div>' +
                    '<div class="ri-foot">' + typeTag + stTag + '</div></div>';
            }).join('');
        }).catch(function () {
            el.innerHTML = '<div class="empty"><div class="et">加载失败，请重试</div></div>';
        });
    }
    window.loadMyList = loadMyList;

    /* ───────────────── 详情 Sheet ───────────────── */
    /* 共享：审批记录渲染（详情页 / 存档备案页一致）*/
    var APPROVAL_ACTION_LABELS = {
        '通过': '已通过',
        '驳回': '已驳回',
        '转审': '已转审',
        '归档': '已审核',
        '打款': '已打款',
        '备案': '已备案',
        '回单归档': '回单归档'
    };
    function renderApprovalRecords(records) {
        if (!records || !records.length) {
            return '<div class="row" style="border-top:none;color:var(--ios-gray);">暂无审批记录</div>';
        }
        return records.map(function (rec) {
            var action = APPROVAL_ACTION_LABELS[rec.action] || rec.action || '—';
            return '<div class="row col" style="align-items:stretch;">' +
                '<div class="row" style="border-top:none;padding:0 0 6px;">' +
                '<div class="row-label" style="font-weight:600;">' + escHtml(rec.approver_name || rec.approver_id || '—') + '</div>' +
                '<div class="row-label" style="color:var(--ios-blue);">' + escHtml(action) + '</div></div>' +
                '<div style="font-size:12px;color:var(--ios-gray);">' + escHtml(fmtTime(rec.action_time)) +
                (rec.comment ? ' · ' + escHtml(rec.comment) : '') + '</div></div>';
        }).join('');
    }

    window.openDetail = function (id) {
        var body = document.getElementById('detailBody');
        body.innerHTML = '<div class="empty"><div class="et">加载中…</div></div>';
        document.getElementById('detailTitle').textContent = '报销单详情';
        document.getElementById('detailSheet').classList.add('show');
        fetch('/api/reimbursement/' + encodeURIComponent(id))
            .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
            .then(function (res) {
                if (!res.ok || res.d.error) {
                    body.innerHTML = '<div class="empty"><div class="et">' + escHtml(res.d.error || '加载失败') + '</div></div>';
                    return;
                }
                body.innerHTML = renderDetail(res.d);
            })
            .catch(function () { body.innerHTML = '<div class="empty"><div class="et">请求失败，请重试</div></div>'; });
    };
    window.closeDetail = function () {
        document.getElementById('detailSheet').classList.remove('show');
    };

    function renderDetail(d) {
        var html = '';

        // 状态映射（与列表页一致）
        var wsLabels = {
            '待审批': '⏳ 待审批', '审批中': '🔄 审批中', '待复核': '✓ 待复核',
            '已驳回': '✕ 已驳回', '已复核': '📦 已复核', '已打款': '💰 已打款',
            '已存档备案': '📦 已存档备案', '已转审': '↪️ 已转审'
        };

        // ── 基本信息 ──
        var basic = [
            { k: '报销单号', v: d.request_id || '—' },
            { k: '提交人', v: d.employee_id || '—' },
            { k: '申请金额', v: money(d.apply_amount) },
            { k: '报销事由', v: d.reason || '—' },
            { k: '费用类型', v: d.expense_category || '—' },
            { k: '申请日期', v: fmtDate(d.apply_date) },
            { k: '当前状态', v: wsLabels[d.workflow_status] || d.workflow_status || '—' },
            { k: '提交时间', v: fmtTime(d.created_at) }
        ];
        if (d.archived_by) basic.push({ k: '复核人', v: d.archived_by });
        if (d.paid_by) basic.push({ k: '打款人', v: d.paid_by });
        if (d.filed_by) {
            basic.push({ k: '存档备案人', v: d.filed_by });
            var filedRec = (d.approval_records || []).filter(function (a) { return a.action === '备案'; }).pop();
            if (filedRec && filedRec.action_time) basic.push({ k: '备案时间', v: fmtTime(filedRec.action_time) });
        }
        html += '<div class="detail-list">' + basic.map(function (it) { return infoRow(it.k, it.v); }).join('') + '</div>';

        // ── 审批记录 ──
        var records = d.approval_records || [];
        html += '<div class="group" style="margin-top:16px;"><div class="group-title">审批记录</div>' +
            renderApprovalRecords(records) + '</div>';

        // ── 发票明细（第一行：发票影像）──
        html += '<div class="group" style="margin-top:4px;"><div class="group-title">发票原件</div>' +
            renderInvoiceGallery(d.invoices || [], d.request_id) + '</div>';
        return html;
    }

    /* 共享：发票原件缩略图画廊（详情页 / 存档备案页一致）*/
    function renderInvoiceGallery(invoices, requestId) {
        if (!invoices || !invoices.length) {
            return '<div class="row" style="border-top:none;color:var(--ios-gray);">暂无可展示的发票</div>';
        }
        var html = '<div class="inv-gallery">';
        invoices.forEach(function (inv, idx) {
            if (inv.has_image) {
                html += '<div class="inv-thumb inv-thumb-detail" data-action="open-invoice" data-rid="' + escHtml(requestId) + '" data-idx="' + idx + '">' +
                    '<img src="/api/reimbursement/' + escHtml(requestId) + '/invoice/' + idx + '/thumb" loading="lazy" onerror="this.parentElement.classList.add(\'inv-thumb-missing\')">' +
                    '<span class="inv-thumb-tap">点击查看</span></div>';
            } else {
                html += '<div class="inv-thumb inv-thumb-detail inv-thumb-placeholder">' +
                    '<span class="inv-thumb-icon">📄</span>' +
                    '<span class="inv-thumb-hint">暂无影像</span></div>';
            }
        });
        html += '</div>';
        return html;
    }

    /* ═════════════════ 主管：待审工作台 ═════════════════ */
    function aiTagOf(it) {
        if (it.ai_disabled) return '<span class="tag manual">✍️ 人工填写</span>';
        if (it.ai_status === '通过') return '<span class="tag ai-pass">✓ AI 通过</span>';
        if (it.ai_status === '预警') return '<span class="tag ai-warn">⚠️ AI 预警</span>';
        if (it.ai_status === '拦截') return '<span class="tag ai-block">⛔ AI 拦截</span>';
        return '';
    }
    function typeTagOf(it) {
        return it.ticket_type === '行程单'
            ? '<span class="tag itinerary">🚕 行程单</span>'
            : '<span class="tag invoice">🧾 发票</span>';
    }
    function submitterOf(it) {
        return (it.employee_name ? it.employee_name + '（' + it.employee_id + '）' : (it.employee_id || '—'));
    }

    function loadApproveList() {
        var el = document.getElementById('approveList');
        if (!el) return;
        fetch('/api/approve/list').then(function (r) { return r.json(); }).then(function (data) {
            if (data.error) { el.innerHTML = '<div class="empty"><div class="et">' + escHtml(data.error) + '</div></div>'; return; }
            var items = data.items || [];
            document.getElementById('approvePendingCount').textContent = items.length;
            document.getElementById('approveDoneCount').textContent = data.done_this_month || 0;
            if (!items.length) {
                el.innerHTML = '<div class="empty"><div class="ei">📭</div><div class="et">暂无待审报销单</div></div>';
                return;
            }
            el.innerHTML = items.map(function (it, i) {
                var stTag = it.workflow_status === '审批中'
                    ? '<span class="tag pending">🔄 审批中（会签）</span>'
                    : '<span class="tag pending">⏳ 待审</span>';
                return '<div class="reimb-item stagger-item" style="--i:' + i + ';cursor:default;">' +
                    '<div class="ri-head"><div><div class="ri-id">' + escHtml(it.request_id) + '</div>' +
                    '<div class="ri-reason">' + escHtml(it.reason || '—') + '</div></div>' +
                    '<div class="ri-amount">' + money(it.apply_amount) + '</div></div>' +
                    '<div class="ri-meta"><span><span class="mk">提交</span>' + escHtml(submitterOf(it)) + '</span>' +
                    '<span><span class="mk">类型</span>' + escHtml(it.expense_category || '—') + '</span></div>' +
                    (it.ai_disabled ? '' : '<div class="ai-note">🤖 <b>AI 校验：</b>' + escHtml(it.ai_summary || '—') + '</div>') +
                    '<div class="ri-foot">' + typeTagOf(it) + aiTagOf(it) + stTag + '</div>' +
                    '<div class="item-actions">' +
                    '<button class="btn-mini gray" data-action="open-detail" data-id="' + escHtml(it.request_id) + '">📄 明细</button>' +
                    '<button class="btn-mini orange" data-action="approve" data-act="转审" data-id="' + escHtml(it.request_id) + '">↪️ 转审</button>' +
                    '<button class="btn-mini red" data-action="approve" data-act="驳回" data-id="' + escHtml(it.request_id) + '">✕ 驳回</button>' +
                    '<button class="btn-mini green" data-action="approve" data-act="通过" data-id="' + escHtml(it.request_id) + '">✓ 通过</button>' +
                    '</div></div>';
            }).join('');
        }).catch(function () {
            el.innerHTML = '<div class="empty"><div class="et">加载失败，请重试</div></div>';
        });
    }

    window.handleApprove = function (id, action) {
        var confirmMsg = {
            '通过': '确认通过报销单 ' + id + '？',
            '驳回': '确认驳回报销单 ' + id + '？',
            '转审': '确认将报销单 ' + id + ' 转交上级审批？'
        }[action];
        if (!confirmMsg) return;
        showConfirm({
            title: '审批确认',
            message: confirmMsg,
            confirmText: action,
            danger: action === '驳回',
            onConfirm: function () {
                fetch('/api/approve', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken() },
                    body: JSON.stringify({ request_id: id, action: action })
                }).then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
                    .then(function (res) {
                        if (!res.ok || res.d.error) {
                            showAlert({ title: '操作失败', message: (res.d && res.d.error) || '操作失败' });
                        } else {
                            var st = (res.d.data && res.d.data.workflow_status) || '';
                            showAlert({
                                title: '审批成功',
                                message: '已' + action + '报销单 ' + id + (st ? '（当前状态：' + st + '）' : '')
                            });
                        }
                        loadApproveList();
                    })
                    .catch(function () {
                        showAlert({ title: '请求失败', message: '网络异常，请重试' });
                        loadApproveList();
                    });
            }
        });
    };

    /* ═════════════════ 财务 / 出纳（职责分离） ═════════════════ */
    var financeItems = {};   // request_id -> item（供存档备案 Sheet 使用）

    function loadFinanceLists() {
        fetch('/api/finance/list').then(function (r) { return r.json(); }).then(function (data) {
            if (data.error) return;
            var items = data.items || [];
            financeItems = {};
            items.forEach(function (it) { financeItems[it.request_id] = it; });
            renderFinReviewList(items.filter(function (it) { return it.workflow_status === '待复核'; }));
            renderFinPayList(items.filter(function (it) { return it.workflow_status === '已复核'; }));
            renderFinFileList(items.filter(function (it) { return it.workflow_status === '已打款'; }));
        }).catch(function () { /* 网络异常静默 */ });
    }

    function renderFinReviewList(list) {
        var el = document.getElementById('finReviewList');
        if (!el) return;
        var cnt = document.getElementById('finReviewPending');
        if (cnt) cnt.textContent = list.length;
        if (!list.length) {
            el.innerHTML = '<div class="empty"><div class="ei">📦</div><div class="et">暂无待复核的报销单</div></div>';
            return;
        }
        el.innerHTML = list.map(function (it, i) {
            return '<div class="reimb-item stagger-item" style="--i:' + i + ';cursor:default;">' +
                '<div class="ri-head"><div><div class="ri-id">' + escHtml(it.request_id) + '</div>' +
                '<div class="ri-reason">' + escHtml(it.reason || '—') + '</div></div>' +
                '<div class="ri-amount">' + money(it.apply_amount) + '</div></div>' +
                '<div class="ri-meta"><span><span class="mk">提交</span>' + escHtml(submitterOf(it)) + '</span>' +
                '<span><span class="mk">类型</span>' + escHtml(it.expense_category || '—') + '</span></div>' +
                '<div class="ri-foot">' + typeTagOf(it) + aiTagOf(it) + '<span class="tag pending">⏳ 待复核</span></div>' +
                '<div class="item-actions">' +
                '<button class="btn-mini gray" data-action="open-detail" data-id="' + escHtml(it.request_id) + '">📄 明细</button>' +
                '<button class="btn-mini green" data-action="finance" data-act="归档" data-id="' + escHtml(it.request_id) + '">📦 复核</button>' +
                '</div></div>';
        }).join('');
    }

    function renderFinPayList(list) {
        var el = document.getElementById('finPayList');
        if (!el) return;
        var cnt = document.getElementById('finPayPending');
        if (cnt) cnt.textContent = list.length;
        if (!list.length) {
            el.innerHTML = '<div class="empty"><div class="ei">💰</div><div class="et">暂无待打款的报销单<br>（请先由财务岗 FIN-001 受理）</div></div>';
            return;
        }
        el.innerHTML = list.map(function (it, i) {
            return '<div class="reimb-item stagger-item" style="--i:' + i + ';cursor:default;">' +
                '<div class="ri-head"><div><div class="ri-id">' + escHtml(it.request_id) + '</div>' +
                '<div class="ri-reason">' + escHtml(it.reason || '—') + '</div></div>' +
                '<div class="ri-amount">' + money(it.apply_amount) + '</div></div>' +
                '<div class="ri-meta"><span><span class="mk">提交</span>' + escHtml(submitterOf(it)) + '</span>' +
                '<span><span class="mk">受理</span>' + escHtml(it.archived_by || '—') + '</span></div>' +
                '<div class="ri-foot">' + typeTagOf(it) + '<span class="tag status-archived">📦 已复核</span></div>' +
                '<div class="item-actions">' +
                '<button class="btn-mini gray" data-action="open-detail" data-id="' + escHtml(it.request_id) + '">📄 明细</button>' +
                '<button class="btn-mini teal" data-action="finance" data-act="打款" data-id="' + escHtml(it.request_id) + '">💰 发起打款</button>' +
                '</div></div>';
        }).join('');
    }

    function renderFinFileList(list) {
        var el = document.getElementById('finFileList');
        if (!el) return;
        var cnt = document.getElementById('finFilePending');
        if (cnt) cnt.textContent = list.length;
        if (!list.length) {
            el.innerHTML = '<div class="empty"><div class="ei">📦</div><div class="et">暂无待存档备案的报销单<br>（打款后在此统一存档备案）</div></div>';
            return;
        }
        el.innerHTML = list.map(function (it, i) {
            return '<div class="reimb-item stagger-item" style="--i:' + i + ';cursor:default;">' +
                '<div class="ri-head"><div><div class="ri-id">' + escHtml(it.request_id) + '</div>' +
                '<div class="ri-reason">' + escHtml(it.reason || '—') + '</div></div>' +
                '<div class="ri-amount">' + money(it.apply_amount) + '</div></div>' +
                '<div class="ri-meta"><span><span class="mk">打款</span>' + escHtml(it.paid_by || '—') + '</span>' +
                '<span><span class="mk">回单</span>已归档</span></div>' +
                '<div class="ri-foot">' + typeTagOf(it) + '<span class="tag status-paid">💰 已打款·待存档</span></div>' +
                '<div class="item-actions">' +
                '<button class="btn-mini gray" data-action="open-detail" data-id="' + escHtml(it.request_id) + '">📄 明细</button>' +
                '<button class="btn-mini green" data-action="open-file-sheet" data-id="' + escHtml(it.request_id) + '">📦 存档备案</button>' +
                '</div></div>';
        }).join('');
    }

    function postFinance(id, action, onDone) {
        fetch('/api/finance', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken() },
            body: JSON.stringify({ request_id: id, action: action })
        }).then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
            .then(function (res) {
                if (!res.ok || res.d.error) { showAlert({ title: '操作失败', message: (res.d && res.d.error) || '操作失败' }); }
                else if (onDone) { onDone(res.d); }
                loadFinanceLists();
            })
            .catch(function () { showAlert({ title: '请求失败', message: '网络异常，请重试' }); loadFinanceLists(); });
    }

    window.handleFinance = function (id, action) {
        if (action === '归档') {
            showConfirm({
                title: '复核受理',
                message: '确认复核并受理报销单 ' + id + '？',
                confirmText: '复核',
                onConfirm: function () {
                    postFinance(id, '归档', function () {
                        showAlert({ title: '复核成功', message: '已复核并受理报销单 ' + id + '（请切换出纳岗 FIN-002 登录后发起打款）' });
                    });
                }
            });
            return;
        }
        if (action === '打款') {
            var it = financeItems[id];
            // 舞弊风险拦截（职责分离）：复核人与打款人不能为同一人（后端 /api/finance 同样兜底校验）
            if (it && it.archived_by && currentAccount && it.archived_by === currentAccount) {
                showAlert({ title: '舞弊风险拦截', message: '复核人（' + it.archived_by + '）与打款人不能为同一人。请切换「出纳岗（FIN-002）」账号登录后再发起打款。' });
                return;
            }
            showConfirm({
                title: '发起打款',
                message: '确认打款报销单 ' + id + (it ? '（金额 ' + money(it.apply_amount) + '）' : '') + '？',
                confirmText: '打款',
                danger: true,
                onConfirm: function () {
                    postFinance(id, '打款', function () {
                        showAlert({ title: '打款成功', message: '已打款报销单 ' + id + '（银行回单已自动回写归档，请在「待存档备案」中完成存档）' });
                    });
                }
            });
            return;
        }
    };

    /* ── 存档备案 Sheet（凭证 + 审批记录 + 发票影像） ── */
    window.openFileSheet = function (id) {
        var item = financeItems[id];
        if (!item) { showAlert({ title: '操作失败', message: '未找到报销单 ' + id }); return; }
        if (item.workflow_status !== '已打款') { showAlert({ title: '无法存档备案', message: '该报销单尚未打款，无法存档备案。请先发起打款。' }); return; }
        var html = '';
        html += '<p style="font-size:13px;color:var(--ios-gray);margin:0 0 12px;">请核对以下三项材料齐全后，确认统一存档备案。</p>';
        html += '<div class="file-block"><div class="fb-title">🧾 凭证（银行回单 / 付款凭证）</div><div class="detail-list">' +
            infoRow('付款凭证号', 'PAY-' + item.request_id) +
            infoRow('付款金额', money(item.apply_amount)) +
            infoRow('打款人', item.paid_by || '—') +
            infoRow('回单状态', '已回写归档') +
            '</div></div>';
        html += '<div class="file-block"><div class="fb-title">📝 审批记录</div>' +
            renderApprovalRecords(item.approval_records || []) + '</div>';
        html += '<div class="file-block"><div class="fb-title">📄 发票原件</div>' +
            renderInvoiceGallery(item.invoices || [], item.request_id) + '</div>';
        html += '<div style="margin:6px 0 14px;">' +
            '<label class="check-row"><input type="checkbox" id="chkVoucher" checked> 凭证（银行回单）已齐全</label>' +
            '<label class="check-row"><input type="checkbox" id="chkApproval" checked> 审批记录已齐全</label>' +
            '<label class="check-row"><input type="checkbox" id="chkImage" checked> 发票原件已齐全</label>' +
            '</div>';
        html += '<button class="btn-primary approve" data-action="confirm-file" data-id="' + escHtml(item.request_id) + '">📦 确认存档备案</button>';
        document.getElementById('fileSheetTitle').textContent = '存档备案';
        document.getElementById('fileSheetBody').innerHTML = html;
        document.getElementById('fileSheet').classList.add('show');
    };
    window.closeFileSheet = function () {
        document.getElementById('fileSheet').classList.remove('show');
    };
    window.confirmFile = function (id) {
        var v = document.getElementById('chkVoucher') && document.getElementById('chkVoucher').checked;
        var a = document.getElementById('chkApproval') && document.getElementById('chkApproval').checked;
        var i = document.getElementById('chkImage') && document.getElementById('chkImage').checked;
        if (!(v && a && i)) { showAlert({ title: '材料不齐', message: '请确认凭证、审批记录、发票原件三项均已齐全后再存档备案。' }); return; }
        postFinance(id, '备案', function () {
            closeFileSheet();
            showAlert({ title: '存档备案成功', message: '已存档备案报销单 ' + id + '（凭证、审批记录及发票影像已统一归档备案）' });
        });
    };

    /* ═════════════════ 发票影像预览 ═════════════════ */
    var invViewer = {
        rid: '',
        idx: 0,
        page: 1,
        totalPages: 1
    };

    window.openInvoice = function (rid, idx) {
        invViewer.rid = rid;
        invViewer.idx = idx;
        invViewer.page = 1;
        invViewer.totalPages = 1;
        loadInvoicePage(1);
        document.getElementById('invoiceSheet').classList.add('show');
    };

    function loadInvoicePage(n) {
        var img = document.getElementById('invoicePageImg');
        var prevBtn = document.getElementById('invPagePrev');
        var nextBtn = document.getElementById('invPageNext');
        var info = document.getElementById('invPageInfo');

        // 显示加载状态
        img.src = '';
        img.style.display = 'block';
        info.textContent = '加载中…';
        prevBtn.disabled = true;
        nextBtn.disabled = true;

        var url = '/api/reimbursement/' + encodeURIComponent(invViewer.rid) +
            '/invoice/' + invViewer.idx + '/page/' + n;
        img.src = url;
        img.onload = function () {
            prevBtn.disabled = n <= 1;
            info.textContent = '第 ' + n + ' 页';
            invViewer.page = n;
        };
        img.onerror = function () {
            if (n === 1) {
                // 第 1 页加载失败 → 影像缺失
                img.style.display = 'none';
                info.textContent = '影像缺失';
                prevBtn.disabled = true;
                nextBtn.disabled = true;
            } else {
                // 页码越界 → 停在上一页
                info.textContent = '第 ' + invViewer.page + ' 页（共 ' + invViewer.page + ' 页）';
                invViewer.totalPages = Math.max(invViewer.page, 1);
                prevBtn.disabled = invViewer.page <= 1;
                nextBtn.disabled = true;
            }
        };
    }

    window.invPagePrev = function () {
        if (invViewer.page > 1) {
            loadInvoicePage(invViewer.page - 1);
        }
    };

    window.invPageNext = function () {
        loadInvoicePage(invViewer.page + 1);
    };

    window.closeInvoice = function () {
        document.getElementById('invoiceSheet').classList.remove('show');
    };

    window.invDownload = function () {
        var url = '/api/reimbursement/' + encodeURIComponent(invViewer.rid) +
            '/invoice/' + invViewer.idx + '/file';
        window.open(url, '_blank');
    };

    /* ═════════════════ 系统管理员：系统配置 ═════════════════ */
    var adminConfigLoaded = false;

    function loadAdminConfig(force) {
        var wrap = document.getElementById('adminConfigGroups');
        if (!wrap) return;
        if (adminConfigLoaded && !force) return;
        fetch('/api/admin/config').then(function (r) { return r.json(); }).then(function (data) {
            if (data.error) { wrap.innerHTML = '<div class="empty"><div class="et">' + escHtml(data.error) + '</div></div>'; return; }
            renderAdminConfig(data.schema || [], data.config || {});
            adminConfigLoaded = true;
        }).catch(function () {
            wrap.innerHTML = '<div class="empty"><div class="et">加载失败，请重试</div></div>';
        });
    }

    function renderAdminConfig(schema, config) {
        var wrap = document.getElementById('adminConfigGroups');
        var html = '';
        schema.forEach(function (grp) {
            html += '<div class="group"><div class="group-title">' + escHtml(grp.group) + '</div>';
            (grp.items || []).forEach(function (it) {
                var val = config[it.key];
                var labelHtml = escHtml(it.label) + (it.env ? '<span class="cfg-env">' + escHtml(it.env) + '</span>' : '');
                if (it.type === 'toggle') {
                    html += '<div class="row"><div class="row-label" style="flex:1;display:block;">' + labelHtml + '</div>' +
                        '<div class="ios-switch' + (val ? ' on' : '') + '" data-cfg-key="' + escHtml(it.key) + '"></div></div>';
                } else if (it.type === 'number') {
                    html += '<div class="row"><div class="row-label" style="flex:1;display:block;">' + labelHtml + '</div>' +
                        '<input type="number" class="cfg-input" data-cfg-key="' + escHtml(it.key) + '" value="' + escHtml(val != null ? val : '') + '">' +
                        (it.unit ? '<span class="cfg-suffix">' + escHtml(it.unit) + '</span>' : '') + '</div>';
                } else {
                    var inputType = it.type === 'secret' ? 'password' : 'text';
                    var ph = it.type === 'secret' ? '留空使用环境变量默认值' : '';
                    html += '<div class="row"><div class="row-label" style="flex:none;display:block;max-width:44%;">' + labelHtml + '</div>' +
                        '<input type="' + inputType + '" class="cfg-input wide" data-cfg-key="' + escHtml(it.key) + '" value="' + escHtml(val != null ? val : '') + '" placeholder="' + ph + '"></div>';
                }
            });
            html += '</div>';
        });
        wrap.innerHTML = html;
    }

    window.saveConfig = function () {
        var items = {};
        document.querySelectorAll('#adminConfigGroups [data-cfg-key]').forEach(function (el) {
            var key = el.getAttribute('data-cfg-key');
            if (el.classList && el.classList.contains('ios-switch')) {
                items[key] = el.classList.contains('on');
            } else if (el.type === 'number') {
                items[key] = el.value === '' ? 0 : Number(el.value);
            } else {
                items[key] = el.value;
            }
        });
        fetch('/api/admin/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken() },
            body: JSON.stringify({ items: items })
        }).then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
            .then(function (res) {
                if (!res.ok || res.d.error) { showToast((res.d && res.d.error) || '保存失败', 'error'); return; }
                showToast('配置已保存：共 ' + Object.keys(items).length + ' 项配置进入审计日志并立即生效。', 'success', 3200);
                loadAdminConfig(true);
            })
            .catch(function () { showToast('请求失败，请重试', 'error'); });
    };

    window.resetConfig = function () {
        showConfirm({
            title: '恢复默认配置',
            message: '确认将全部系统配置恢复为默认值？此操作会写入审计日志。',
            confirmText: '恢复默认',
            danger: true,
            onConfirm: function () {
                fetch('/api/admin/config/reset', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken() },
                    body: JSON.stringify({})
                }).then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
                    .then(function (res) {
                        if (!res.ok || res.d.error) { showToast((res.d && res.d.error) || '恢复失败', 'error'); return; }
                        showToast('已恢复默认值', 'success');
                        loadAdminConfig(true);
                    })
                    .catch(function () { showToast('请求失败，请重试', 'error'); });
            }
        });
    };

    /* ═════════════════ 系统管理员：审计日志 ═════════════════ */
    var AUDIT_ACTION_LABELS = {
        'SUBMIT': '📤 提交报销', 'APPROVE': '✓ 审批通过', 'REJECT': '✕ 审批驳回', 'TRANSFER': '↪️ 转审',
        'ARCHIVE': '📦 受理', 'BUDGET_CHECK': '💰 预算核对', 'ARCHIVE_FILING': '📦 存档备案',
        'PAYMENT_INIT': '💰 发起打款', 'RECEIPT_ARCHIVE': '🏦 回单归档', 'LOGIN': '🔓 登录', 'LOGIN_FAILED': '⚠️ 登录失败',
        'CONFIG_UPDATE': '⚙️ 配置更新', 'CONFIG_RESET': '↩️ 配置重置', 'RULE_TOGGLE': '🚦 规则切换', 'PERMISSION_GRANT': '👥 权限授予'
    };

    function loadAuditLog() {
        var el = document.getElementById('auditList');
        if (!el) return;
        fetch('/api/admin/audit').then(function (r) { return r.json(); }).then(function (data) {
            if (data.error) { el.innerHTML = '<div class="empty"><div class="et">' + escHtml(data.error) + '</div></div>'; return; }
            var items = data.items || [];
            var c = document.getElementById('auditCount');
            if (c) c.textContent = items.length;
            if (!items.length) {
                el.innerHTML = '<div class="empty"><div class="ei">📜</div><div class="et">暂无审计日志</div></div>';
                return;
            }
            el.innerHTML = items.map(function (r, i) {
                var label = AUDIT_ACTION_LABELS[r.action] || r.action;
                return '<div class="audit-item stagger-item" style="--i:' + i + '">' +
                    '<div class="audit-top"><span class="audit-action-tag">' + escHtml(label) + '</span>' +
                    '<span class="audit-result ' + (r.result === '成功' ? 'ok' : 'err') + '">' + escHtml(r.result || '—') + '</span></div>' +
                    '<div class="audit-target">' + escHtml(r.target || '—') + '</div>' +
                    '<div class="audit-meta"><span>👤 ' + escHtml(r.user || '—') + ' · ' + escHtml(r.role || '—') + '</span>' +
                    '<span>🕐 ' + escHtml(r.time || '—') + '</span><span>IP ' + escHtml(r.ip || '—') + '</span></div>' +
                    '</div>';
            }).join('');
        }).catch(function () {
            el.innerHTML = '<div class="empty"><div class="et">加载失败，请重试</div></div>';
        });
    }

    /* ═════════════════ 系统管理员：用量统计 ═════════════════ */
    var PRICE_INPUT_PER_1K = 0.001, PRICE_OUTPUT_PER_1K = 0.002;
    function calcCostCny(pt, ct) { return (pt / 1000) * PRICE_INPUT_PER_1K + (ct / 1000) * PRICE_OUTPUT_PER_1K; }
    function formatTokens(n) {
        n = Number(n) || 0;
        if (n >= 1000000) return (n / 1000000).toFixed(2) + 'M';
        if (n >= 1000) return (n / 1000).toFixed(1) + 'K';
        return String(n);
    }

    function loadUsage() {
        fetch('/api/admin/usage').then(function (r) { return r.json(); }).then(function (data) {
            if (data.error) return;
            renderUsageOverview(data.overview || {});
            renderUsageDaily(data.daily || []);
            renderUsageByType(data.by_type || []);
            renderUsageRecords(data.records || []);
        }).catch(function () { /* 网络异常静默 */ });
    }

    function renderUsageOverview(o) {
        var el = document.getElementById('usageOverview');
        if (!el) return;
        var total = o.total_calls || 0;
        var errs = o.error_count || 0;
        var ok = total - errs;
        el.innerHTML = '' +
            '<div class="metric-card stagger-item" style="--i:0"><div class="metric-icon">📋</div><div class="metric-value blue">' + total.toLocaleString() + '</div><div class="metric-label">总调用次数</div><div class="metric-sub">成功 ' + ok.toLocaleString() + ' · 失败 ' + errs + '</div></div>' +
            '<div class="metric-card stagger-item" style="--i:1"><div class="metric-icon">🔢</div><div class="metric-value purple">' + formatTokens(o.total_tokens) + '</div><div class="metric-label">Token 总量</div><div class="metric-sub">输入 ' + formatTokens(o.total_prompt_tokens) + ' · 输出 ' + formatTokens(o.total_completion_tokens) + '</div></div>' +
            '<div class="metric-card stagger-item" style="--i:2"><div class="metric-icon">💰</div><div class="metric-value green">¥' + Number(o.estimated_cost_cny || 0).toFixed(2) + '</div><div class="metric-label">预估费用 (CNY)</div><div class="metric-sub">按 DeepSeek-V4-Flash 定价</div></div>' +
            '<div class="metric-card stagger-item" style="--i:3"><div class="metric-icon">⚡</div><div class="metric-value orange">' + (Number(o.avg_latency_ms || 0) / 1000).toFixed(1) + 's</div><div class="metric-label">平均延迟</div><div class="metric-sub">成功率 ' + (o.success_rate != null ? o.success_rate : 0) + '%</div></div>';
    }

    function renderUsageDaily(daily) {
        var bars = document.getElementById('usageDailyBars');
        var labels = document.getElementById('usageDailyLabels');
        if (!bars) return;
        if (!daily.length) {
            bars.innerHTML = '<div style="flex:1;text-align:center;font-size:12px;color:var(--ios-gray);align-self:center;">暂无调用数据</div>';
            if (labels) labels.innerHTML = '';
            return;
        }
        var max = Math.max.apply(null, daily.map(function (d) { return d.tokens || 0; })) || 1;
        var bh = '', lh = '';
        daily.forEach(function (d, i) {
            var pct = Math.max(4, ((d.tokens || 0) / max * 100));
            bh += '<div class="chart-col stagger-item" style="--i:' + i + '"><div class="chart-bar" style="height:' + pct.toFixed(0) + '%;"><span class="bar-calls">' + (d.calls || 0) + '</span></div></div>';
            lh += '<span>' + escHtml(d.date || '—') + '</span>';
        });
        bars.innerHTML = bh;
        if (labels) labels.innerHTML = lh;
    }

    function renderUsageByType(byType) {
        var el = document.getElementById('usageByType');
        if (!el) return;
        if (!byType.length) {
            el.innerHTML = '<div style="font-size:12px;color:var(--ios-gray);">暂无调用数据</div>';
            return;
        }
        var total = byType.reduce(function (s, t) { return s + (t.tokens || 0); }, 0) || 1;
        var colors = ['var(--ios-blue)', 'var(--ios-indigo)', 'var(--ios-orange)', '#30B0C7', 'var(--ios-green)'];
        el.innerHTML = byType.map(function (t, i) {
            var pct = ((t.tokens || 0) / total * 100).toFixed(1);
            return '<div class="type-row"><div class="type-head"><span class="tn">' + escHtml(t.type || '—') + '</span>' +
                '<span class="ts">' + (t.calls || 0) + ' 次 · ' + formatTokens(t.tokens) + ' · ¥' + Number(t.cost || 0).toFixed(2) + ' · ' + pct + '%</span></div>' +
                '<div class="type-track"><div class="type-fill" style="width:' + pct + '%;background:' + colors[i % colors.length] + ';"></div></div></div>';
        }).join('');
    }

    function renderUsageRecords(records) {
        var el = document.getElementById('usageRecords');
        if (!el) return;
        var c = document.getElementById('usageRecCount');
        if (c) c.textContent = records.length;
        if (!records.length) {
            el.innerHTML = '<div class="empty"><div class="ei">📊</div><div class="et">暂无调用明细</div></div>';
            return;
        }
        el.innerHTML = records.map(function (r, i) {
            var pt = r.prompt_tokens || 0, ct = r.completion_tokens || 0;
            var total = pt + ct;
            var cost = r.cost_cny != null ? Number(r.cost_cny) : calcCostCny(pt, ct);
            var latency = (r.latency_ms === 0 || r.latency_ms == null) ? '—' : (r.latency_ms + 'ms');
            return '<div class="usage-rec stagger-item" style="--i:' + i + '">' +
                '<div class="ur-top"><span>' + escHtml(r.call_type || '—') + '</span><span class="usage-pill ' + (r.status === '成功' ? 'success' : 'error') + '">' + escHtml(r.status || '—') + '</span></div>' +
                '<div class="ur-meta"><span>🕐 ' + escHtml(r.time || '—') + '</span><span>ID ' + escHtml(r.request_id || '—') + '</span></div>' +
                '<div class="ur-meta"><span>输入 ' + pt.toLocaleString() + '</span><span>输出 ' + ct.toLocaleString() + '</span><span>总 ' + total.toLocaleString() + '</span><span>延迟 ' + latency + '</span><span style="color:var(--ios-green);font-weight:700;">¥' + cost.toFixed(4) + '</span></div>' +
                '</div>';
        }).join('');
    }

    /* ═════════════════ 使用指南 · OA 报销助手对话（纯前端 mock，与 prototype_ios.html 一致） ═════════════════ */
    var GUIDE_ROLES = '' +
        '<div class="list-header">角色说明</div>' +
        '<div class="group">' +
        '  <div class="group-title">👤 员工 · 张三（EMP-2026）</div>' +
        '  <div class="row col"><div class="row-label" style="font-weight:600;">提交人</div><div class="row-sub">提交日常差旅、餐饮、住宿等报销申请，上传发票 / 行程单影像，等待审批结果。</div></div>' +
        '</div>' +
        '<div class="group">' +
        '  <div class="group-title">👔 主管 · 李总（APR-001）</div>' +
        '  <div class="row col"><div class="row-label" style="font-weight:600;">一级审批</div><div class="row-sub">审核下属报销申请，依据费用限额与审批权限通过 / 驳回 / 转审（上级或部门总监）。</div></div>' +
        '</div>' +
        '<div class="group">' +
        '  <div class="group-title">💼 财务 · 王会计（FIN-001）</div>' +
        '  <div class="row col"><div class="row-label" style="font-weight:600;">复核</div><div class="row-sub">复核 AI 校验结果，确认发票原件、凭证与审批记录齐全；与出纳须为不同人。</div></div>' +
        '</div>' +
        '<div class="group">' +
        '  <div class="group-title">🏦 出纳 · 李出纳（FIN-002）</div>' +
        '  <div class="row col"><div class="row-label" style="font-weight:600;">打款、回单归档 和存档备案</div><div class="row-sub">对已复核报销单发起打款，上传银行回单归档，并将凭证、审批记录、发票原件统一存档备案。</div></div>' +
        '</div>' +
        '<div class="group">' +
        '  <div class="group-title">⚙️ 系统管理员 · 赵管理（ADM-001）</div>' +
        '  <div class="row col"><div class="row-label" style="font-weight:600;">系统配置、审计日志和用量统计</div><div class="row-sub">系统配置：维护 DeepSeek 大模型、异常检测规则、费用限额与审批权限；审计日志：追溯全部操作；用量统计：查看 AI 调用与费用。</div></div>' +
        '</div>' +
        '<p class="footnote">员工与主管、财务与出纳职责分离，关键操作均写入审计日志。</p>';
    var GUIDE_SCENES = '' +
        '<div class="list-header">使用场景</div>' +
        '<div class="card guide-scene">' +
        '  <div class="scene-step"><span class="scene-dot">1</span><div><b>员工提交报销</b><br><span class="row-sub">在「报销申请」页选择发票 / 行程单，AI 智能体自动 OCR 提取、异常检测、分类限额校验，提交后进入审批流转。</span></div></div>' +
        '  <div class="scene-step"><span class="scene-dot">2</span><div><b>主管审批</b><br><span class="row-sub">在「待审工作台」查看 AI 摘要与校验结论，通过 / 驳回 / 转审。大额或需会签时由多人把关。</span></div></div>' +
        '  <div class="scene-step"><span class="scene-dot">3</span><div><b>财务复核</b><br><span class="row-sub">财务在「财务」页复核 AI 结果，确认发票原件齐全后，流转至出纳。</span></div></div>' +
        '  <div class="scene-step"><span class="scene-dot">4</span><div><b>出纳打款和回单归档</b><br><span class="row-sub">出纳在「出纳」页对复核单发起打款，上传银行回单归档，并进入待存档备案。</span></div></div>' +
        '  <div class="scene-step"><span class="scene-dot">5</span><div><b>出纳存档备案</b><br><span class="row-sub">出纳确认凭证、审批记录、发票原件三项齐全后存档备案，报销单归档结案。</span></div></div>' +
        '  <div class="scene-step end"><span class="scene-dot">6</span><div><b>管理员维护与审计</b><br><span class="row-sub">系统管理员在「系统配置」维护制度规则，在「审计日志」追溯全部操作，在「用量统计」查看 AI 调用与费用。</span></div></div>' +
        '</div>';
    var GUIDE_CONFIG = '' +
        '<div class="list-header">系统配置说明</div>' +
        '<p class="row-sub" style="margin:0 2px 10px;">系统管理员在「系统配置」中维护制度规则，所有变更进入审计日志并立即生效：</p>' +
        '<div class="group">' +
        '  <div class="group-title">🤖 DeepSeek 大模型</div>' +
        '  <div class="row col"><div class="row-label" style="font-weight:600;">AI 引擎</div><div class="row-sub">启用 DeepSeek 大模型驱动 OCR 提取、语义复核与异常检测；可配置 API 密钥 / 地址 / 模型名称（环境变量 DEEPSEEK_API_KEY、DEEPSEEK_BASE_URL、DEEPSEEK_MODEL）。</div></div>' +
        '</div>' +
        '<div class="group">' +
        '  <div class="group-title">🚨 异常检测规则</div>' +
        '  <div class="row col"><div class="row-label" style="font-weight:600;">风控开关</div><div class="row-sub">金额异常、发票真伪（国税查验）、行程单字段完整性、DeepSeek 语义复核，均可单独开启 / 关闭。</div></div>' +
        '</div>' +
        '<div class="group">' +
        '  <div class="group-title">💰 费用限额配置（月度）</div>' +
        '  <div class="row col"><div class="row-label" style="font-weight:600;">分类限额</div><div class="row-sub">交通 600 元、住宿 1000 元、餐饮 1000 元、办公 200 元、其他 200 元，提交时自动分类校验。</div></div>' +
        '</div>' +
        '<div class="group">' +
        '  <div class="group-title">👥 审批权限分配</div>' +
        '  <div class="row col"><div class="row-label" style="font-weight:600;">分级审批</div><div class="row-sub">≤3000 元直属领导；3000~10000 元部门总监；10000~50000 元 VP；&gt;50000 元 CEO；≥10000 元需两人会签。</div></div>' +
        '</div>' +
        '<p class="footnote">所有配置变更会写入审计日志并立即生效。</p>';

    var guideInit = false;
    function ensureGuideChat() {
        if (!guideInit) { initGuideChat(); guideInit = true; }
    }
    function guideAppend(cls, html) {
        var c = document.getElementById('guideChat');
        if (!c) return;
        var d = document.createElement('div');
        d.className = 'msg-' + cls;
        if (cls === 'user') { d.textContent = html; }
        else { d.innerHTML = '<div class="bubble">' + html + '</div>'; }
        c.appendChild(d);
        c.scrollTop = c.scrollHeight;
    }
    function initGuideChat() {
        var c = document.getElementById('guideChat');
        if (!c) return;
        c.innerHTML = '';
        guideAppend('bot', '你好，我是 <b>智能化报销助手</b> 👋<br>点击下方快捷提示词，或在输入框输入「用户角色 / 使用场景 / 系统配置」等关键词，了解系统说明。');
    }
    function sendGuidePreset(kw) {
        var i = document.getElementById('guideInput');
        if (i) i.value = kw;
        sendGuide();
    }
    function sendGuide() {
        var inp = document.getElementById('guideInput');
        if (!inp) return;
        var v = inp.value.trim();
        if (!v) return;
        inp.value = '';
        guideAppend('user', v);
        if (v.indexOf('角色') >= 0 || v.indexOf('用户') >= 0) { guideAppend('bot', GUIDE_ROLES); }
        else if (v.indexOf('场景') >= 0) { guideAppend('bot', GUIDE_SCENES); }
        else if (v.indexOf('配置') >= 0 || v.indexOf('制度') >= 0 || v.indexOf('权限') >= 0 || v.indexOf('限额') >= 0) { guideAppend('bot', GUIDE_CONFIG); }
        else {
            guideAppend('bot', '我可以帮你了解：<br>• <b>用户角色</b> — 5 类角色职责说明<br>• <b>使用场景</b> — 报销全流程 6 步<br>• <b>系统配置</b> — DeepSeek 大模型、异常检测、费用限额与审批权限<br>请输入以上任一关键词，或点击下方快捷提示词。');
        }
    }
    var guideInputEl = document.getElementById('guideInput');
    if (guideInputEl) {
        guideInputEl.addEventListener('keydown', function (e) {
            if (e.key === 'Enter') { e.preventDefault(); sendGuide(); }
        });
    }

    /* ───────────────── 事件委托（P2：去内联事件，配合 /m 严格 CSP script-src 'self'） ───────────────── */
    var SHEET_CLOSERS = {
        pipelineSheet: function () { window.closePipelineSheet(); },
        detailSheet: function () { window.closeDetail(); },
        fileSheet: function () { window.closeFileSheet(); },
        invoiceSheet: function () { window.closeInvoice(); },
        successSheet: function () { window.closeSuccess(); }
    };

    document.addEventListener('click', function (e) {
        // Sheet 遮罩：点击遮罩空白处关闭
        if (e.target.classList && e.target.classList.contains('sheet-overlay')) {
            var closer = SHEET_CLOSERS[e.target.id];
            if (closer) { closer(); return; }
        }
        // 系统配置 iOS 开关
        var sw = e.target.closest ? e.target.closest('.ios-switch[data-cfg-key]') : null;
        if (sw) { sw.classList.toggle('on'); return; }
        // 底部 Tab Bar
        var tabBtn = e.target.closest ? e.target.closest('.tab-item[data-tab]') : null;
        if (tabBtn) { window.switchTab(tabBtn.getAttribute('data-tab')); return; }
        // 通用 data-action 派发
        var el = e.target.closest ? e.target.closest('[data-action]') : null;
        if (!el) return;
        var action = el.getAttribute('data-action');
        var id = el.getAttribute('data-id');
        var act = el.getAttribute('data-act');
        switch (action) {
            case 'logout': window.logout(); break;
            case 'submit': window.onSubmitClick(); break;
            case 'save-config': window.saveConfig(); break;
            case 'reset-config': window.resetConfig(); break;
            case 'close-sheet':
                var fn = SHEET_CLOSERS[el.getAttribute('data-sheet')];
                if (fn) fn();
                break;
            case 'open-detail': window.openDetail(id); break;
            case 'approve': window.handleApprove(id, act); break;
            case 'finance': window.handleFinance(id, act); break;
            case 'open-file-sheet': window.openFileSheet(id); break;
            case 'confirm-file': window.confirmFile(id); break;
            case 'open-invoice': window.openInvoice(el.getAttribute('data-rid'), parseInt(el.getAttribute('data-idx'), 10)); break;
            case 'inv-page-prev': window.invPagePrev(); break;
            case 'inv-page-next': window.invPageNext(); break;
            case 'inv-download': window.invDownload(); break;
            case 'guide-preset': sendGuidePreset(el.getAttribute('data-kw')); break;
            case 'guide-send': sendGuide(); break;
            case 'reupload':
                resetUploadForm();
                if (fileInput) fileInput.click();
                break;
        }
    });

    /* ───────────────── 初始化 ───────────────── */
    if (isLoggedIn) {
        renderTabBar(currentRole);
        var initTabs = ROLE_TABS[currentRole] || ['reimburse'];
        // 与原型一致：系统管理员默认落地「使用指南」
        switchTab(currentRole === 'admin' ? 'guide' : initTabs[0]);
    } else {
        renderTabBar('employee');
    }
    // 注册 Service Worker（PWA「添加到主屏幕」+ 离线壳）
    // 脚本部署在 /m/ 下，作用域锁定为 /m/，不会拦截桌面端请求；非 HTTPS/localhost 时静默失败
    if ('serviceWorker' in navigator) {
        window.addEventListener('load', function () {
            navigator.serviceWorker.register('/m/sw.js', { scope: '/m/' }).catch(function () {});
        });
    }
})();
