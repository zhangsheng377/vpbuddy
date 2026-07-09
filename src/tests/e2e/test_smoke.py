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
    """GPU server 真在跑, 注册 + 登录后 /api/meetings 返回 {"meetings": [...], "count": N} dict."""
    import json
    import time
    import urllib.request

    email = f"e2e_smoke_{int(time.time())}@test.com"
    password = "test123456"

    reg_data = json.dumps({"email": email, "password": password}).encode()
    req = urllib.request.Request(
        f"{gpu_server}/api/auth/register",
        data=reg_data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        assert r.status == 200
        body = json.loads(r.read())
        token = body["token"]

    req = urllib.request.Request(
        f"{gpu_server}/api/meetings",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=3) as r:
        assert r.status == 200
        body = json.loads(r.read())
        assert isinstance(body, dict), f"期望 dict, 实际 {type(body)}"
        assert "meetings" in body and "count" in body, f"key 缺: keys={list(body.keys())}"
        assert isinstance(body["meetings"], list)
        assert body["count"] == len(body["meetings"])
        print(f"\n[GPU] 当前 meetings: {[m.get('meeting_id') for m in body['meetings']]}")


def test_page_loads_with_tauri_stub(page, gpu_server):
    """page 加载 + Tauri stub 注入 + UI 关键元素存在."""
    assert "VPBuddy Desktop" in page.title()

    assert page.locator("#meeting-select").count() == 1
    assert page.locator("#meeting-new").count() == 1
    assert page.locator("#audio-source-kind").count() == 1
    assert page.locator("#btn-rec").count() == 1

    audio_source = page.locator("#audio-source-kind")
    options = audio_source.locator("option").all_text_contents()
    assert any("麦克风" in o for o in options), f"音频源缺麦克风: {options}"
    assert any("内录" in o for o in options), f"音频源缺内录: {options}"
    assert any("麦克风 + 内录" in o for o in options), f"音频源缺 both: {options}"

    rec_btn = page.locator("#btn-rec")
    is_disabled = rec_btn.is_disabled()
    assert is_disabled, "录音按钮默认应 disabled (Req #4)"

    assert page.locator("#gpu-pill").count() == 1
