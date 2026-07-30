# OA 报销 AI Agent · 代码评审与发票图片提取适配方案

> 评审范围：聚焦 **OCR / 发票 / 行程单提取主线**（`skill/utils/image_ocr.py`、`skill/utils/pdf_extractor.py`、`skill/tools/tool_ocr_extract.py`、`skill/tools/tool_itinerary_ocr.py`、`skill/orchestrator/nodes/ocr_node.py`、`skill/utils/http_client.py`、配置与部署文档），并对 `skill/` 全量做静态质量扫描。
> 评审时间：2026-07-30（实测环境：macOS / Apple Silicon / miniconda python）

---

## 一、本地 OCR 安装情况（实测结论）

| 项 | 结果 |
| --- | --- |
| Tesseract 二进制 | ✅ 已安装 `5.5.2`，路径 `/opt/miniconda3/bin/tesseract`，可执行 |
| pytesseract | ✅ `0.3.13` 已装 |
| 中文语言包 | ✅ `chi_sim`、`chi_sim_vert`、`chi_tra`、`eng` 均在（共 125 个语言包） |
| PaddleOCR / paddlepaddle | ❌ 未安装（`_paddle_available()` 返回 `False`） |
| `.env` 实际配置 | `LOCAL_OCR_ENGINE=tesseract`、`TESSERACT_CMD=/opt/miniconda3/bin/tesseract`、`TESSERACT_LANG=chi_sim+eng` |
| pymupdf（PDF 文本层/渲染） | ✅ 已装 |

**端到端实测**：用合成的「增值税电子普通发票」样图（含发票代码/号码/价税合计/购销方/开票日期）跑 `image_ocr.extract_image_text()`，Tesseract 准确识别出全部关键字段：

```
增值税电子普通发票
发票代码: 011001900311
票号码: 08826341        # 注：前导「发」字在本次合成字体下漏识（见后文§4-G）
价税合计: ¥ 1280.00
销售方: 北京示例科技有限公司
购买方: 张三
开票日期: 2026-07-28
```

**结论：本机本地 OCR 完全可用且对发票字段识别率高，无需依赖 Vision 兜底即可工作。** 之前命令行 `which tesseract` 报「未找到」是 PATH 问题，`.env` 已用绝对路径 `TESSERACT_CMD` 显式指向二进制，代码内探测逻辑正确。

---

## 二、当前架构（方案 A）概述

设计哲学：**图片/扫描件 PDF → 本地 OCR 抽文本 → DeepSeek Function Call「文本管线」**。DeepSeek 只作为结构化提取的"大脑"，不依赖其原生多模态。

```
发票/行程单文件
 ├─ PDF（有文本层）  → PyMuPDF 提取文本        → DeepSeek Function Call
 ├─ 图片            → 本地 OCR（Tesseract）抽文本 → DeepSeek Function Call
 │                   （本地 OCR 异常 → 降级 DeepSeek Vision）
 └─ 扫描件 PDF（无文本层）→ 渲染页图 → 本地 OCR → DeepSeek Function Call
```

- **发票图片**：`tool_ocr_extract._ocr_extract_image` 优先本地 OCR；异常即降级 Vision（`_ocr_extract_image_by_vision`）；即便 OCR 成功但「发票号码/开票日期/发票金额」缺失，还触发一次 Vision 聚焦重试补全（三级兜底，设计稳健）。
- **PDF**：有文本层走 PyMuPDF；扫描件走本地 OCR。
- **行程单**：`tool_itinerary_ocr` 图片/PDF/扫描件均走本地 OCR 文本管线，**无 Vision 兜底**。

---

## 三、代码质量评审结论

**整体：代码质量高，结构清晰，编译零错误。** `skill/` 42 个 `.py`、6129 行全部 `py_compile` 通过；无裸 `except:`、无 `eval/exec`、无 `subprocess/os.system/shell=True`（无命令注入面）；无遗留查验平台扩展点（发票查验步骤已移除）。

### 发现的问题（按严重程度）

