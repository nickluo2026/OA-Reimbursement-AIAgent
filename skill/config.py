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
TEMPERATURE: float = 0.0
MAX_TOKENS: int = 4096
REQUEST_TIMEOUT: int = 120

# ============ 业务配置 ============
SMALL_AMOUNT_THRESHOLD: float = 100.0  # 小额免审阈值（元）


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
    return rules


def get_itinerary_rules() -> dict[str, Any]:
    """获取行程单校验规则配置（与异常规则同文件，按 key 隔离）"""
    return get_anomaly_rules()
