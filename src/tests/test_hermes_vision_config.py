"""v0.22.6: Hermes auxiliary.vision 配置看护 — provider=custom + OPENAI_API_KEY/BASE_URL env → DashScope

ADR-0054: fastapi_app 启动时自动重命名 hermes_cli/runtime_provider.py → .bak，
让 _resolve_custom_runtime import 失败 → 走 env fallback → _create_openai_client → DashScope。
pip upgrade hermes-agent 后文件会恢复 → 需重新 apply (fastapi_app 重启自动处理)。
"""

from __future__ import annotations
import os, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest


def test_hermes_vision_provider_is_custom():
    """Hermes auxiliary.vision.provider 必须为 custom (不是 openai).
    
    openai 在 _resolve_strict_vision_backend 中没有匹配分支 → return None,None
    → fallback 到全局 Anthropic 客户端 → MiniMax key → api.anthropic.com → 401.
    custom 走 _try_custom_endpoint() → _resolve_custom_runtime() → 读 OPENAI_API_KEY/OPENAI_BASE_URL env.
    """
    import yaml

    config_path = Path("/root/.hermes/config.yaml")
    if not config_path.exists():
        config_path = Path.home() / ".hermes" / "config.yaml"
    if not config_path.exists():
        pytest.skip("Hermes config.yaml 不存在")
    config = yaml.safe_load(config_path.read_text())
    vision = config.get("auxiliary", {}).get("vision", {})
    assert vision.get("provider") == "custom", (
        f"Vision provider 必须为 custom（openai 不在 _resolve_strict_vision_backend 分支中），当前: {vision.get('provider', 'N/A')}"
    )


def test_hermes_vision_openai_api_key_points_to_dashscope():
    """OPENAI_API_KEY env 必须为 DashScope key (48 位 sk-) — _try_custom_endpoint 读此 env."""
    key = os.environ.get("OPENAI_API_KEY", "")
    if not key:
        pytest.skip("OPENAI_API_KEY 未在环境变量中设置（非 GPU 环境）")
    assert key.startswith("sk-"), f"OPENAI_API_KEY 必须以 sk- 开头，当前: {key[:20]}..."
    assert len(key) >= 48, f"OPENAI_API_KEY 长度不足 (DashScope key 通常 48+ 字符)，当前: {len(key)}"


def test_hermes_vision_openai_base_url_points_to_dashscope():
    """OPENAI_BASE_URL env 必须指向 DashScope 兼容端点."""
    url = os.environ.get("OPENAI_BASE_URL", "")
    if not url:
        pytest.skip("OPENAI_BASE_URL 未在环境变量中设置（非 GPU 环境）")
    assert "dashscope" in url, (
        f"OPENAI_BASE_URL 必须指向 DashScope，当前: {url}"
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
