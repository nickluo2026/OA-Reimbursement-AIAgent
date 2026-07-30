"""全局配置：从环境变量与 YAML 规则文件加载"""

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

load_dotenv()

# ============ 路径常量 ============
SKILL_ROOT = Path(__file__).resolve().parent
RULES_DIR = SKILL_ROOT / "rules"

# ============ DeepSeek API 配置 ============
DEEPSEEK_API_KEY: str = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/chat/completions")
DEEPSEEK_MODEL: str = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
# 注意：图片/扫描件 OCR 已改为「本地 OCR 引擎抽文本 → DeepSeek Function Call 文本管线」
# （方案 A），不再依赖大模型原生多模态能力。
# 该常量仅保留用于配置自检展示与历史用量统计兼容，未来切回视觉模型时可复用。
DEEPSEEK_VISION_MODEL: str = os.getenv("DEEPSEEK_VISION_MODEL", DEEPSEEK_MODEL)
TEMPERATURE: float = 0.0
MAX_TOKENS: int = 4096
REQUEST_TIMEOUT: int = 120

# ============ 本地 OCR 引擎配置（方案 A）============
# 生产环境默认引擎为 Tesseract（已内置依赖 pytesseract，需系统安装 tesseract 与语言包）。
# PaddleOCR 为可选增强：仅当另装 paddleocr/paddlepaddle 后，设 paddle 或 auto 才会启用。
# 引擎选择：auto（默认，优先 PaddleOCR，未装则回退 Tesseract）/ paddle / tesseract
LOCAL_OCR_ENGINE: str = os.getenv("LOCAL_OCR_ENGINE", "auto")
# Tesseract 识别语言（简体 + 繁体 + 英文），覆盖简/繁体发票，需系统安装对应语言包
TESSERACT_LANG: str = os.getenv("TESSERACT_LANG", "chi_sim+chi_tra+eng")
# tesseract 可执行文件路径（留空则使用系统 PATH）
TESSERACT_CMD: str = os.getenv("TESSERACT_CMD", "")
# 扫描件 PDF 渲染为图片的分辨率（DPI）
OCR_RENDER_DPI: int = int(os.getenv("OCR_RENDER_DPI", "200"))
# Vision 兜底前图片最长边压缩上限（px），避免超大发票图占用过量请求体/token
OCR_VISION_MAX_SIDE: int = int(os.getenv("OCR_VISION_MAX_SIDE", "1600"))
# 是否启用 DeepSeek Vision 兜底（本地 OCR 失败 / 漏关键字段时降级「看图」识别）。
# 默认关闭：链路只走「本地 OCR 抽文本 → DeepSeek 文本 Function Call」，不调用任何多模态 API；
# 设为 true/1/yes/on 可恢复 Vision 安全网（需 DEEPSEEK_VISION_MODEL 支持多模态输入）。
OCR_VISION_FALLBACK_ENABLED: bool = os.getenv(
    "OCR_VISION_FALLBACK_ENABLED", "false"
).lower() in ("1", "true", "yes", "on")

# ============ DeepSeek-V4-Flash 定价（CNY / 千 token）============
# 与官方价（≈$0.14/M 输入 · $0.28/M 输出）换算一致，可经环境变量覆盖。
PRICE_INPUT_PER_1K: float = float(os.getenv("DEEPSEEK_PRICE_INPUT_PER_1K", "0.001"))
PRICE_OUTPUT_PER_1K: float = float(os.getenv("DEEPSEEK_PRICE_OUTPUT_PER_1K", "0.002"))

# ============ 用户可见文案常量 ============
DEEPSEEK_DISABLED_MSG: str = (
    "DeepSeek 大模型已停用（系统配置），请联系系统管理员启用" "DeepSeek大模型或者人工填写报销单"
)

# ============ 业务配置 ============
SMALL_AMOUNT_THRESHOLD: float = 100.0  # 小额免审阈值（元）

# 即将退役的旧模型名（2026-07-24 15:59 UTC 停服），用于启动自检拦截
_LEGACY_MODELS = {"deepseek-chat", "deepseek-reasoner"}


