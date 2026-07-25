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
    var currentTicketType = '发票';
    var lastCheckPassed = false;
    var lastRequestId = '';
    var dsDisabled = false;          // DeepSeek 是否停用
    var selectedFile = null;
    var isLoggedIn = document.body.getAttribute('data-logged-in') === 'true';

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

    /* ───────────────── 登录 / 退出 ───────────────── */
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
        if (!confirm('确定退出登录吗？')) return;
        fetch('/api/auth/logout', {
            method: 'POST',
            headers: { 'X-CSRF-Token': csrfToken() }
        }).then(function () { location.reload(); })
            .catch(function () { location.reload(); });
    };

    /* ───────────────── Tab 切换 ───────────────── */
    window.switchTab = function (tab) {
        document.querySelectorAll('.tab-item').forEach(function (b) {
            b.classList.toggle('active', b.getAttribute('data-tab') === tab);
        });
        document.getElementById('tab-reimburse').style.display = tab === 'reimburse' ? 'block' : 'none';
        document.getElementById('tab-my').style.display = tab === 'my' ? 'block' : 'none';
        document.getElementById('navTitle').textContent = '首页';
        document.querySelector('.content').scrollTop = 0;
        if (tab === 'my') loadMyList();
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
                alert('仅支持 PDF / JPG / PNG 格式'); fileInput.value = ''; return;
            }
            if (f.size > 10 * 1024 * 1024) {
                alert('文件超过 10MB 限制'); fileInput.value = ''; return;
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
        ['apply_amount', 'apply_date', 'invoice_date'].forEach(function (id) { document.getElementById(id).value = ''; });
        document.getElementById('expense_category').value = '';
        document.getElementById('reason').value = '';
        document.getElementById('invoice_number').value = '';
        document.getElementById('invoiceNumberRow').style.display = 'none';
        document.getElementById('invoiceDateRow').style.display = 'none';
        document.getElementById('autoFields').style.display = 'none';
        var note = document.getElementById('autoFieldsNote');
        if (note) note.style.display = 'none';
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
        else { btn.textContent = '提交校验'; btn.classList.remove('approve'); }
    }
    window.setSubmitMode = setSubmitMode;

    window.onSubmitClick = function () {
        var btn = document.getElementById('submitBtn');
        if (btn.classList.contains('approve')) { submitApprove(); }
        else {
            if (document.getElementById('uploadPreview').style.display !== 'block') {
                alert('请先选择票据文件，再提交校验。'); return;
            }
            runCheck();
        }
    };

    /* ───────────────── 智能体流水线 ───────────────── */
    var pipeTimer = null, pipeIdx = 0, pipeStepsData = [], pipeResolved = false, pipelineStarted = false;

    function setupPipeSheet(isIt, steps) {
        pipeStepsData = steps; pipeIdx = 0; pipeResolved = false; pipelineStarted = false;
        document.getElementById('pipeTitle').textContent = isIt ? '行程单智能体执行流水线' : '发票智能体执行流水线';
        document.getElementById('pipeBadge').innerHTML = (isIt ? '🚕' : '🧾') + ' ' + (isIt ? '行程单智能体' : '发票智能体');
        var pr = document.getElementById('pipeResult'); pr.style.display = 'none'; pr.className = 'result-card';
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
        if (!selectedFile) { alert('请先选择票据文件'); return; }
        var btn = document.getElementById('submitBtn');
        btn.disabled = true;
        var isIt = currentTicketType === '行程单';
        var steps = isIt ? ITINERARY_STEPS : INVOICE_STEPS;
        setupPipeSheet(isIt, steps);

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
        function present(res) {
            btn.disabled = false;
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
            if (!dsDisabled) { pipelineStarted = true; startPipelineAnim(); }
            if (resolved) present(resolved);
        });
        uploadPromise.then(function (res) {
            resolved = res;
            if (dsDisabled || pipelineStarted) present(res);
        });
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
        }
        if (dsDisabled) enableManualMode(data);
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
        rc.innerHTML = '<div class="rc-icon">' + meta.icon + '</div><div class="rc-body"><div class="rc-label">' + meta.label + '</div><div class="rc-summary">' + escHtml(data.summary || '') + '</div></div>';
        var ic = document.getElementById('resultCard');
        ic.className = 'result-card ' + meta.cls; ic.style.display = 'flex';
        ic.innerHTML = rc.innerHTML;
    }
    function renderErrorResult(msg) {
        var rc = document.getElementById('pipeResult');
        rc.className = 'result-card block'; rc.style.display = 'flex';
        rc.innerHTML = '<div class="rc-icon">❌</div><div class="rc-body"><div class="rc-label">校验失败</div><div class="rc-summary">' + escHtml(msg) + '</div></div>';
        var ic = document.getElementById('resultCard');
        ic.className = 'result-card block'; ic.style.display = 'flex'; ic.innerHTML = rc.innerHTML;
    }

    /* ───────────────── AI 回写字段 ───────────────── */
    function markAuto(id) {
        var el = document.getElementById(id);
        if (el) el.classList.add('auto-filled');
    }
    function autoFillFromOcr(data) {
        var isIt = currentTicketType === '行程单';
        var ocr = data.ocr_result || {};
        var amount = isIt ? ocr['总金额_元'] : (ocr['发票金额'] != null ? ocr['发票金额'] : ocr['价税合计_小写']);
        if (amount != null && amount !== '') { document.getElementById('apply_amount').value = amount; markAuto('apply_amount'); }
        var cat = isIt ? '交通' : ((data.classify_result && data.classify_result['费用分类']) || '住宿');
        document.getElementById('expense_category').value = cat; markAuto('expense_category');
        var date = isIt ? (ocr['申请日期'] || '') : (ocr['开票日期'] || '');
        if (date) { document.getElementById('apply_date').value = String(date).slice(0, 10); markAuto('apply_date'); }
        if (!document.getElementById('apply_date').value) {
            document.getElementById('apply_date').value = new Date().toISOString().slice(0, 10);
        }
        document.getElementById('reason').value = isIt ? '北京出差市内交通' : '北京出差交通费';
        document.getElementById('autoFields').style.display = 'block';
        // 发票启用态：预填发票号码与开票日期（停用态无 OCR，留空待人工补录）
        if (!isIt && ocr['发票号码']) {
            document.getElementById('invoice_number').value = ocr['发票号码'];
        }
        if (!isIt && ocr['开票日期']) {
            document.getElementById('invoice_date').value = String(ocr['开票日期']).slice(0, 10);
            document.getElementById('invoiceDateRow').style.display = 'flex';
        }
    }

    /* ───────────────── 停用态：人工填写 ───────────────── */
    function enableManualMode(data) {
        setSubmitMode('approve');
        document.getElementById('invoiceNumberRow').style.display = 'flex';
        if (currentTicketType === '发票') {
            document.getElementById('invoiceDateRow').style.display = 'flex';
        }
        document.getElementById('autoFields').style.display = 'block';
        var note = document.getElementById('autoFieldsNote');
        if (note) {
            note.style.display = 'block';
            note.textContent = (data && data.summary)
                ? String(data.summary)
                : 'DeepSeek 大模型已停用，请人工填写金额与费用类型后提交（发票类需补录发票号码）。';
        }
    }

    /* ───────────────── 关闭流水线 Sheet ───────────────── */
    window.closePipelineSheet = function () {
        document.getElementById('pipelineSheet').classList.remove('show');
    };

    /* ───────────────── 提交审批 → 我的报销 ───────────────── */
    function submitApprove() {
        if (!lastRequestId) { alert('未找到报销单号，请重新提交校验'); return; }
        var amount = document.getElementById('apply_amount').value.trim();
        var category = document.getElementById('expense_category').value.trim();
        var date = document.getElementById('apply_date').value.trim();
        var reason = document.getElementById('reason').value.trim();
        var invNo = document.getElementById('invoice_number').value.trim();
        var invDate = document.getElementById('invoice_date').value.trim();
        if (!amount || !category) {
            alert('请先填写「申请金额」与「费用类型」后再提交审批');
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
            el.innerHTML = items.map(function (it) {
                var wsMap = {
                    '待审批': '⏳ 待审批', '审批中': '🔄 审批中', '待复核': '✓ 待复核',
                    '已驳回': '✕ 已驳回', '已复核并归档': '📦 已归档', '已打款': '💰 已打款'
                };
                var wsClsMap = {
                    '待审批': 'pending', '审批中': 'pending', '待复核': 'approved',
                    '已驳回': 'pending', '已复核并归档': 'approved', '已打款': 'approved'
                };
                var ws = wsMap[it.workflow_status] || it.workflow_status;
                var wsCls = wsClsMap[it.workflow_status] || 'pending';
                var typeTag = it.ticket_type === '行程单'
                    ? '<span class="tag itinerary">🚕 行程单</span>'
                    : '<span class="tag invoice">🧾 发票</span>';
                var stTag = '<span class="tag ' + wsCls + '">' + ws + '</span>';
                return '<div class="reimb-item" onclick="openDetail(\'' + escHtml(it.request_id) + '\')">' +
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
        var basic = [
            { k: '报销单号', v: d.request_id || '—' },
            { k: '申请金额', v: money(d.apply_amount) },
            { k: '报销事由', v: d.reason || '—' },
            { k: '费用类型', v: d.expense_category || '—' },
            { k: '申请日期', v: fmtDate(d.apply_date) },
            { k: '当前状态', v: d.workflow_status || '—' },
            { k: '提交时间', v: fmtTime(d.created_at) }
        ];
        html += '<div class="detail-list">' + basic.map(function (it) { return infoRow(it.k, it.v); }).join('') + '</div>';

        // 审批记录
        var records = d.approval_records || [];
        html += '<div class="group" style="margin-top:16px;"><div class="group-title">审批记录</div>';
        if (records.length) {
            records.forEach(function (rec) {
                var action = rec.action || '—';
                html += '<div class="row col" style="align-items:stretch;">' +
                    '<div class="row" style="border-top:none;padding:0 0 6px;">' +
                    '<div class="row-label" style="font-weight:600;">' + escHtml(rec.approver_name || rec.approver_id || '—') + '</div>' +
                    '<div class="row-label" style="color:var(--ios-blue);">' + escHtml(action) + '</div></div>' +
                    '<div style="font-size:12px;color:var(--ios-gray);">' + escHtml(fmtTime(rec.action_time)) +
                    (rec.comment ? ' · ' + escHtml(rec.comment) : '') + '</div></div>';
            });
        } else {
            html += '<div class="row" style="border-top:none;color:var(--ios-gray);">暂无审批记录</div>';
        }
        html += '</div>';

        // 发票列表
        var invoices = d.invoices || [];
        html += '<div class="group" style="margin-top:4px;"><div class="group-title">发票列表</div>';
        if (invoices.length) {
            invoices.forEach(function (inv) {
                var noCell = inv.invoice_number ? escHtml(inv.invoice_number) : '缺失，请补录';
                var amt = (inv.invoice_number && inv.invoice_amount != null) ? money(inv.invoice_amount) : '—';
                html += '<div class="row col" style="align-items:stretch;">' +
                    '<div class="row" style="border-top:none;padding:0 0 4px;">' +
                    '<div class="row-label">发票号码</div><div class="row-label" style="font-weight:600;">' + noCell + '</div></div>' +
                    '<div class="row" style="border-top:none;padding:0 0 4px;">' +
                    '<div class="row-label">金额</div><div class="row-label" style="font-weight:600;">' + amt + '</div></div>' +
                    (inv.seller_name ? '<div class="row" style="border-top:none;padding:0;"><div class="row-label">销售方</div><div class="row-label" style="font-weight:600;">' + escHtml(inv.seller_name) + '</div></div>' : '') +
                    '</div>';
            });
        } else {
            html += '<div class="row" style="border-top:none;color:var(--ios-gray);">暂无可展示的发票（行程单类报销单无发票明细）</div>';
        }
        html += '</div>';
        return html;
    }

    /* ───────────────── 初始化 ───────────────── */
    if (isLoggedIn && document.getElementById('myList')) {
        loadMyList();
    }
    // 注册 Service Worker（PWA「添加到主屏幕」+ 离线壳）
    // 脚本部署在 /m/ 下，作用域锁定为 /m/，不会拦截桌面端请求；非 HTTPS/localhost 时静默失败
    if ('serviceWorker' in navigator) {
        window.addEventListener('load', function () {
            navigator.serviceWorker.register('/m/sw.js', { scope: '/m/' }).catch(function () {});
        });
    }
})();
