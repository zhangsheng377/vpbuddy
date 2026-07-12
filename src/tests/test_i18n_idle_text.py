"""v0.22.6: i18n 文案 — idle "录音就绪" 而非 "未连接" (录音断开 ≠ 服务断开)"""

from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest


def test_i18n_zh_idle_means_ready_not_disconnected():
    """zh idle 必须包含"就绪"或"准备"，不能是"未连接"（录音断开≠服务断开）."""
    import re

    js_path = Path(__file__).parent.parent.parent / "vpbuddy-client" / "ui" / "main.js"
    src = js_path.read_text(encoding="utf-8")

    m = re.search(r'zh:\s*\{[^}]*idle:\s*"([^"]+)"', src)
    assert m, "找不到 i18n.zh.idle"
    idle_text = m.group(1)
    assert "未连接" not in idle_text, (
        f"idle 不能叫'未连接'（录音断开 ≠ 服务断开），当前值: {idle_text!r}"
    )
    assert "就绪" in idle_text or "准备" in idle_text, (
        f"idle 应含'就绪'或'准备'，当前值: {idle_text!r}"
    )


def test_i18n_en_idle_not_contains_chinese():
    """en idle 不含中文."""
    import re

    js_path = Path(__file__).parent.parent.parent / "vpbuddy-client" / "ui" / "main.js"
    src = js_path.read_text(encoding="utf-8")

    m = re.search(r'en:\s*\{[^}]*idle:\s*"([^"]+)"', src)
    assert m, "找不到 i18n.en.idle"
    idle_text = m.group(1)
    import unicodedata
    has_cjk = any("CJK" in unicodedata.name(c, "") for c in idle_text if c != " ")
    assert not has_cjk, f"en idle 不应含中文: {idle_text!r}"