def self_check_model_config() -> dict[str, Any]:
    """启动期模型配置自检。

    不发起真实网络请求（避免启动阻塞/计费），仅校验配置完整性与命名有效性。
    返回各检查项状态，供 run_web 与 CLI 调用。
    """
    issues: list[str] = []
    if not DEEPSEEK_API_KEY:
        issues.append("DEEPSEEK_API_KEY 未配置")
    if not DEEPSEEK_BASE_URL.startswith("https://"):
        issues.append("DEEPSEEK_BASE_URL 非 https")
    if DEEPSEEK_MODEL in _LEGACY_MODELS or DEEPSEEK_VISION_MODEL in _LEGACY_MODELS:
        issues.append(
            f"检测到即将退役的旧模型名（{sorted(_LEGACY_MODELS)}），"
            f"将于 2026-07-24 15:59 UTC 停服，请改用 deepseek-v4-flash"
        )
    # 发现 D：Vision 兜底有效性提示（非阻断）。
    # 仅当 OCR_VISION_FALLBACK_ENABLED=True 时本地 OCR 不可用才依赖 DEEPSEEK_VISION_MODEL
    # 识别图片；兜底关闭时无需关注视觉能力。
    vision_model_note = (
        "请确认 DEEPSEEK_VISION_MODEL 支持多模态（图像）输入；"
        "否则本地 OCR 不可用时的发票/行程单图片兜底将失效"
    )
    if DEEPSEEK_VISION_MODEL != DEEPSEEK_MODEL:
        vision_model_note = ""  # 已显式指定不同视觉模型，默认不再提示
    if not OCR_VISION_FALLBACK_ENABLED:
        vision_model_note = (
            "Vision 兜底已禁用（OCR_VISION_FALLBACK_ENABLED=false）："
            "本地 OCR 失败将直接报错，不再降级多看模态"
        )
    return {
        "ok": not issues,
        "model": DEEPSEEK_MODEL,
        "vision_model": DEEPSEEK_VISION_MODEL,
        "base_url": DEEPSEEK_BASE_URL,
        "price_input_per_1k": PRICE_INPUT_PER_1K,
        "price_output_per_1k": PRICE_OUTPUT_PER_1K,
        "issues": issues,
        "vision_model_note": vision_model_note,
    }


def _load_yaml(filename: str) -> dict[str, Any]:
    """加载 rules 目录下的 YAML 文件"""
    filepath = RULES_DIR / filename
    with open(filepath, encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_system_config_overrides() -> dict[str, Any]:
    """读取管理员配置覆盖值（从 system_config 表）。

    失败时返回空字典，不影响主流程（YAML 默认值生效）。
    """
    try:
        from .utils.admin_store import get_system_config

        return get_system_config()
    except Exception:
        return {}


def get_deepseek_settings() -> dict[str, Any]:
    """返回 DeepSeek 运行时设置（管理员配置覆盖优先，环境变量兜底）。

    对应原型「启用/停用Deepseek大模型」分组：
        - ds_enabled: 是否启用 AI 校验（默认 True）
        - deepseek_api_key / base_url / model: 留空时回退到环境变量（config 模块常量）

    关闭 ds_enabled 时，http_client 将跳过真实模型调用，返回「已停用」标记，
    由各工具降级处理（规则引擎兜底 / 提示用户启用）。
    """
    admin = get_system_config_overrides()
    return {
        "enabled": bool(admin.get("ds_enabled", True)),
        "api_key": (admin.get("deepseek_api_key") or DEEPSEEK_API_KEY),
        "base_url": (admin.get("deepseek_base_url") or DEEPSEEK_BASE_URL),
        "model": (admin.get("deepseek_model") or DEEPSEEK_MODEL),
    }


def get_deepseek_enabled() -> bool:
    """是否启用 DeepSeek 大模型（AI 校验）。"""
    return bool(get_system_config_overrides().get("ds_enabled", True))


def get_deepseek_base_url() -> str:
    """DeepSeek API 地址（管理员覆盖优先）。"""
    return get_deepseek_settings()["base_url"]


def get_category_limits() -> dict[str, float]:
    """获取费用分类限额字典（YAML 默认 + 管理员覆盖）"""
    data = _load_yaml("category_limits.yaml")
    limits = dict(data.get("category_limits", {}))
    admin = get_system_config_overrides()
    # 管理员配置覆盖各分类限额（与 DEFAULT_CONFIG 键对齐）
    if "limit_travel_transport" in admin:
        limits["交通"] = float(admin["limit_travel_transport"])
    if "limit_travel_hotel" in admin:
        limits["住宿"] = float(admin["limit_travel_hotel"])
    if "limit_meal_single" in admin:
        limits["餐饮"] = float(admin["limit_meal_single"])
    if "limit_office" in admin:
        limits["办公"] = float(admin["limit_office"])
    if "limit_other" in admin:
        limits["其他"] = float(admin["limit_other"])
    return limits


def get_anomaly_rules() -> dict[str, Any]:
    """获取异常检测规则配置（YAML 默认 + 管理员覆盖）"""
    rules = _load_yaml("anomaly_rules.yaml")
    admin = get_system_config_overrides()
    # 规则开关（默认 True，管理员可关闭）
    rules["enable_amount_check"] = admin.get("rule_amount", True)
    rules["enable_deepseek_semantic"] = admin.get("rule_deepseek_semantic", True)
    rules["enable_itinerary_field"] = admin.get("rule_itinerary_field", True)
    return rules


def get_itinerary_rules() -> dict[str, Any]:
    """获取行程单校验规则配置（与异常规则同文件，按 key 隔离）"""
    return get_anomaly_rules()


