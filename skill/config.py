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


def get_category_limits() -> dict[str, float]:
    """获取费用分类限额字典，如 {"餐饮": 300, "交通": 500, ...}"""
    data = _load_yaml("category_limits.yaml")
    return data.get("category_limits", {})


def get_anomaly_rules() -> dict[str, Any]:
    """获取异常检测规则配置"""
    return _load_yaml("anomaly_rules.yaml")


def get_itinerary_rules() -> dict[str, Any]:
    """获取行程单校验规则配置（与异常规则同文件，按 key 隔离）"""
    return _load_yaml("anomaly_rules.yaml")
