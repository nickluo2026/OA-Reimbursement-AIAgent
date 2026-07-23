/* ============================================
   发票上传首页 & 校验结果页 — 前端交互脚本
   ============================================ */

(function () {
    'use strict';

    /* ── 状态映射 ── */
    var STATUS_MAP = {
        '通过': { icon: '✅', label: '校验通过', cls: 'pass' },
        '预警': { icon: '⚠️', label: '校验预警', cls: 'warning' },
        '拦截': { icon: '⛔', label: '校验拦截', cls: 'block' },
        '错误': { icon: '❌', label: '系统错误', cls: 'error' },
    };

    /* ── OCR 显示用的关键字段白名单（按展示顺序） ── */
    var OCR_FIELDS = [
        '发票类型', '发票号码', '发票代码', '开票日期',
        '购买方名称', '购买方税号', '销售方名称', '销售方税号',
        '金额', '税率', '税额', '价税合计_大写', '价税合计_小写', '发票金额',
    ];

    /* ── 行程单汇总字段（按展示顺序） ── */
    var ITINERARY_SUMMARY_FIELDS = [
        '申请日期', '行程开始日期', '行程结束日期',
        '总行程数', '总金额_元', '手机号',
    ];

    /* ── 行程明细表列定义 ── */
    var ITINERARY_DETAIL_COLS = [
        { key: '序号', label: '序号' },
        { key: '车型', label: '车型' },
        { key: '上车时间', label: '上车时间' },
        { key: '城市', label: '城市' },
        { key: '起点', label: '起点' },
        { key: '终点', label: '终点' },
        { key: '里程_公里', label: '里程(km)' },
        { key: '金额_元', label: '金额(元)' },
    ];

    /* ── 多文件管理 ── */
    var selectedFiles = [];

    /* ── 当前票据类型 ── */
    var currentTicketType = '';

    /* ========================================
       智能体执行流水线（对应后端 LangGraph 节点）
       ======================================== */
    var PIPELINE_STEPS = {
        '发票': [
            { icon: '🤖', name: '票据类型路由', node: 'route_by_ticket_type', tool: '条件边路由', detail: '识别为发票，路由到【发票智能体】' },
            { icon: '🔍', name: 'OCR 提取发票字段', node: 'ocr_node', tool: 'DeepSeek Vision API', detail: '提取发票类型/号码/金额/商品明细等字段' },
            { icon: '⚠️', name: '异常检测', node: 'anomaly_node', tool: '规则引擎', detail: '校验字段完整性、金额逻辑、重复发票等' },
            { icon: '💰', name: '分类限额校验', node: 'classify_node', tool: 'DeepSeek + 限额规则', detail: '识别费用类型并校验是否超限' },
            { icon: '✅', name: '发票查验', node: 'verify_node', tool: 'Mock Provider', detail: '发票真伪查验（Provider 抽象，默认 Mock 模式）' },
        ],
        '行程单': [
            { icon: '🤖', name: '票据类型路由', node: 'route_by_ticket_type', tool: '条件边路由', detail: '识别为行程单，路由到【行程单智能体】' },
            { icon: '🚕', name: 'OCR 提取行程明细', node: 'itinerary_ocr', tool: 'DeepSeek Vision API', detail: '提取行程汇总信息与明细列表' },
            { icon: '⚠️', name: '行程单异常检测', node: 'itinerary_anomaly', tool: '规则引擎', detail: '校验字段/日期/金额异常' },
            { icon: '✅', name: '行程合理性校验', node: 'itinerary_verify', tool: '合理性规则', detail: '校验金额匹配/天数/连续性' },
        ],
    };

    var pipelineEl = document.getElementById('pipeline');
    var pipelineStepsEl = document.getElementById('pipelineSteps');
    var pipelineTitleEl = document.getElementById('pipelineTitle');
    var pipelineAgentIconEl = document.getElementById('pipelineAgentIcon');
    var pipelineAgentBadgeEl = document.getElementById('pipelineAgentBadge');
    var pipelineTimer = null;
    var pipelineCurrentIdx = 0;
    var pipelineStepsData = [];
    var pipelineStarted = false;

    function startPipeline(ticketType) {
        if (!pipelineEl) return;
        // 重置可能因「DeepSeek 停用」弹窗而隐藏/修改的样式（保证下次正常显示）
        pipelineEl.style.display = '';
        if (pipelineAgentBadgeEl) { pipelineAgentBadgeEl.style.display = ''; }
        pipelineStarted = true;
        pipelineStepsData = (PIPELINE_STEPS[ticketType] || PIPELINE_STEPS['发票']).slice();
        var isItinerary = ticketType === '行程单';

        pipelineEl.classList.toggle('itinerary', isItinerary);
        pipelineAgentIconEl.textContent = isItinerary ? '🚕' : '📄';
        pipelineTitleEl.textContent = (isItinerary ? '行程单' : '发票') + '智能体执行流水线';
        pipelineAgentBadgeEl.textContent = isItinerary ? '行程单智能体' : '发票智能体';
        pipelineAgentBadgeEl.className = 'pipeline-agent-badge' + (isItinerary ? ' itinerary' : '');

        var html = '';
        pipelineStepsData.forEach(function (step, i) {
            html += '<div class="pipeline-step pending" data-idx="' + i + '">' +
                '<div class="step-icon">' + step.icon + '</div>' +
                '<div class="step-body">' +
                '<div class="step-name">' + escHtml(step.name) + '</div>' +
                '<div class="step-meta">' +
                '<span class="step-node">' + escHtml(step.node) + '</span>' +
                '<span class="step-tool">' + escHtml(step.tool) + '</span>' +
                '</div>' +
                '<div class="step-detail">' + escHtml(step.detail) + '</div>' +
                '<div class="step-status">等待中</div>' +
                '</div>' +
                '</div>';
        });
        pipelineStepsEl.innerHTML = html;

        // 打开弹窗并重置结果区
        var rs = document.getElementById('pipelineResultStatus');
        if (rs) rs.style.display = 'none';
        var modal = document.getElementById('pipelineModal');
        if (modal) modal.style.display = 'flex';
        pipelineCurrentIdx = 0;

        advancePipeline();
    }

    function advancePipeline() {
        if (pipelineCurrentIdx >= pipelineStepsData.length) { return; }
        var stepEl = pipelineStepsEl.querySelector('.pipeline-step[data-idx="' + pipelineCurrentIdx + '"]');
        if (!stepEl) { return; }

        stepEl.classList.remove('pending');
        stepEl.classList.add('active');
        var statusEl = stepEl.querySelector('.step-status');
        if (statusEl) { statusEl.innerHTML = '<span class="step-spinner"></span> 执行中...'; }
        stepEl.scrollIntoView({ behavior: 'smooth', block: 'nearest' });

        pipelineTimer = setTimeout(function () {
            stepEl.classList.remove('active');
            stepEl.classList.add('done');
            if (statusEl) { statusEl.textContent = '✓ 完成'; }
            pipelineCurrentIdx++;
            advancePipeline();
        }, 1200);
    }

    function finishPipeline() {
        if (pipelineTimer) { clearTimeout(pipelineTimer); pipelineTimer = null; }
        if (!pipelineStepsEl) { return; }
        var remaining = pipelineStepsEl.querySelectorAll('.pipeline-step.pending, .pipeline-step.active');
        remaining.forEach(function (el) {
            el.classList.remove('pending', 'active');
            el.classList.add('done');
            var statusEl = el.querySelector('.step-status');
            if (statusEl) { statusEl.textContent = '✓ 完成'; }
        });
        pipelineCurrentIdx = pipelineStepsData.length;
    }

    /* ========================================
       首页：文件上传交互
       ======================================== */
    var uploadZone = document.getElementById('uploadZone');
    var fileInput = document.getElementById('fileInput');
    var uploadPlaceholder = document.getElementById('uploadPlaceholder');
    var uploadPreview = document.getElementById('uploadPreview');
    var fileList = document.getElementById('fileList');
    var uploadHint = document.getElementById('uploadHint');

    /* ── 票据类型下拉切换 ── */
    var ticketTypeSelect = document.getElementById('ticket_type_select');
    var ticketTypeInput = document.getElementById('ticket_type');
    if (ticketTypeSelect) {
        ticketTypeSelect.addEventListener('change', function () {
            currentTicketType = ticketTypeSelect.value || '';
            if (ticketTypeInput) { ticketTypeInput.value = currentTicketType; }
            // 切换提示文案
            if (uploadHint) {
                if (currentTicketType === '行程单') {
                    uploadHint.textContent = '支持 PDF、JPG、PNG 格式的行程单文件（如滴滴行程单），单文件最大 10MB';
                } else {
                    uploadHint.textContent = '支持 PDF、JPG、PNG 格式，单文件最大 10MB，支持多文件';
                }
            }
            // 切换票据类型后重置按钮状态并隐藏回写字段
            setSubmitMode('check');
            lastCheckPassed = false;
            isDisabledMode = false;
            hideAutoFields();
        });
    }

    if (uploadZone) {
        uploadZone.addEventListener('click', function (e) {
            if (e.target.closest('.file-remove')) return;
            fileInput.click();
        });

        fileInput.addEventListener('change', function () {
            addFiles(Array.from(fileInput.files));
        });

        uploadZone.addEventListener('dragover', function (e) {
            e.preventDefault();
            uploadZone.classList.add('dragover');
        });
        uploadZone.addEventListener('dragleave', function () {
            uploadZone.classList.remove('dragover');
        });
        uploadZone.addEventListener('drop', function (e) {
            e.preventDefault();
            uploadZone.classList.remove('dragover');
            if (e.dataTransfer.files.length > 0) {
                var dt = new DataTransfer();
                Array.from(e.dataTransfer.files).forEach(function (f) { dt.items.add(f); });
                fileInput.files = dt.files;
                addFiles(Array.from(e.dataTransfer.files));
            }
        });
    }

    function addFiles(files) {
        files.forEach(function (file) {
            var ext = '.' + file.name.split('.').pop().toLowerCase();
            var allowed = ['.pdf', '.jpg', '.jpeg', '.png'];
            if (allowed.indexOf(ext) === -1) {
                alert('不支持的文件类型: ' + file.name + '（仅支持 PDF / JPG / PNG）');
                return;
            }
            if (file.size > 10 * 1024 * 1024) {
                alert('文件 ' + file.name + ' 超过 10MB 限制');
                return;
            }
            // 避免重复添加
            var exists = selectedFiles.some(function (f) { return f.name === file.name && f.size === file.size; });
            if (!exists) {
                selectedFiles.push(file);
            }
        });
        renderFileList();
    }

    function renderFileList() {
        if (selectedFiles.length === 0) {
            uploadPlaceholder.style.display = 'block';
            uploadPreview.style.display = 'none';
            return;
        }
        uploadPlaceholder.style.display = 'none';
        uploadPreview.style.display = 'block';

        var html = '';
        selectedFiles.forEach(function (file, i) {
            var ext = '.' + file.name.split('.').pop().toLowerCase();
            var icon = ext === '.pdf' ? '📄' : '🖼️';
            html += '<div class="file-info">' +
                '<span class="file-icon">' + icon + '</span>' +
                '<div class="file-detail">' +
                '<span class="file-name">' + escHtml(file.name) + '</span>' +
                '<span class="file-size">' + formatSize(file.size) + '</span>' +
                '</div>' +
                '<button type="button" class="file-remove" data-index="' + i + '" title="移除文件">✕</button>' +
                '</div>';
        });
        // 添加更多文件按钮
        html += '<div class="add-more" id="addMoreBtn"><span>+ 添加更多文件</span></div>';
        fileList.innerHTML = html;

        // 绑定移除按钮
        fileList.querySelectorAll('.file-remove').forEach(function (btn) {
            btn.addEventListener('click', function (e) {
                e.stopPropagation();
                var idx = parseInt(btn.getAttribute('data-index'));
                selectedFiles.splice(idx, 1);
                renderFileList();
            });
        });

        // 绑定添加更多
        var addMoreBtn = document.getElementById('addMoreBtn');
        if (addMoreBtn) {
            addMoreBtn.addEventListener('click', function (e) {
                e.stopPropagation();
                fileInput.click();
            });
        }
    }

    function formatSize(bytes) {
        if (bytes < 1024) return bytes + ' B';
        if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
        return (bytes / 1024 / 1024).toFixed(1) + ' MB';
    }

    /* ========================================
       表单提交 & 结果渲染
       ======================================== */
    var uploadForm = document.getElementById('uploadForm');
    var submitBtn = document.getElementById('submitBtn');
    var resultContainer = document.getElementById('resultContainer');

    /* ========================================
       提交按钮状态机：check（提交校验）/ approve（提交审批）
       ======================================== */
    var lastCheckPassed = false;
    var lastRequestIds = [];
    var submitSuccessModalMode = '';
    var isDisabledMode = false;
    var lastDisabledSummary = '';   // 停用态：保存后端返回的统一停用说明
    // 本地兜底文案：仅当后端未返回 summary 时启用；运行期文案统一由后端 config.DEEPSEEK_DISABLED_MSG 下发
    var DISABLED_MSG_FALLBACK = 'DeepSeek 大模型已停用（系统配置），请联系系统管理员启用DeepSeek大模型或者人工填写报销单';

    function setSubmitMode(mode) {
        submitBtn.setAttribute('data-mode', mode);
        var txt = submitBtn.querySelector('.btn-text');
        if (txt) { txt.textContent = mode === 'approve' ? '✅ 提交审批' : '🚀 提交校验'; }
        // 回到「提交校验」模式时隐藏 AI 回写字段，以便重新提交
        if (mode === 'check') { hideAutoFields(); }
    }

    window.onSubmitClick = function () {
        if (submitBtn.getAttribute('data-mode') === 'approve') {
            submitApproval();
        } else {
            runCheck();
        }
    };

    window.closePipelineModal = function () {
        var modal = document.getElementById('pipelineModal');
        if (modal) { modal.style.display = 'none'; }
        // 停用态：展开人工填写字段并切换为「提交审批」
        if (isDisabledMode) {
            enableManualMode();
        } else if (lastCheckPassed) {
            // 校验通过 → 主按钮变为「提交审批」
            setSubmitMode('approve');
        }
    };

    // 点击遮罩层关闭弹窗
    var pipelineMask = document.getElementById('pipelineModal');
    if (pipelineMask) {
        pipelineMask.addEventListener('click', function (e) {
            if (e.target === pipelineMask) { window.closePipelineModal(); }
        });
    }

    // 点击遮罩层关闭提交结果弹窗
    var submitMask = document.getElementById('submitSuccessModal');
    if (submitMask) {
        submitMask.addEventListener('click', function (e) {
            if (e.target === submitMask) { window.closeSubmitSuccessModal(); }
        });
    }

    function submitApproval() {
        var rid = (lastRequestIds && lastRequestIds[0]) ? lastRequestIds[0] : '';
        if (!rid) {
            alert('未找到报销单号，请重新提交校验');
            return;
        }
        // 收集表单当前值（可能已被 AI 回写或人工填写），先 PATCH 落库再提示
        var payload = {
            apply_amount: document.getElementById('apply_amount').value.trim() || null,
            apply_date: document.getElementById('apply_date').value.trim() || null,
            expense_category: document.getElementById('expense_category').value.trim() || null,
            reason: document.getElementById('reason').value.trim() || null,
            invoice_number: document.getElementById('invoice_number').value.trim() || null,
            invoice_date: document.getElementById('invoice_date').value.trim() || null,
        };
        // 停用态 / 人工填写：金额与费用类型为必填，留空拦截
        if (!payload.apply_amount || !payload.expense_category) {
            alert('请先填写「申请金额」与「费用类型」后再提交审批');
            return;
        }
        var csrfMeta = document.querySelector('meta[name="csrf-token"]');
        var csrfToken = csrfMeta ? csrfMeta.content : '';
        fetch('/api/reimbursement/' + encodeURIComponent(rid) + '/update', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken },
            body: JSON.stringify(payload),
        }).then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
            .then(function (res) {
                if (!res.ok || res.d.error) {
                    showSubmitSuccessModal({
                        success: false,
                        requestId: rid,
                        message: (res.d.error || '未知错误') + '（报销单号：' + rid + '）',
                    });
                    return;
                }
                showSubmitSuccessModal({
                    success: true,
                    requestId: rid,
                    ticketType: currentTicketType,
                    amount: payload.apply_amount,
                    category: payload.expense_category,
                    reason: payload.reason,
                    invoiceNumber: payload.invoice_number,
                });
                loadMyList();
            })
            .catch(function () {
                showSubmitSuccessModal({ success: false, requestId: rid, message: '请求失败，请重试' });
            });
    }

    /* ── 提交审批结果弹窗（替代浏览器原生 alert）── */
    function showSubmitSuccessModal(opts) {
        var modal = document.getElementById('submitSuccessModal');
        if (!modal) return;

        var isError = !opts.success;
        modal.classList.toggle('is-error', isError);

        var iconEl = document.getElementById('submitSuccessIcon');
        var titleEl = document.getElementById('submitSuccessTitle');
        var hintEl = document.getElementById('submitSuccessHint');
        if (iconEl) { iconEl.textContent = isError ? '❌' : '✅'; }
        if (titleEl) { titleEl.textContent = isError ? '提交失败' : '提交成功'; }
        if (hintEl) {
            hintEl.textContent = isError
                ? '请检查后重试，或联系系统管理员。'
                : '您的报销单已提交，审批人将在审批工作台处理；可前往「我的报销」查看进度。';
        }

        var grid = document.getElementById('submitSuccessGrid');
        if (grid) {
            var items = isError
                ? [
                    { k: '报销单号', v: opts.requestId || '—' },
                    { k: '错误信息', v: opts.message || '未知错误' },
                ]
                : [
                    { k: '报销单号', v: opts.requestId || '—' },
                    { k: '票据类型', v: opts.ticketType || '—' },
                    { k: '申请金额', v: (opts.amount != null && opts.amount !== '') ? ('¥' + Number(opts.amount).toFixed(2)) : '—' },
                    { k: '费用类型', v: opts.category || '—' },
                    { k: '报销事由', v: opts.reason || '—' },
                    { k: '发票号码', v: opts.invoiceNumber || '—' },
                    { k: '当前状态', v: '待审批' },
                ];
            grid.innerHTML = items.map(function (it) {
                return '<div class="info-item"><div class="info-key">' + escHtml(it.k) +
                    '</div><div class="info-value">' + escHtml(it.v) + '</div></div>';
            }).join('');
        }

        submitSuccessModalMode = isError ? 'error' : 'success';
        modal.style.display = 'flex';
    }

    window.closeSubmitSuccessModal = function () {
        var modal = document.getElementById('submitSuccessModal');
        if (modal) { modal.style.display = 'none'; }
        // 仅成功提交后关闭才重置表单并切到「我的报销」查看进度
        if (submitSuccessModalMode === 'success') {
            setSubmitMode('check');
            lastCheckPassed = false;
            isDisabledMode = false;
            lastRequestIds = [];
            hideAutoFields();
            if (typeof window.switchTab === 'function') { window.switchTab('my'); }
        }
    };

    /* ── AI 回写：校验通过后从首个结果回写金额/费用类型/申请日期，并展示此前隐藏的字段 ── */
    function applyAiWriteback(results) {
        if (!results || !results.length) return;
        var r = results[0];
        var ticketType = (r._form && r._form.ticket_type) || '发票';
        var amtEl = document.getElementById('apply_amount');
        var catEl = document.getElementById('expense_category');

        if (ticketType === '行程单') {
            // 行程单：使用智能体回写的金额与费用类型（已写入 DB，并随响应透传）
            var itAmt = r.apply_amount;
            if (amtEl && itAmt != null && itAmt !== '') {
                amtEl.value = itAmt;
                amtEl.classList.add('auto-filled');
            }
            var itCat = r.expense_category;
            if (catEl && itCat) {
                var matchedIt = false;
                for (var k = 0; k < catEl.options.length; k++) {
                    if (catEl.options[k].value === itCat) { catEl.selectedIndex = k; matchedIt = true; break; }
                }
                if (!matchedIt && catEl.options[0]) { catEl.options[0].text = '🤖 ' + itCat; catEl.selectedIndex = 0; }
                catEl.classList.add('auto-filled');
            }
        } else {
            // 发票：从 OCR 字段与分类限额结果回写
            var ocr = r.ocr_result || {};
            var amt = ocr['发票金额'] != null ? ocr['发票金额'] : ocr['价税合计_小写'];
            if (amtEl && amt != null && amt !== '') {
                amtEl.value = amt;
                amtEl.classList.add('auto-filled');
            }
            var cls = r.classify_result || {};
            var cat = cls['费用分类'];
            if (catEl && cat) {
                var matched = false;
                for (var i = 0; i < catEl.options.length; i++) {
                    if (catEl.options[i].value === cat) { catEl.selectedIndex = i; matched = true; break; }
                }
                if (!matched && catEl.options[0]) { catEl.options[0].text = '🤖 ' + cat; catEl.selectedIndex = 0; }
                catEl.classList.add('auto-filled');
            }
            // 发票号码：从 OCR 回写并标记 AI 徽标
            var invNo = ocr['发票号码'];
            var invNoEl = document.getElementById('invoice_number');
            if (invNoEl && invNo != null && invNo !== '') {
                invNoEl.value = invNo;
                invNoEl.classList.add('auto-filled');
                var invBadge = document.getElementById('badge_invoice_number');
                if (invBadge) { invBadge.textContent = '🤖 AI 回写'; invBadge.className = 'ai-writeback-badge'; }
            }
        }

        // 开票日期：从 OCR 回写（启用态 AI 识别），并标记 AI 徽标
        var ocrDate = r.ocr_result || {};
        var invDate = ocrDate['开票日期'];
        var invDateEl = document.getElementById('invoice_date');
        if (invDateEl && invDate != null && invDate !== '') {
            invDateEl.value = invDate;
            invDateEl.classList.add('auto-filled');
            var invDateBadge = document.getElementById('badge_invoice_date');
            if (invDateBadge) { invDateBadge.textContent = '🤖 AI 回写'; invDateBadge.className = 'ai-writeback-badge'; }
        }

        // 申请日期：空则填系统日期
        var dateEl = document.getElementById('apply_date');
        if (dateEl && !dateEl.value) {
            var today = new Date();
            var yyyy = today.getFullYear();
            var mm = String(today.getMonth() + 1).padStart(2, '0');
            var dd = String(today.getDate()).padStart(2, '0');
            dateEl.value = yyyy + '-' + mm + '-' + dd;
            dateEl.classList.add('auto-filled');
        }
        // 回写完成后展示此前隐藏的 autoFields 区域（带动画）
        var af = document.getElementById('autoFields');
        if (af) {
            af.style.display = '';
            af.classList.add('auto-fields-visible');
        }
    }

    /* ── 停用态：展开人工填写字段、徽标置「✍️ 人工填写」、主按钮置「提交审批」 ── */
    function enableManualMode() {
        setSubmitMode('approve');
        // 展开字段区
        var af = document.getElementById('autoFields');
        if (af) {
            af.style.display = '';
            af.classList.add('auto-fields-visible');
        }
        // 金额 / 申请日期 / 费用类型 徽标切换为「人工填写」
        setBadge('badge_apply_amount', '✍️ 人工填写', 'field-badge manual-badge');
        setBadge('badge_apply_date', '✍️ 人工填写', 'field-badge manual-badge');
        setBadge('badge_expense_category', '✍️ 人工填写', 'field-badge manual-badge');
        setBadge('badge_invoice_date', '✍️ 人工填写', 'field-badge manual-badge');
        // 提示文案切换为停用态说明
        var note = document.getElementById('autoFieldsNote');
        if (note) {
            // 与停用弹窗保持一致：统一引用后端返回的统一停用说明
            note.textContent = lastDisabledSummary || DISABLED_MSG_FALLBACK;
        }
    }

    /* ── 设置某个徽标的文案与样式 ── */
    function setBadge(id, text, cls) {
        var el = document.getElementById(id);
        if (!el) return;
        el.textContent = text;
        el.className = cls;
    }

    /* ── 重置所有徽标 / 提示文案为初始（AI 回写）状态 ── */
    function resetBadges() {
        setBadge('badge_apply_amount', '🤖 AI 回写·请核对', 'ai-writeback-badge');
        setBadge('badge_apply_date', '📅 系统日期', 'ai-writeback-badge');
        setBadge('badge_expense_category', '🤖 AI 识别', 'ai-writeback-badge');
        setBadge('badge_invoice_number', '✍️ 人工填写', 'field-badge manual-badge');
        setBadge('badge_invoice_date', '✍️ 人工填写', 'field-badge manual-badge');
        var note = document.getElementById('autoFieldsNote');
        if (note) {
            note.textContent = '⚠️ 以上「金额 / 申请日期 / 费用类型」由 AI 在提交校验后自动回写，请人工核对确认后提交审批；报销事由与发票号码请人工填写。';
        }
    }

    /* ── 隐藏 autoFields 并清除回写数据（切换票据类型 / 重置时调用）── */
    function hideAutoFields() {
        var af = document.getElementById('autoFields');
        if (af) {
            af.style.display = 'none';
            af.classList.remove('auto-fields-visible');
        }
        // 清除回写值并移除高亮
        ['apply_amount', 'apply_date', 'expense_category', 'invoice_number', 'invoice_date'].forEach(function(id) {
            var el = document.getElementById(id);
            if (el) { el.value = ''; el.classList.remove('auto-filled', 'ai-writeback'); }
        });
        resetBadges();
    }

    function runCheck() {
        // 表单校验：仅需选择票据类型并上传文件；金额/日期/事由/费用类型由 AI 回写，提交审批时落库
        if (!currentTicketType) {
            alert('请先选择票据类型');
            return;
        }
        if (selectedFiles.length === 0) {
            alert(currentTicketType === '行程单' ? '请先选择行程单文件' : '请先选择发票文件');
            return;
        }

        // 按钮加载态
        submitBtn.disabled = true;
        submitBtn.querySelector('.btn-text').style.display = 'none';
        submitBtn.querySelector('.btn-loading').style.display = 'flex';

        pipelineStarted = false;
        var dsDisabled = false;
        var resolvedResults = null;

        // 并行：状态探测（DeepSeek 是否停用）+ 上传校验
        var statusPromise = fetch('/api/deepseek/status')
            .then(function (r) { return r.json(); })
            .catch(function () { return { enabled: true }; });

        var uploadPromise = Promise.all(selectedFiles.map(function (file) {
            var formData = new FormData(uploadForm);
            formData.set('file', file);
            var csrfMeta = document.querySelector('meta[name="csrf-token"]');
            var csrfToken = csrfMeta ? csrfMeta.content : '';
            return fetch('/upload', { method: 'POST', body: formData, headers: { 'X-CSRF-Token': csrfToken } })
                .then(function (resp) {
                    return resp.json().then(function (d) {
                        if (!resp.ok) { throw new Error(d.summary || '请求失败'); }
                        return d;
                    });
                });
        }));

        // 根据最终结论展示结果：停用 → 不显示流水线动画；正常 → 走动画 + 结果
        function present(results) {
            if (dsDisabled) {
                showDisabledModal(results);
            } else {
                finishPipeline();
                setTimeout(function () { showPipelineResult(results); }, 400);
            }
        }

        // 状态探测先返回：停用时不再启动流水线动画
        statusPromise.then(function (cfg) {
            dsDisabled = !cfg.enabled;
            if (!dsDisabled) {
                startPipeline(currentTicketType);
            }
            if (resolvedResults) { present(resolvedResults); }
        });

        uploadPromise
            .then(function (results) {
                resolvedResults = results;
                if (dsDisabled || pipelineStarted) { present(results); }
                // 否则等待 statusPromise 回调启动动画后再 present
            })
            .catch(function (err) {
                resolvedResults = [{ status: '错误', summary: err.message || '请求失败，请重试' }];
                if (dsDisabled || pipelineStarted) { present(resolvedResults); }
            })
            .finally(function () {
                submitBtn.disabled = false;
                submitBtn.querySelector('.btn-text').style.display = 'inline';
                submitBtn.querySelector('.btn-loading').style.display = 'none';
            });
    }

    /* ── DeepSeek 停用时的替代弹窗：不显示流水线动画，仅说明原因 ── */
    function showDisabledModal(results) {
        var modal = document.getElementById('pipelineModal');
        if (!modal) return;

        // 标记为停用态：关闭弹窗后展开人工填写字段
        isDisabledMode = true;
        lastRequestIds = (results || []).map(function (r) { return r._request_id; }).filter(Boolean);

        // 隐藏流水线动画，重置标题/智能体徽标
        if (pipelineEl) { pipelineEl.style.display = 'none'; }
        if (pipelineTitleEl) { pipelineTitleEl.textContent = 'AI 校验不可用'; }
        if (pipelineAgentIconEl) { pipelineAgentIconEl.textContent = '⚠️'; }
        if (pipelineAgentBadgeEl) { pipelineAgentBadgeEl.style.display = 'none'; }

        var summary = DISABLED_MSG_FALLBACK;
        if (results && results.length && results[0].summary) {
            // 优先采用后端返回的原始说明（统一来源：config.DEEPSEEK_DISABLED_MSG）
            summary = String(results[0].summary).replace(/^OCR 提取失败:\s*/, '');
        }
        lastDisabledSummary = summary;

        var rs = document.getElementById('pipelineResultStatus');
        if (rs) {
            rs.className = 'result-status warning';
            rs.style.display = 'flex';
            document.getElementById('pipelineStatusIcon').textContent = '⚠️';
            document.getElementById('pipelineStatusLabel').textContent = 'AI 校验已停用';
            document.getElementById('pipelineStatusSummary').textContent = summary;
        }

        modal.style.display = 'flex';
        var body = modal.querySelector('.modal-body');
        if (body) { body.scrollTop = 0; }
    }

    /* ========================================
       弹窗内显示校验结果（状态 + 摘要，无按钮）
       ======================================== */
    function showPipelineResult(results) {
        // 汇总状态：取最严重
        var worstStatus = '通过';
        var priority = { '通过': 0, '预警': 1, '拦截': 2, '错误': 3 };
        results.forEach(function (r) {
            var s = r.status || '错误';
            if (priority[s] > priority[worstStatus]) { worstStatus = s; }
        });

        // #fail 调试开关：强制拦截
        if (window.location.hash === '#fail') { worstStatus = '拦截'; }

        var meta = STATUS_MAP[worstStatus] || { icon: '❌', label: worstStatus, cls: 'error' };

        // 拼接摘要
        var summaryText = results.map(function (r) {
            return r.summary || (STATUS_MAP[r.status] ? STATUS_MAP[r.status].label : r.status) || '';
        }).filter(Boolean).join('；') || meta.label;

        lastCheckPassed = (worstStatus === '通过' || worstStatus === '预警');
        lastRequestIds = results.map(function (r) { return r._request_id; }).filter(Boolean);

        // 校验通过 / 预警 → AI 回写金额/费用类型/申请日期，并展开字段表单
        if (lastCheckPassed) { applyAiWriteback(results); }

        var rs = document.getElementById('pipelineResultStatus');
        if (rs) {
            rs.className = 'result-status ' + meta.cls;
            rs.style.display = 'flex';
            document.getElementById('pipelineStatusIcon').textContent = meta.icon;
            document.getElementById('pipelineStatusLabel').textContent = meta.label;
            document.getElementById('pipelineStatusSummary').textContent = summaryText;
        }
        // 滚动弹窗内容到底部
        var body = document.querySelector('#pipelineModal .modal-body');
        if (body) { body.scrollTop = body.scrollHeight; }
    }

    /* ========================================
       结果渲染函数 (支持首页 AJAX 和独立结果页)
       ======================================== */
    window.renderResult = function (data) {
        var banner = document.getElementById('statusBanner');
        if (banner) {
            fillResultPage(data);
            return;
        }

        if (!resultContainer) return;
        resultContainer.style.display = 'block';
        resultContainer.innerHTML = buildSingleResult(data);
    };

    function renderMultiResult(results) {
        if (!resultContainer) return;
        resultContainer.style.display = 'block';

        // 汇总状态：取最严重的
        var worstStatus = '通过';
        var priority = { '通过': 0, '预警': 1, '拦截': 2, '错误': 3 };
        results.forEach(function (r) {
            if (priority[r.status] > priority[worstStatus]) {
                worstStatus = r.status;
            }
        });

        var meta = STATUS_MAP[worstStatus] || { icon: '❌', label: worstStatus, cls: 'error' };
        var html = '<div class="result-status ' + meta.cls + '">' +
            '<div class="status-icon">' + meta.icon + '</div>' +
            '<div>' +
            '<div class="status-label">' + meta.label + '（' + results.length + ' 个文件）</div>' +
            '<div class="status-summary">共处理 ' + results.length + ' 个文件，汇总状态: ' + worstStatus + '</div>' +
            '</div>' +
            '</div>';

        results.forEach(function (data, idx) {
            var fileLabel = data._form && data._form.filename ? data._form.filename : ('文件 ' + (idx + 1));
            html += '<div class="card multi-file-card">' +
                '<div class="card-header">' +
                '<span class="card-icon">📄</span>' +
                '<h3>' + escHtml(fileLabel) + '</h3>' +
                '<span class="file-status-badge ' + (STATUS_MAP[data.status] || {}).cls + '">' +
                (data.status || '未知') +
                '</span>' +
                '</div>' +
                buildResultContent(data) +
                '</div>';
        });

        var multiBtnText = worstStatus === '通过' ? '提交审批' : '人工审核';
        html += '<div class="action-bar"><button type="button" class="btn-secondary" onclick="loadMyList()">查看我的报销</button></div>';
        resultContainer.innerHTML = html;
    }

    function buildSingleResult(data) {
        var status = data.status || '错误';
        var meta = STATUS_MAP[status] || { icon: '❌', label: status, cls: 'error' };

        var html = '<div class="result-status ' + meta.cls + '">' +
            '<div class="status-icon">' + meta.icon + '</div>' +
            '<div>' +
            '<div class="status-label">' + meta.label + '</div>' +
            '<div class="status-summary">' + escHtml(data.summary || '') + '</div>' +
            '</div>' +
            '</div>';

        html += buildResultContent(data);
        html += '<div class="action-bar"><button type="button" class="btn-secondary" onclick="loadMyList()">查看我的报销</button></div>';
        return html;
    }

    function buildResultContent(data) {
        var html = '';
        var ticketType = (data._form && data._form.ticket_type) ? data._form.ticket_type : '发票';

        // 表单信息
        if (data._form) {
            html += '<div class="card"><div class="card-header"><span class="card-icon">📋</span><h3>报销申请信息</h3></div><div class="info-grid">' +
                buildInfoItems(data._form) +
                '</div></div>';
        }

        // OCR 结果
        if (data.ocr_result && !data.ocr_result._error) {
            if (ticketType === '行程单') {
                html += buildItineraryOcrCard(data.ocr_result);
            } else {
                html += buildOcrCard(data.ocr_result);
            }
        } else if (data.ocr_result && data.ocr_result._error) {
            html += '<div class="card"><div class="card-header"><span class="card-icon">🔍</span><h3>OCR 提取结果</h3></div>' +
                '<div class="error-msg">OCR 提取失败: ' + escHtml(data.ocr_result._error) + '</div></div>';
        }

        // 异常检测
        if (data.anomaly_result) {
            html += buildAnomalyCard(data.anomaly_result, ticketType);
        }

        // 分类限额（仅发票）
        if (data.classify_result) {
            html += buildClassifyCard(data.classify_result);
        }

        // 行程单合理性校验
        if (data.itinerary_result) {
            html += buildItineraryVerifyCard(data.itinerary_result);
        }

        // 错误兜底
        if (data.status === '错误' && !data.ocr_result && !data.anomaly_result) {
            html += '<div class="card"><div class="card-header"><span class="card-icon">❌</span><h3>错误信息</h3></div>' +
                '<div class="error-msg">' + escHtml(data.summary || '未知错误') + '</div></div>';
        }

        return html;
    }

    /* ========================================
       独立结果页填充
       ======================================== */
    function fillResultPage(data) {
        var status = data.status || '错误';
        var meta = STATUS_MAP[status] || { icon: '❌', label: status, cls: 'error' };

        var banner = document.getElementById('statusBanner');
        banner.className = 'result-status ' + meta.cls;
        document.getElementById('statusIcon').textContent = meta.icon;
        document.getElementById('statusLabel').textContent = meta.label;
        document.getElementById('statusSummary').textContent = data.summary || '';

        // 表单信息
        if (data._form) {
            var fc = document.getElementById('formCard');
            fc.style.display = 'block';
            document.getElementById('formInfo').innerHTML = buildInfoItems(data._form);
        }

        // OCR
        if (data.ocr_result && !data.ocr_result._error) {
            fillOcr(data.ocr_result);
        } else if (data.ocr_result && data.ocr_result._error) {
            var ocrCard = document.getElementById('ocrCard');
            ocrCard.style.display = 'block';
            document.getElementById('ocrTable').outerHTML = '<div class="error-msg">OCR 提取失败: ' + escHtml(data.ocr_result._error) + '</div>';
        }

        // 异常检测
        if (data.anomaly_result) {
            fillAnomaly(data.anomaly_result);
        }

        // 分类限额
        if (data.classify_result) {
            fillClassify(data.classify_result);
        }

        // 错误兜底
        if (status === '错误' && !data.ocr_result && !data.anomaly_result) {
            document.getElementById('errorCard').style.display = 'block';
            document.getElementById('errorMsg').textContent = data.summary || '未知错误';
        }

        // 动态设置底部按钮文案：校验通过 → 提交审批，否则 → 人工审核
        var actionBtn = document.getElementById('actionBtn');
        if (actionBtn) {
            actionBtn.textContent = status === '通过' ? '提交审批' : '人工审核';
        }
    }

    /* ── OCR ── */
    function fillOcr(ocr) {
        var card = document.getElementById('ocrCard');
        card.style.display = 'block';
        document.getElementById('ocrRaw').textContent = JSON.stringify(ocr, null, 2);
        var tbody = '';
        OCR_FIELDS.forEach(function (key) {
            if (key in ocr) {
                tbody += '<tr><td class="key-col">' + key + '</td><td>' + escHtml(formatVal(ocr[key])) + '</td></tr>';
            }
        });
        // 商品明细（如果有）
        if (ocr['商品明细'] && Array.isArray(ocr['商品明细']) && ocr['商品明细'].length > 0) {
            tbody += '<tr><td class="key-col">商品明细</td><td>' + escHtml(JSON.stringify(ocr['商品明细'], null, 2)).replace(/\n/g, '<br>') + '</td></tr>';
        }
        document.getElementById('ocrTable').innerHTML = '<table class="data-table">' + tbody + '</table>';
    }

    function buildOcrCard(ocr) {
        var rows = '';
        OCR_FIELDS.forEach(function (key) {
            if (key in ocr) {
                rows += '<tr><td class="key-col">' + key + '</td><td>' + escHtml(formatVal(ocr[key])) + '</td></tr>';
            }
        });
        if (ocr['商品明细'] && Array.isArray(ocr['商品明细']) && ocr['商品明细'].length > 0) {
            rows += '<tr><td class="key-col">商品明细</td><td>' + escHtml(JSON.stringify(ocr['商品明细'], null, 2)).replace(/\n/g, '<br>') + '</td></tr>';
        }
        var raw = JSON.stringify(ocr, null, 2);
        return '<div class="card"><div class="card-header"><span class="card-icon">🔍</span><h3>OCR 提取结果</h3></div>' +
            '<div class="table-wrap"><table class="data-table">' + rows + '</table></div>' +
            '<details class="raw-details"><summary>查看完整 JSON</summary><pre class="raw-json">' + escHtml(raw) + '</pre></details></div>';
    }

    /* ── 异常检测 ── */
    function fillAnomaly(anomaly) {
        var card = document.getElementById('anomalyCard');
        card.style.display = 'block';
        document.getElementById('anomalyRaw').textContent = JSON.stringify(anomaly, null, 2);

        var conc = anomaly['总体结论'] || '—';
        var concEl = document.getElementById('anomalyConclusion');
        concEl.textContent = '总体结论: ' + conc;
        concEl.className = 'anomaly-conclusion ' + (conc === '拦截' ? 'block' : conc === '预警' ? 'warning' : 'pass');

        var details = anomaly['异常明细'];
        var html = '';
        if (details && Array.isArray(details) && details.length > 0) {
            details.forEach(function (item) {
                var severity = item['严重程度'] || '提示';
                var cls = severity === '严重' ? 'severe' : severity === '警告' ? 'warn' : 'info';
                html += '<div class="anomaly-item ' + cls + '">' +
                    '<span class="anomaly-type">' + escHtml(item['异常类型'] || '') + '</span>' +
                    '<span class="anomaly-desc">' + escHtml(item['异常描述'] || '') + '</span>' +
                    '<span class="anomaly-severity ' + cls + '">' + severity + '</span>' +
                    '</div>';
            });
        } else {
            html = '<p style="color:var(--green);font-weight:600;padding:8px 0;">✓ 无异常项</p>';
        }
        document.getElementById('anomalyDetails').innerHTML = html;
    }

    function buildAnomalyCard(anomaly, ticketType) {
        var conc = anomaly['总体结论'] || '—';
        var raw = JSON.stringify(anomaly, null, 2);
        var title = (ticketType === '行程单') ? '行程单异常检测' : '异常检测结果';
        var icon = (ticketType === '行程单') ? '🛡️' : '⚠️';
        var html = '<div class="card ' + (ticketType === '行程单' ? 'itinerary-card' : '') + '"><div class="card-header"><span class="card-icon">' + icon + '</span><h3>' + title + '</h3></div>' +
            '<div class="anomaly-conclusion ' + (conc === '拦截' ? 'block' : conc === '预警' ? 'warning' : 'pass') + '">总体结论: ' + conc + '</div>';

        var details = anomaly['异常明细'];
        if (details && Array.isArray(details) && details.length > 0) {
            details.forEach(function (item) {
                var severity = item['严重程度'] || '提示';
                var cls = severity === '严重' ? 'severe' : severity === '警告' ? 'warn' : 'info';
                html += '<div class="anomaly-item ' + cls + '">' +
                    '<span class="anomaly-type">' + escHtml(item['异常类型'] || '') + '</span>' +
                    '<span class="anomaly-desc">' + escHtml(item['异常描述'] || '') + '</span>' +
                    '<span class="anomaly-severity ' + cls + '">' + severity + '</span>' +
                    '</div>';
            });
        } else {
            html += '<p style="color:var(--green);font-weight:600;padding:8px 0;">✓ 无异常项</p>';
        }

        html += '<details class="raw-details"><summary>查看完整 JSON</summary><pre class="raw-json">' + escHtml(raw) + '</pre></details></div>';
        return html;
    }

    /* ── 行程单 OCR 卡片：汇总字段 + 行程明细表 ── */
    function buildItineraryOcrCard(ocr) {
        var rows = '';
        ITINERARY_SUMMARY_FIELDS.forEach(function (key) {
            if (key in ocr) {
                rows += '<tr><td class="key-col">' + key + '</td><td>' + escHtml(formatVal(ocr[key])) + '</td></tr>';
            }
        });

        var details = ocr['行程详情'];
        var detailHtml = '';
        if (details && Array.isArray(details) && details.length > 0) {
            var head = '';
            ITINERARY_DETAIL_COLS.forEach(function (col) {
                head += '<th>' + col.label + '</th>';
            });
            var body = '';
            details.forEach(function (item) {
                body += '<tr>';
                ITINERARY_DETAIL_COLS.forEach(function (col) {
                    body += '<td>' + escHtml(formatVal(item[col.key])) + '</td>';
                });
                body += '</tr>';
            });
            detailHtml = '<div class="table-wrap" style="margin-top:16px;">' +
                '<table class="data-table itinerary-table">' + head + body + '</table></div>';
        }

        var raw = JSON.stringify(ocr, null, 2);
        return '<div class="card itinerary-card"><div class="card-header"><span class="card-icon">🚕</span><h3>行程单提取结果</h3>' +
            '<span class="pipeline-agent-badge itinerary">行程单 Agent</span></div>' +
            '<div class="table-wrap"><table class="data-table">' + rows + '</table></div>' +
            detailHtml +
            '<details class="raw-details"><summary>查看完整 JSON</summary><pre class="raw-json">' + escHtml(raw) + '</pre></details></div>';
    }

    /* ── 行程单合理性校验卡片 ── */
    function buildItineraryVerifyCard(verify) {
        var conc = verify['校验结论'] || '—';
        var concCls = conc === '拦截' ? 'block' : conc === '预警' ? 'warning' : 'pass';

        // 关键信息网格
        var gridItems = [
            { key: '总金额校验', val: verify['总金额校验'] },
            { key: '行程天数', val: verify['行程天数'] + ' 天' },
            { key: '单笔最高金额', val: verify['单笔最高金额'] },
            { key: '日期合理性', val: verify['日期合理性'] },
            { key: '行程连续性', val: verify['行程连续性'] },
        ];
        var gridHtml = '';
        gridItems.forEach(function (it) {
            gridHtml += '<div class="info-item"><div class="info-key">' + it.key + '</div><div class="info-value">' + escHtml(it.val || '—') + '</div></div>';
        });

        // 校验明细表
        var details = verify['校验明细'];
        var detailHtml = '';
        if (details && Array.isArray(details) && details.length > 0) {
            var head = '<tr><th>校验项目</th><th>校验结果</th><th>说明</th></tr>';
            var body = '';
            details.forEach(function (item) {
                var r = item['校验结果'] || '通过';
                var rCls = r === '拦截' ? 'block' : r === '预警' ? 'warning' : 'pass';
                body += '<tr><td class="key-col">' + escHtml(item['校验项目'] || '') + '</td>' +
                    '<td><span class="verify-badge ' + rCls + '">' + r + '</span></td>' +
                    '<td>' + escHtml(item['说明'] || '') + '</td></tr>';
            });
            detailHtml = '<div class="table-wrap" style="margin-top:16px;">' +
                '<table class="data-table">' + head + body + '</table></div>';
        }

        var raw = JSON.stringify(verify, null, 2);
        return '<div class="card itinerary-card"><div class="card-header"><span class="card-icon">✅</span><h3>行程合理性校验</h3>' +
            '<span class="pipeline-agent-badge itinerary">合理性校验</span></div>' +
            '<div class="anomaly-conclusion ' + concCls + '">校验结论: ' + conc + '</div>' +
            '<div class="info-grid">' + gridHtml + '</div>' +
            detailHtml +
            '<details class="raw-details"><summary>查看完整 JSON</summary><pre class="raw-json">' + escHtml(raw) + '</pre></details></div>';
    }

    /* ── 分类限额 ── */
    function fillClassify(classify) {
        var card = document.getElementById('classifyCard');
        card.style.display = 'block';
        document.getElementById('classifyRaw').textContent = JSON.stringify(classify, null, 2);
        document.getElementById('classifyInfo').innerHTML = buildInfoItems(classify);
    }

    function buildClassifyCard(classify) {
        var raw = JSON.stringify(classify, null, 2);
        return '<div class="card"><div class="card-header"><span class="card-icon">💰</span><h3>分类与限额校验</h3></div>' +
            '<div class="info-grid">' + buildInfoItems(classify) + '</div>' +
            '<details class="raw-details"><summary>查看完整 JSON</summary><pre class="raw-json">' + escHtml(raw) + '</pre></details></div>';
    }

    /* ── 通用辅助 ── */
    function buildInfoItems(obj) {
        var html = '';
        for (var key in obj) {
            if (key === '_form' || key === '异常明细' || key === '_error' || key === '_warning' || key === '_raw') continue;
            html += '<div class="info-item"><div class="info-key">' + key + '</div><div class="info-value">' + escHtml(formatVal(obj[key])) + '</div></div>';
        }
        return html;
    }

    function formatVal(val) {
        if (val === null || val === undefined) return '—';
        if (typeof val === 'boolean') return val ? '是' : '否';
        return String(val);
    }

    function escHtml(str) {
        if (str === null || str === undefined) return '';
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    /* ========================================
       "我的报销"：加载当前用户提交的报销单与进度
       ======================================== */
    function loadMyList() {
        var box = document.getElementById('myList');
        if (!box) return;
        fetch('/api/my').then(function (r) { return r.json(); }).then(function (data) {
            if (data.error) { box.innerHTML = ''; return; }
            var items = data.items || [];
            var cnt = document.getElementById('myCount');
            if (cnt) cnt.textContent = items.length;
            if (!items.length) {
                box.innerHTML = '<div class="empty-state"><div class="empty-icon">📭</div><div class="empty-text">暂无报销单，提交后将显示在此</div></div>';
                return;
            }
            box.innerHTML = items.map(function (it) {
                var wsText = {
                    '待审批': '⏳ 待审批', '审批中': '🔄 审批中', '待复核': '✓ 待复核',
                    '已驳回': '✕ 已驳回', '已复核并归档': '📦 已复核并归档', '已打款': '💰 已打款',
                }[it.workflow_status] || it.workflow_status;
                var wsCls = {
                    '待审批': 'status-pending', '审批中': 'status-inreview', '待复核': 'status-paid',
                    '已驳回': 'status-rejected', '已复核并归档': 'status-archived', '已打款': 'status-paid',
                }[it.workflow_status] || '';
                var ticketCls = (it.ticket_type === '行程单') ? 'itinerary' : 'invoice';
                var ticketIcon = (it.ticket_type === '行程单') ? '🚕' : '🧾';
                var ticketText = (it.ticket_type === '行程单') ? '行程单' : '发票';
                var rid = escHtml(it.request_id);
                return '<div class="my-item">' +
                    '<div class="my-item-head">' +
                        '<span class="my-id">报销单号: ' + rid + '</span>' +
                        '<span class="my-amount">' + money(it.apply_amount) + '</span>' +
                    '</div>' +
                    '<div class="my-reason">' + escHtml(it.reason || '—') + '</div>' +
                    '<div class="my-meta">' +
                        '<span class="my-meta-item"><span class="meta-key">提交时间</span><span class="meta-value">' + escHtml(fmtTime(it.created_at)) + '</span></span>' +
                        '<span class="my-meta-item"><span class="meta-key">费用类型</span><span class="meta-value">' + escHtml(it.expense_category || '—') + '</span></span>' +
                    '</div>' +
                    '<div class="my-item-footer">' +
                        '<div class="my-tags">' +
                            '<span class="tag ' + ticketCls + '">' + ticketIcon + ' ' + ticketText + '</span>' +
                            '<span class="tag ' + wsCls + '">' + wsText + '</span>' +
                        '</div>' +
                        '<button type="button" class="btn-detail" onclick="viewDetail(\'' + rid + '\')">📄 查看明细</button>' +
                    '</div>' +
                '</div>';
            }).join('');
        }).catch(function () { box.innerHTML = ''; });
    }

    function money(v) {
        if (v == null) return '—';
        return '¥' + Number(v).toFixed(2);
    }

    /* 将 ISO 时间转为 YYYY-MM-DD HH:MM:SS（失败则原样返回） */
    function fmtTime(iso) {
        if (!iso) return '—';
        // 兼容 "2026-07-22T12:11:55" 或 "2026-07-22 12:11:55" 或带时区
        var s = String(iso).replace('T', ' ').replace(/\.\d+.*$/, '');
        var parts = s.split(' ');
        if (parts[0] && parts[0].length === 10 && !parts[1]) { return s + ' 00:00:00'; }
        if (parts[1] && parts[1].length === 8) { return s; }
        if (parts[1] && parts[1].length >= 5) { return parts[0] + ' ' + parts[1].slice(0, 8); }
        return s;
    }

    /* 仅取日期部分 YYYY-MM-DD（失败则原样返回），用于开票日期等纯日期字段 */
    function fmtDate(iso) {
        if (!iso) return '—';
        var s = String(iso).replace('T', ' ').replace(/\.\d+.*$/, '');
        var d = s.split(' ')[0];
        return d || s;
    }

    /* 仅保留年月日（YYYY-MM-DD），用于申请日期等无需具体时间的字段 */
    function fmtDate(v) {
        if (!v) return '—';
        return String(v).replace('T', ' ').split(' ')[0] || '—';
    }

    /* ── 查看报销单明细弹窗 ── */
    window.viewDetail = function (requestId) {
        var modal = document.getElementById('myDetailModal');
        var body = document.getElementById('myDetailBody');
        if (!modal || !body) return;
        body.innerHTML = '<div class="detail-loading">加载中…</div>';
        modal.style.display = 'flex';

        fetch('/api/reimbursement/' + encodeURIComponent(requestId)).then(function (r) {
            return r.json().then(function (d) { return { ok: r.ok, d: d }; });
        }).then(function (res) {
            if (!res.ok || res.d.error) {
                body.innerHTML = '<div class="error-msg">加载失败：' + escHtml(res.d.error || '未知错误') + '</div>';
                return;
            }
            // 按票据类型切换明细弹窗标题图标（行程单显示行程单图标）
            var titleIcon = document.getElementById('myDetailTitleIcon');
            if (titleIcon) {
                titleIcon.textContent = (res.d.ticket_type === '行程单') ? '🚕' : '📄';
            }
            body.innerHTML = renderMyDetail(res.d, requestId);
            body.scrollTop = 0;
        }).catch(function () {
            body.innerHTML = '<div class="error-msg">请求失败，请重试</div>';
        });
    };

    function renderMyDetail(d, requestId) {
        var html = '';

        // 基本信息
        var basic = [
            { k: '报销单号', v: d.request_id || requestId },
            { k: '申请金额', v: money(d.apply_amount) },
            { k: '报销事由', v: d.reason || '—' },
            { k: '费用类型', v: d.expense_category || '—' },
            { k: '申请日期', v: fmtDate(d.apply_date) },
            { k: '当前状态', v: d.workflow_status || '—' },
            { k: '提交时间', v: fmtTime(d.created_at) },
        ];
        html += '<div class="detail-section">' +
            '<div class="detail-section-title"><span class="ds-icon">📋</span>基本信息</div>' +
            '<div class="info-grid">' + basic.map(function (it) {
                return '<div class="info-item"><div class="info-key">' + escHtml(it.k) +
                    '</div><div class="info-value">' + escHtml(it.v) + '</div></div>';
            }).join('') + '</div></div>';

        // 审批记录
        var records = d.approval_records || [];
        html += '<div class="detail-section"><div class="detail-section-title"><span class="ds-icon">📝</span>审批记录</div>';
        if (records.length) {
            html += '<div class="approval-timeline">';
            records.forEach(function (rec) {
                var action = rec.action || '—';
                var actCls = action.indexOf('通过') >= 0 ? 'pass' : action.indexOf('驳回') >= 0 ? 'reject' : action.indexOf('转审') >= 0 ? 'transfer' : '';
                var dotCls = actCls || '';
                html += '<div class="approval-node">' +
                    '<div class="approval-dot ' + dotCls + '"></div>' +
                    '<div class="approval-body">' +
                        '<div class="approval-node-head">' +
                            '<strong>' + escHtml(rec.approver_name || rec.approver_id || '—') + '</strong>' +
                            '<span class="approval-action ' + actCls + '">' + escHtml(action) + '</span>' +
                            (rec.approval_node ? '<span class="approval-node-meta">节点：' + escHtml(rec.approval_node) + '</span>' : '') +
                        '</div>' +
                        '<div class="approval-node-meta">时间：' + escHtml(fmtTime(rec.action_time)) + '</div>' +
                        (rec.comment ? '<div class="approval-comment">意见：' + escHtml(rec.comment) + '</div>' : '') +
                    '</div>' +
                '</div>';
            });
            html += '</div>';
        } else {
            html += '<div class="empty-hint">暂无审批记录</div>';
        }
        html += '</div>';

        // 发票列表
        var invoices = d.invoices || [];
        html += '<div class="detail-section"><div class="detail-section-title"><span class="ds-icon">🧾</span>发票列表</div>';
        if (invoices.length) {
            html += '<div class="table-wrap"><table class="data-table"><thead><tr>' +
                '<th>发票号码</th><th>类型</th><th>金额(元)</th><th>销售方</th><th>开票日期</th>' +
                '</tr></thead><tbody>';
            invoices.forEach(function (inv) {
                var invNo = inv.invoice_number;
                var noCell = invNo ? escHtml(invNo) : '缺失，请补上发票';
                var amtCell = invNo && inv.invoice_amount != null ? money(inv.invoice_amount) : '—';
                html += '<tr>' +
                    '<td>' + noCell + '</td>' +
                    '<td>' + escHtml(inv.invoice_type || '—') + '</td>' +
                    '<td>' + escHtml(amtCell) + '</td>' +
                    '<td>' + escHtml(inv.seller_name || '—') + '</td>' +
                    '<td>' + escHtml(fmtDate(inv.invoice_date)) + '</td>' +
                '</tr>';
            });
            html += '</tbody></table></div>';
        } else {
            html += '<div class="empty-hint">暂无可展示的发票（行程单类报销单无发票明细）</div>';
        }
        html += '</div>';

        return html;
    }

    window.closeMyDetailModal = function () {
        var modal = document.getElementById('myDetailModal');
        if (modal) { modal.style.display = 'none'; }
    };

    /* 点击遮罩层关闭我的报销明细弹窗 */
    var myDetailMask = document.getElementById('myDetailModal');
    if (myDetailMask) {
        myDetailMask.addEventListener('click', function (e) {
            if (e.target === myDetailMask) { window.closeMyDetailModal(); }
        });
    }

    window.loadMyList = loadMyList;
    if (document.getElementById('myList')) { loadMyList(); }

    /* ========================================
       Tab 切换：报销申请 / 我的报销
       ======================================== */
    window.switchTab = function (tabName) {
        document.querySelectorAll('.tab-nav-btn').forEach(function (btn) {
            btn.classList.toggle('active', btn.getAttribute('data-tab') === tabName);
        });
        document.querySelectorAll('.tab-panel').forEach(function (panel) {
            panel.classList.toggle('active', panel.id === 'tab-' + tabName);
        });
        // 切到「我的报销」时刷新最新进度
        if (tabName === 'my') { loadMyList(); }
        window.scrollTo({ top: 0, behavior: 'smooth' });
    };

    /* ========================================
       初始化：独立结果页数据加载
       ======================================== */
    // 检查是否在 result.html 页面，并且 URL hash 有数据
    var bannerCheck = document.getElementById('statusBanner');
    if (bannerCheck && window.location.hash) {
        try {
            var data = JSON.parse(decodeURIComponent(window.location.hash.slice(1)));
            fillResultPage(data);
        } catch (_) { /* ignore */ }
    }

})();
