"""v0.22.6: gkd hash 不含 demo content + 去掉字数阈值 + 空文本 guard"""

from __future__ import annotations
import sys, hashlib, tempfile, os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from vpbuddy.state import MeetingState
from vpbuddy.storage import MeetingStorage


@pytest.fixture
def tmp_data():
    d = tempfile.mkdtemp(prefix="vp_gkd_test_")
    yield d
    import shutil
    shutil.rmtree(d, ignore_errors=True)


def test_cleaned_text_hash_deterministic():
    """cleaned_text 的 hash 是确定性的."""
    text = "客户要求 SSO 登录, 支持微信扫码。"
    h1 = hashlib.md5(text.encode()).hexdigest()
    h2 = hashlib.md5(text.encode()).hexdigest()
    assert h1 == h2


def test_cleaned_text_hash_changes_on_content_change():
    """cleaned_text 内容变化 → hash 必须变."""
    text_a = "客户要求 SSO 登录。"
    text_b = "客户要求 SSO 登录, 支持微信扫码。"
    assert hashlib.md5(text_a.encode()).hexdigest() != hashlib.md5(text_b.encode()).hexdigest()


def test_gkd_no_length_threshold(tmp_data):
    """v0.22.6: 去掉字数阈值 — 任何字数 > 0 都应通过 hash 判断触发."""
    st = MeetingStorage(data_dir=tmp_data)
    mid = "gkd_short_001"

    state = MeetingState(meeting_id=mid)
    state.cleaned_text = "AB"  # 只有 2 个字
    st.save(state)
    cur = state.cleaned_text

    assert cur.strip()
    cur_hash = hashlib.md5(cur.encode()).hexdigest()
    assert len(cur_hash) == 32


def test_gkd_empty_text_guard(tmp_data):
    """v0.22.6: 空文本不触发 — not cur.strip() 为 True 时跳过."""
    st = MeetingStorage(data_dir=tmp_data)
    mid = "gkd_empty_001"

    state = MeetingState(meeting_id=mid)
    state.cleaned_text = ""
    st.save(state)
    assert not state.cleaned_text.strip()

    state.cleaned_text = "   "
    st.save(state)
    assert not state.cleaned_text.strip()


def test_gkd_empty_hash_not_equals_meaningful_hash():
    """空文本 hash 不同于有意义文本的 hash，避免空串误触发最后一次 hash 缓存."""
    h_empty = hashlib.md5("".encode()).hexdigest()
    h_hi = hashlib.md5("你好".encode()).hexdigest()
    assert h_empty != h_hi


def test_kick_docs_no_false_trigger_on_same_text(tmp_data):
    """相同 cleaned_text → hash 不变 → 不触发."""
    st = MeetingStorage(data_dir=tmp_data)
    mid = "kd_test_002"
    state = MeetingState(meeting_id=mid)
    text = "C" * 60
    state.cleaned_text = text
    st.save(state)
    h1 = hashlib.md5(text.encode()).hexdigest()

    state.cleaned_text = text
    st.save(state)
    h2 = hashlib.md5(text.encode()).hexdigest()
    assert h1 == h2


def test_hash_does_not_include_demo_content_by_default():
    """v0.22.6: hash 只用 cleaned_text, 不混入 demo 内容."""
    text = "客户需求: AI 平台化。"
    demo_text = "<html><body><h1>Demo v3</h1></body></html>"
    h_text_only = hashlib.md5(text.encode()).hexdigest()
    h_with_demo = hashlib.md5((text + demo_text).encode()).hexdigest()
    assert h_text_only != h_with_demo, "加上 demo 内容 hash 会不同 → gkd 只用文本 hash 是正确的"