| # | 严重度 | 问题 | 位置 | 影响 |
| --- | --- | --- | --- | --- |
| A | 🔴 高 | **发票有 Vision 三级兜底，行程单与扫描件 PDF 完全没有兜底**。本地 OCR 不可用时，发票仍可工作，行程单/扫描件 PDF 直接返回 `_error`。 | `tool_itinerary_ocr.py:87-123`、`tool_ocr_extract.py:225-242` | 环境差异导致功能不一致 |
| B | 🟠 中 | **`auto` 模式注释与实际不符**：注释称"优先 PaddleOCR"，但 `requirements.txt` 不含 paddle，生产环境 `auto` 永远回退 Tesseract。 | `image_ocr.py:7-13,55-84` | 误导运维；"auto" 名不副实 |
| C | 🟠 中 | **部署文档与实现冲突**：`docs/integration/` 架构图写"推理服务器(PaddleOCR+…)"，但实际用 Tesseract 且未装 Paddle。 | `docs/integration/*`、`requirements.txt` | 部署误导 |
| D | 🟡 低 | **Vision 兜底有效性隐患**：`DEEPSEEK_VISION_MODEL` 默认 = `DEEPSEEK_MODEL = "deepseek-v4-flash"`，配置注释称该常量"仅保留用于自检…未来切回视觉模型时可复用"，暗示当前模型**可能不支持多模态**。若模型无视觉能力，发票图片在本地 OCR 不可用时仍会失败（带 `_error`）。 | `config.py:23`、`http_client.py:169`、`tool_ocr_extract.py:151-222` | 兜底"看起来有，实际可能无效" |
| E | 🟡 低 | **DeepSeek 停用边界**：本地 OCR 成功 → Function Call 返回 `_disabled` 时被当作正常 `result` 进入字段缺失检查，逻辑绕且会误跑 `save_ai_check_result`。 | `tool_ocr_extract.py:111-148`、`ocr_node.py:30-50` | 边界混乱（非阻断） |
| F | 🟡 低 | **发往 Vision 的图片未压缩**：整张大发票图 base64 可能数 MB，有超请求体/高 token 成本风险。 | `tool_ocr_extract.py:166-171` | 健壮性与成本 |
| G | 🟢 增强 | **预处理对旋转/极端小字号不足**：实测合成图"发票号码"前导"发"漏识；无旋转校正、无二值化细化、无置信度过滤。 | `image_ocr.py:130-151` | 识别率上限 |
| H | 🟢 增强 | **语言包硬编码 `chi_sim+eng`**：繁体/纯英文发票识别率下降；默认未含 `chi_tra`。 | `config.py:32` | 票种覆盖 |
| I | 🟢 增强 | **缺真实 OCR 集成测试**：现有测试全 mock，无"有 Tesseract 时端到端跑 OCR"的可选用例。 | `tests/test_local_ocr_pipeline.py` | CI 无法防止回归 |

---

## 四、发票图片提取适配：可行方案

现状已支持发票图片提取且本机本地 OCR 可用。方案围绕 **更稳 / 更准 / 更一致** 四个目标，按优先级给出。

### 方案 1（P0，必做）：固化本地 OCR 引擎并消除文档/依赖矛盾
- 把 `LOCAL_OCR_ENGINE=tesseract` 与 `TESSERACT_CMD` 写入 `.env.example` 与部署文档，明确"本地 Tesseract 为唯一 OCR 引擎"。
- `requirements.txt` 显式列出 `pytesseract`，并在部署文档写明系统依赖：`brew install tesseract tesseract-lang`（macOS）/ `apt install tesseract-ocr tesseract-ocr-chi-sim`（Ubuntu）。
- 修正 `image_ocr.py` 顶部与 `_resolve_engine` 的 "auto 优先 PaddleOCR" 注释；若确需 Paddle，则把 `paddleocr`/`paddlepaddle` 加入依赖并真实验证，否则统一口径为 Tesseract。
- 统一部署文档 `docs/integration/` 的"推理服务器"描述为实际使用的 Tesseract（或删除 PaddleOCR 字样）。

### 方案 2（P0，必做）：统一兜底策略（解决发现 A/D）
把发票现有的 Vision 三级兜底抽象为**共享函数**，供发票与行程单复用，并为扫描件 PDF 增加 Vision 兜底：

