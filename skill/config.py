# -*- coding: utf-8 -*-
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
DEEPSEEK_BASE_URL: str = os.getenv(
    "DEEPSEEK_BASE_URL", "https://api.deepseek.com/chat/completions"
)
DEEPSEEK_MODEL: str = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
# 视觉调用复用同一模型（V4-Flash 原生多模态）；
# 保留独立常量，便于未来切换专用视觉模型。
DEEPSEEK_VISION_MODEL: str = os.getenv("DEEPSEEK_VISION_MODEL", DEEPSEEK_MODEL)
TEMPERATURE: float = 0.0
MAX_TOKENS: int = 4096
REQUEST_TIMEOUT: int = 120

# ============ DeepSeek-V4-Flash 定价（CNY / 千 token）============
# 与官方价（≈$0.14/M 输入 · $0.28/M 输出）换算一致，可经环境变量覆盖。
PRICE_INPUT_PER_1K: float = float(os.getenv("DEEPSEEK_PRICE_INPUT_PER_1K", "0.001"))
PRICE_OUTPUT_PER_1K: float = float(os.getenv("DEEPSEEK_PRICE_OUTPUT_PER_1K", "0.002"))

# ============ 业务配置 ============
SMALL_AMOUNT_THRESHOLD: float = 100.0  # 小额免审阈值（元）

# ============ 发票查验平台配置 ============
# 查验 Provider：mock（默认）/ 预留真实平台扩展点
INVOICE_VERIFY_PROVIDER: str = os.getenv("INVOICE_VERIFY_PROVIDER", "mock")
# 查验平台调用超时（秒），供真实 Provider 使用
INVOICE_VERIFY_TIMEOUT: int = int(os.getenv("INVOICE_VERIFY_TIMEOUT", "30"))

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
    return {
        "ok": not issues,
        "model": DEEPSEEK_MODEL,
        "vision_model": DEEPSEEK_VISION_MODEL,
        "base_url": DEEPSEEK_BASE_URL,
        "price_input_per_1k": PRICE_INPUT_PER_1K,
        "price_output_per_1k": PRICE_OUTPUT_PER_1K,
        "issues": issues,
    }


def _load_yaml(filename: str) -> dict[str, Any]:
    """加载 rules 目录下的 YAML 文件"""
    filepath = RULES_DIR / filename
    with open(filepath, "r", encoding="utf-8") as f:
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
    # 管理员配置覆盖餐饮单笔上限
    if "limit_meal_single" in admin:
        limits["餐饮"] = float(admin["limit_meal_single"])
    return limits


def get_anomaly_rules() -> dict[str, Any]:
    """获取异常检测规则配置（YAML 默认 + 管理员覆盖）"""
    rules = _load_yaml("anomaly_rules.yaml")
    admin = get_system_config_overrides()
    # 行程单单笔金额阈值覆盖
    if "limit_itinerary_single" in admin:
        rules["itinerary_single_amount_threshold"] = float(
            admin["limit_itinerary_single"]
        )
    # 规则开关（默认 True，管理员可关闭）
    rules["enable_amount_check"] = admin.get("rule_amount", True)
    rules["enable_deepseek_semantic"] = admin.get("rule_deepseek_semantic", True)
    rules["enable_itinerary_field"] = admin.get("rule_itinerary_field", True)
    return rules


def get_itinerary_rules() -> dict[str, Any]:
    """获取行程单校验规则配置（与异常规则同文件，按 key 隔离）"""
    return get_anomaly_rules()


def get_verify_rules() -> dict[str, Any]:
    """获取发票查验配置（YAML 默认 + 管理员覆盖）"""
    rules = _load_yaml("anomaly_rules.yaml")
    admin = get_system_config_overrides()
    rules["verify_block_on_fake"] = admin.get(
        "verify_block_on_fake", rules.get("verify_block_on_fake", True)
    )
    rules["verify_block_on_error"] = admin.get(
        "verify_block_on_error", rules.get("verify_block_on_error", False)
    )
    # 管理员可通过 rule_invoice_auth 关闭「检测发票真伪（国税查验）」
    rules["enable_invoice_auth"] = admin.get("rule_invoice_auth", True)
    return rules
