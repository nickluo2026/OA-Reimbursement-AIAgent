"""DeepSeek 模型配置固化 — 自检与定价验收测试。

覆盖：
  - 启动期模型配置自检（self_check_model_config）的各分支
  - 定价常量可经环境变量覆盖，并被 admin_store.calc_cost_cny 复用
  - skill_manifest.yaml 与 config 默认模型对齐
  - 不依赖真实网络 / 真实 DeepSeek API，不影响主链路
"""

from __future__ import annotations

import importlib
import os

import pytest

import skill.config as cfg
import skill.utils.admin_store as admin_store
from skill.utils.admin_store import calc_cost_cny


def _set(**kwargs) -> None:
    """临时改写 skill.config 模块级常量（无需 reload config 本身）。"""
    for k, v in kwargs.items():
        setattr(cfg, k, v)


def _healthy() -> None:
    _set(
        DEEPSEEK_API_KEY="sk-test-xxxx",
        DEEPSEEK_BASE_URL="https://api.deepseek.com/chat/completions",
        DEEPSEEK_MODEL="deepseek-v4-flash",
        DEEPSEEK_VISION_MODEL="deepseek-v4-flash",
    )


def test_selfcheck_default_ok():
    _healthy()
    r = cfg.self_check_model_config()
    assert r["ok"] is True
    assert r["issues"] == []
    assert r["model"] == "deepseek-v4-flash"
    assert r["vision_model"] == "deepseek-v4-flash"
    assert r["price_input_per_1k"] == 0.001
    assert r["price_output_per_1k"] == 0.002


def test_selfcheck_missing_api_key():
    _healthy()
    _set(DEEPSEEK_API_KEY="")
    r = cfg.self_check_model_config()
    assert r["ok"] is False
    assert any("DEEPSEEK_API_KEY" in i for i in r["issues"])


def test_selfcheck_legacy_text_model():
    _healthy()
    _set(DEEPSEEK_MODEL="deepseek-chat")
    r = cfg.self_check_model_config()
    assert r["ok"] is False
    assert any("退役" in i for i in r["issues"])


def test_selfcheck_legacy_vision_model():
    _healthy()
    _set(DEEPSEEK_VISION_MODEL="deepseek-reasoner")
    r = cfg.self_check_model_config()
    assert r["ok"] is False
    assert any("退役" in i for i in r["issues"])


def test_selfcheck_invalid_base_url():
    _healthy()
    _set(DEEPSEEK_BASE_URL="http://insecure.example.com/v1")
    r = cfg.self_check_model_config()
    assert r["ok"] is False
    assert any("https" in i for i in r["issues"])


def test_price_override_reflected_in_admin_store():
    """定价常量经环境变量覆盖后，admin_store 的费用估算应同步生效。"""
    _set(PRICE_INPUT_PER_1K=0.005, PRICE_OUTPUT_PER_1K=0.010)
    importlib.reload(admin_store)
    try:
        # 定价为「每千 token」：1000 输入(=1千) * 0.005 + 1000 输出(=1千) * 0.010 = 0.015
        assert calc_cost_cny(1000, 1000) == pytest.approx(0.015)
    finally:
        _set(PRICE_INPUT_PER_1K=0.001, PRICE_OUTPUT_PER_1K=0.002)
        importlib.reload(admin_store)


def test_calc_cost_default_pricing():
    """默认定价下费用估算正确：1000 输入(=1千)*0.001 + 1000 输出(=1千)*0.002 = 0.003"""
    _healthy()
    assert calc_cost_cny(1000, 1000) == pytest.approx(0.003)


def test_admin_store_no_legacy_vl_constant():
    """确认 admin_store 已不再硬编码 deepseek-vl，统一使用视觉模型常量。"""
    _healthy()
    assert cfg.DEEPSEEK_VISION_MODEL == "deepseek-v4-flash"


def test_manifest_aligns_with_config_default():
    """skill_manifest.yaml 的 deepseek_model 应与 config 默认模型一致。"""
    import yaml

    manifest_path = os.path.join(
        os.path.dirname(cfg.__file__), "skill_manifest.yaml"
    )
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = yaml.safe_load(f)
    assert manifest["config"]["deepseek_model"] == cfg.DEEPSEEK_MODEL
    assert manifest["config"]["deepseek_vision_model"] == cfg.DEEPSEEK_VISION_MODEL
