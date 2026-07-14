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
    var currentTicketType = '发票';

    /* ========================================
       智能体执行流水线（对应后端 LangGraph 节点）
       ======================================== */
    var PIPELINE_STEPS = {
        '发票': [
            { icon: '🤖', name: '票据类型路由', node: 'route_by_ticket_type', tool: '条件边路由', detail: '识别为发票，路由到【发票智能体】' },
            { icon: '🔍', name: 'OCR 提取发票字段', node: 'ocr_node', tool: 'DeepSeek Vision API', detail: '提取发票类型/号码/金额/商品明细等字段' },
            { icon: '⚠️', name: '异常检测', node: 'anomaly_node', tool: '规则引擎', detail: '校验字段完整性、金额逻辑、重复发票等' },
            { icon: '💰', name: '分类限额校验', node: 'classify_node', tool: 'DeepSeek + 限额规则', detail: '识别费用类型并校验是否超限' },
            { icon: '✅', name: '发票查验', node: 'verify_node', tool: 'P1 占位', detail: '发票真伪查验（P1 待接入）' },
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

    function startPipeline(ticketType) {
        if (!pipelineEl) return;
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
        pipelineEl.style.display = 'block';
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
            currentTicketType = ticketTypeSelect.value || '发票';
            if (ticketTypeInput) { ticketTypeInput.value = currentTicketType; }
            // 切换提示文案
            if (uploadHint) {
                if (currentTicketType === '行程单') {
                    uploadHint.textContent = '支持 PDF、JPG、PNG 格式的行程单文件（如滴滴行程单），单文件最大 10MB';
                } else {
                    uploadHint.textContent = '支持 PDF、JPG、PNG 格式，单文件最大 10MB，支持多文件';
                }
            }
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

    if (uploadForm) {
        uploadForm.addEventListener('submit', function (e) {
            e.preventDefault();

            // 表单校验
            if (selectedFiles.length === 0) {
                alert(currentTicketType === '行程单' ? '请先选择行程单文件' : '请先选择发票文件');
                return;
            }
            var amt = document.getElementById('apply_amount').value.trim();
            if (!amt) { alert('请填写申请金额'); return; }
            var dt = document.getElementById('apply_date').value.trim();
            if (!dt) { alert('请选择申请日期'); return; }
            var reason = document.getElementById('reason').value.trim();
            if (!reason) { alert('请填写报销事由'); return; }

            // 按钮加载态
            submitBtn.disabled = true;
            submitBtn.querySelector('.btn-text').style.display = 'none';
            submitBtn.querySelector('.btn-loading').style.display = 'flex';

            // 隐藏之前的结果，启动流水线动画
            if (resultContainer) { resultContainer.style.display = 'none'; resultContainer.innerHTML = ''; }
            startPipeline(currentTicketType);

            // 多文件：每个文件单独发送请求
            var promises = selectedFiles.map(function (file) {
                var formData = new FormData(uploadForm);
                formData.set('file', file);
                return fetch('/upload', { method: 'POST', body: formData })
                    .then(function (resp) {
                        return resp.json().then(function (d) {
                            if (!resp.ok) { throw new Error(d.summary || '请求失败'); }
                            return d;
                        });
                    });
            });

            Promise.all(promises)
                .then(function (results) {
                    finishPipeline();
                    setTimeout(function () {
                        if (results.length === 1) {
                            renderResult(results[0]);
                        } else {
                            renderMultiResult(results);
                        }
                        window.scrollTo({ top: resultContainer.offsetTop - 20, behavior: 'smooth' });
                    }, 400);
                })
                .catch(function (err) {
                    finishPipeline();
                    setTimeout(function () {
                        renderResult({ status: '错误', summary: err.message || '请求失败，请重试' });
                    }, 400);
                })
                .finally(function () {
                    submitBtn.disabled = false;
                    submitBtn.querySelector('.btn-text').style.display = 'inline';
                    submitBtn.querySelector('.btn-loading').style.display = 'none';
                });
        });
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
        html += '<div class="action-bar"><a href="/" class="btn-secondary">' + multiBtnText + '</a></div>';
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
        var actionBtnText = status === '通过' ? '提交审批' : '人工审核';
        html += '<div class="action-bar"><a href="/" class="btn-secondary">' + actionBtnText + '</a></div>';
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
            tbody += '<tr><td class="key-col">商品明细</td><td>' + JSON.stringify(ocr['商品明细'], null, 2).replace(/\n/g, '<br>') + '</td></tr>';
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
            rows += '<tr><td class="key-col">商品明细</td><td>' + JSON.stringify(ocr['商品明细'], null, 2).replace(/\n/g, '<br>') + '</td></tr>';
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
       事件委托：点击"提交审批"按钮时显示提示框
       ======================================== */
    document.addEventListener('click', function (e) {
        var btn = e.target.closest('.action-bar .btn-secondary');
        if (btn && btn.textContent.trim() === '提交审批') {
            e.preventDefault();
            alert('提交成功，等待审批');
            window.location.href = '/';
        }
    });

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
