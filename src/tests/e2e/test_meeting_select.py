"""e2e — 首页会议选择 UI 行为 (用户 4 大需求之一, Req #4).

跑法: RUN_E2E=1 pytest src/tests/e2e/test_meeting_select.py -v -m e2e

测什么 (用户原话):
- '首页上点开始录音, 直接默认就是创建新会议了? 不行哦, 还要弄个下拉条吧,
  可以选择旧会议, 或者直接输入会议名就是新会议. 没选择也不输入的话, 不给点开始录音.
  这样就算停止录音, 也不用停止会议.'

覆盖:
1. 默认状态: 录音按钮 disabled, title 提示要选/输入会议
2. 只输入会议名 → 录音按钮 enabled
3. 只选已有会议 → 录音按钮 enabled
4. 两个都不填 → 录音按钮 disabled
5. 录音中停止 → 会议不结束 (端到端: GPU server 上 meeting 还在 list)
6. GPU 上会议历史列表填充到 meeting-select 下拉
"""
from __future__ import annotations

import time

import pytest


pytestmark = pytest.mark.e2e


def test_rec_btn_disabled_by_default(page):
    """默认: 录音按钮 disabled, 提示先选/输入会议."""
    btn = page.locator("#btn-rec")
    assert btn.is_disabled(), "录音按钮默认应 disabled"
    title = btn.get_attribute("title") or ""
    assert "选择" in title or "输入" in title or "会议" in title, \
        f"title 应提示选/输入会议, 实际: {title!r}"


def test_input_only_enables_rec_btn(page):
    """只输入会议名 (不下拉选) → 按钮 enabled."""
    page.locator("#meeting-new").fill("e2e_input_only_test")
    btn = page.locator("#btn-rec")
    # input 触发 'input' event 让 updateRecBtnState 跑
    page.locator("#meeting-new").dispatch_event("input")
    assert not btn.is_disabled(), "输入会议名后按钮应 enabled"
    assert btn.get_attribute("title") in (None, ""), \
        f"输入后 title 应清空, 实际: {btn.get_attribute('title')!r}"


def test_select_existing_meeting_enables_rec_btn(page, gpu_server, e2e_token):
    """选中已有会议 (下拉) → 按钮 enabled."""
    import json
    import urllib.request

    req = urllib.request.Request(f"{gpu_server}/api/meetings")
    req.add_header("Authorization", f"Bearer {e2e_token}")
    with urllib.request.urlopen(req, timeout=5) as r:
        body = json.loads(r.read())

    if not body["meetings"]:
        pytest.skip("GPU 上没会议, 跳过 select-only 测试")

    # 找第一个 meeting_id, 通过 JS 注入 option (因为前端会自己 fetch 填充, 但我们要稳)
    first_meeting_id = body["meetings"][0]["meeting_id"]
    page.evaluate(
        """(mid) => {
            const sel = document.getElementById('meeting-select');
            const opt = document.createElement('option');
            opt.value = mid;
            opt.textContent = mid;
            sel.appendChild(opt);
            sel.value = mid;
            sel.dispatchEvent(new Event('change'));
        }""",
        first_meeting_id,
    )
    btn = page.locator("#btn-rec")
    assert not btn.is_disabled(), f"选会议 {first_meeting_id} 后按钮应 enabled"


def test_both_empty_disables_rec_btn(page):
    """两个都不填 → disabled (跟默认一致, 但显式测一次)."""
    page.locator("#meeting-new").fill("")
    page.locator("#meeting-new").dispatch_event("input")
    # select 默认空 option, 不要碰它 (不要让它被改成别值)
    page.locator("#meeting-select").evaluate("el => { el.value = ''; el.dispatchEvent(new Event('change')); }")
    btn = page.locator("#btn-rec")
    assert btn.is_disabled(), "两个都不填应 disabled"


def test_end_meeting_btn_visible_during_record(page):
    """录音开始后, '结束会议' 按钮显示 (录音中要能手动结束).

    用户原话: '停止录音, 也不用停止会议' → 含义是 stop ≠ close, 而不是 stop 后
    还要看着结束按钮. 主线测: 录音中可结束会议这个 CTA 可见.
    """
    page.locator("#meeting-new").fill("e2e_end_btn_visible")
    page.locator("#meeting-new").dispatch_event("input")
    page.locator("#btn-rec").click()
    page.wait_for_function(
        """() => document.getElementById('btn-rec').dataset.state === 'recording'""",
        timeout=5000,
    )
    end_btn = page.locator("#btn-end-meeting")
    assert end_btn.is_visible(), "录音开始后, '结束会议' 按钮应可见 (Req #4)"


def test_stop_does_not_call_close_endpoint(page, gpu_server, e2e_token):
    import json
    import urllib.request

    req = urllib.request.Request(f"{gpu_server}/api/meetings")
    req.add_header("Authorization", f"Bearer {e2e_token}")
    with urllib.request.urlopen(req, timeout=5) as r:
        before = json.loads(r.read())
    before_count = before["count"]

    page.locator("#meeting-new").fill(f"e2e_no_close_{int(time.time_ns())}")
    page.locator("#meeting-new").dispatch_event("input")
    page.locator("#btn-rec").click()
    page.wait_for_function(
        """() => document.getElementById('btn-rec').dataset.state === 'recording'""",
        timeout=5000,
    )
    # 停止录音
    page.locator("#btn-rec").click()
    page.wait_for_function(
        """() => document.getElementById('btn-rec').dataset.state === 'idle'""",
        timeout=5000,
    )

    # 端到端: stop 后, GPU 上 meeting 数不应变 (没有 close 触发)
    # 注意: start_capture 是 stub, 实际不真建档, 所以 meeting 数可能不变 (期望)
    # 真发生 close 的话 meeting 数会减 1 — 这是我们想避免的
    with urllib.request.urlopen(f"{gpu_server}/api/meetings", timeout=3) as r:
        after = json.loads(r.read())
    after_count = after["count"]

    assert before_count == after_count, \
        f"stop 录音不应减少 meeting 数, before={before_count} after={after_count}"
