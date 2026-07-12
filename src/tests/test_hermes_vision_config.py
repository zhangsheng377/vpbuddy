"""v0.22.6: Hermes auxiliary.vision 配置看护 — 必须含 api_key/base_url，不能空白"""

from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest


def test_hermes_vision_has_api_key():
    """Hermes auxiliary.vision 必须配置 api_key（否则 fallback 到全局 OPENAI_API_KEY 打 MiniMax → 401）."""
    import yaml

    config_path = Path("/root/.hermes/config.yaml")
    if not config_path.exists():
        config_path = Path.home() / ".hermes" / "config.yaml"
    if not config_path.exists():
        pytest.skip("Hermes config.yaml 不存在")
    config = yaml.safe_load(config_path.read_text())
    vision = config.get("auxiliary", {}).get("vision", {})
    assert vision.get("api_key"), (
        "Hermes auxiliary.vision 必须含 api_key，否则 vision 工具会 fallback 到全局 OPENAI_API_KEY (MiniMax key) → 401"
    )


def test_hermes_vision_has_base_url():
    """Hermes auxiliary.vision 必须配置 base_url 指向 DashScope."""
    import yaml

    config_path = Path("/root/.hermes/config.yaml")
    if not config_path.exists():
        config_path = Path.home() / ".hermes" / "config.yaml"
    if not config_path.exists():
        pytest.skip("Hermes config.yaml 不存在")
    config = yaml.safe_load(config_path.read_text())
    vision = config.get("auxiliary", {}).get("vision", {})
    assert vision.get("base_url"), (
        "Hermes auxiliary.vision 必须含 base_url，否则打的是默认 OpenAI/MiniMax endpoint"
    )
    assert "dashscope" in vision["base_url"], (
        f"Vision base_url 必须指向 DashScope，当前: {vision['base_url']}"
    )


def test_hermes_vision_model_is_qwen_vl():
    """Hermes auxiliary.vision.model 必须包含 qwen-vl（DashScope 视觉模型）."""
    import yaml

    config_path = Path("/root/.hermes/config.yaml")
    if not config_path.exists():
        config_path = Path.home() / ".hermes" / "config.yaml"
    if not config_path.exists():
        pytest.skip("Hermes config.yaml 不存在")
    config = yaml.safe_load(config_path.read_text())
    vision = config.get("auxiliary", {}).get("vision", {})
    assert "qwen-vl" in vision.get("model", ""), (
        f"Vision model 必须为 qwen-vl-* 系列（DashScope），当前: {vision.get('model', 'N/A')}"
    )