```python
# skill/utils/ocr_fallback.py（新增）
def ocr_image_with_vision_fallback(
    image_path, *, tool_def, essential_fields, system_prompt, reason=""
) -> dict:
    """本地 OCR 已抽到文本时由调用方先走；本函数封装：本地 OCR 异常→Vision，
    漏字段→Vision 聚焦重试。发票/行程单共用，消除不一致。"""
    ...
```

- 扫描件 PDF：将首页（或全部页拼接）渲染为图，本地 OCR 失败时走同一 Vision 兜底（`tool_ocr_extract._ocr_extract_scanned_pdf` / `tool_itinerary_ocr._ocr_extract_scanned_pdf` 复用）。
- 明确 Vision 模型：将 `DEEPSEEK_VISION_MODEL` 默认改为**确认支持多模态**的模型（如 `deepseek-vl2` 或核实 `deepseek-v4-flash` 是否多模态），否则兜底形同虚设（对应发现 D）。

### 方案 3（P1，推荐）：增强发票图片预处理（解决发现 G/H）
在 `image_ocr._preprocess_for_ocr` 基础上：
- **旋转校正**：用 `pytesseract.image_to_osd` 获取 `Orientation in degrees` 并 `img.rotate` 校正；或引入 `opencv-python` 做霍夫线校正。
- **自适应二值化**：对红章/底纹干扰重的发票，用 `cv2.adaptiveThreshold` 或 `PIL Image.point` 二值化，减少灰度噪声。
- **置信度过滤**：用 `pytesseract.image_to_data` 拿 `conf`，剔除 `conf < 30` 的低置信行，降低噪声喂给 DeepSeek。
- **多票种语言**：默认 `TESSERACT_LANG=chi_sim+chi_tra+eng`，并允许按文件扩展/内容探测切换。

### 方案 4（P1，推荐）：Vision 兜底健壮性（解决发现 F/E）
- 发送前将图片最长边压缩到 ~1600px（保持比例）再 base64，控制请求体与 token 成本。
- Vision 调用返回 `_disabled` 时，直接透传并**不再**做字段补全逻辑（修复发现 E），由 `ocr_node` 统一置 `ERROR`。
- 记录 `_fallback_reason`、识别字符数、耗时到日志/用量统计，便于运营分析。

### 方案 5（P2，增强）：测试与可观测性
- 新增 pytest marker `@pytest.mark.ocr_integration`：当环境中 `image_ocr._resolve_engine()` 可用时，用真实发票样图（可复用 `scripts/make_test_invoice.py` 生成的图）跑端到端，断言「发票号码/金额/开票日期」可抽出；CI 默认可跳过。
- 在 `_record_usage` 中区分 `ocr_engine=tesseract|vision`，观测兜底触发率。

### 落地优先级建议
1. 方案 1 + 方案 2（消除不一致与文档矛盾，风险最低、收益最高）→ 立即做。
2. 方案 3（提升发票图片识别率，尤其旋转/红章/小字号场景）→ 迭代做。
3. 方案 4 + 方案 5（健壮性与可观测）→ 跟随迭代。

---

## 五、评审总结

- **本地 OCR 现状：可用、有效**（Tesseract 5.5.2 + chi_sim 语言包，已实测识别中文发票字段）。无需为"本机能否跑 OCR"担忧。
- **核心风险不在 OCR 本身，而在"兜底策略不一致"与"Vision 模型能力未确认"**：发票图片有完善三级兜底，行程单与扫描件 PDF 无兜底；且兜底所依赖的 Vision 模型是否真支持多模态需核实（发现 A/D）。
- **文档与依赖需对齐**：`auto` 优先 PaddleOCR 的说法、部署文档的 PaddleOCR 推理服务器，与实际只用 Tesseract 不符（发现 B/C）。
- 代码整体质量高、静态扫描干净，方案 1+2 改动集中、风险可控，建议优先落地。

> 说明：本次"全代码评审"对 OCR/发票/行程单主线做了逐文件精读，并对 `skill/` 全量做了编译与代码异味静态扫描；`web/`、`docs/`、规则 YAML、`prompt` 等模块未逐行评审，如需可针对具体模块继续深入。
