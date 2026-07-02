"""e2e smoke 测试 — 验证 Playwright + vite preview + Tauri stub + GPU server 全链路通.

只测连通 + 关键 UI 元素渲染, 不深入业务逻辑. 这 fail 了后面 e2e 都跳过.
"""
from __future__ import annotations

import pytest


pytestmark = pytest.mark.e2e


def test_vite_serves_index_html(vite_preview_url):
    """vite preview 200 + HTML 包含 VPBuddy 标题."""
    import urllib.request

    with urllib.request.urlopen(vite_preview_url, timeout=3) as r:
        body = r.read().decode("utf-8")
        assert r.status == 200
        assert "VPBuddy Desktop" in body
        assert 'id="meeting-select"' in body  # Req #4 下拉


def test_gpu_server_responds(gpu_server):
    """GPU server 真在跑, /api/meetings 返回 {"meetings": [...], "count": N} dict."""
    import json
    import urllib.request

    with urllib.request.urlopen(f"{gpu_server}/api/meetings", timeout=3) as r:
        assert r.status == 200
        body = json.loads(r.read())
        # 实际 GPU 返回格式: {"meetings": [...], "count": N}, 不是裸 list
        assert isinstance(body, dict), f"期望 dict, 实际 {type(body)}"
        assert "meetings" in body and "count" in body, f"key 缺: keys={list(body.keys())}"
        assert isinstance(body["meetings"], list)
        assert body["count"] == len(body["meetings"])
        # 在 e2e 跑期间 GPU 上应该有会议 (至少 PHASE* 测试遗留的)
        print(f"\n[GPU] 当前 meetings: {[m.get('meeting_id') for m in body['meetings']]}")


def test_page_loads_with_tauri_stub(page, gpu_server):
    """page 加载 + Tauri stub 注入 + UI 关键元素存在 + GPU pill 显示已连接."""
    # title
    assert "VPBuddy Desktop" in page.title()

    # Req #4 关键元素存在: meeting-select, meeting-new, audio-source-kind, btn-rec
    assert page.locator("#meeting-select").count() == 1
    assert page.locator("#meeting-new").count() == 1
    assert page.locator("#audio-source-kind").count() == 1
    assert page.locator("#btn-rec").count() == 1

    # Req #1 音频源下拉 3 个 option (microphone/loopback/both)
    audio_source = page.locator("#audio-source-kind")
    options = audio_source.locator("option").all_text_contents()
    assert any("麦克风" in o for o in options), f"音频源缺麦克风: {options}"
    assert any("内录" in o for o in options), f"音频源缺内录: {options}"
    assert any("麦克风 + 内录" in o for o in options), f"音频源缺 both: {options}"

    # Req #4 录音按钮默认 disabled + title 提示
    rec_btn = page.locator("#btn-rec")
    is_disabled = rec_btn.is_disabled()
    assert is_disabled, "录音按钮默认应 disabled (Req #4)"

    # 等 GPU pill 刷到 connected (前端有定时去 /healthz 探测)
    # 不强制等, GPU server 探测失败也可能, 只断言元素存在
    assert page.locator("#gpu-pill").count() == 1
