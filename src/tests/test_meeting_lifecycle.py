"""测试 vpbuddy.ui_server._validate_meeting_id 纯函数.

v0.9.0: HTTP 端点测试已删除 (旧 Handler 移除), 仅保留纯函数测试。
"""

from __future__ import annotations
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from vpbuddy.ui_server import (
    _validate_meeting_id,
    DATA_DIR as REAL_DATA_DIR,
)


# ── _validate_meeting_id 纯函数测试 ──


def test_validate_accepts_alphanumeric():
    ok, err = _validate_meeting_id("mtg-2026-q4")
    assert ok is True
    assert err == ""


def test_validate_rejects_too_short():
    ok, err = _validate_meeting_id("ab")
    assert ok is False
    assert "3-48" in err


def test_validate_rejects_too_long():
    ok, err = _validate_meeting_id("a" * 49)
    assert ok is False
    assert "3-48" in err


def test_validate_rejects_spaces():
    ok, err = _validate_meeting_id("my meeting")
    assert ok is False


def test_validate_rejects_chinese():
    """会议 ID 不许中文 (ADR-0022 — 简化 ID, 显示名走 project_name)."""
    ok, err = _validate_meeting_id("我的会议")
    assert ok is False


def test_validate_accepts_underscore_and_dash():
    assert _validate_meeting_id("a_b-c")[0] is True
    assert _validate_meeting_id("123")[0] is True


def test_validate_rejects_special_chars():
    assert _validate_meeting_id("mtg@home")[0] is False
    assert _validate_meeting_id("mtg/2026")[0] is False
    assert _validate_meeting_id("mtg.2026")[0] is False


def test_validate_exact_boundary_lengths():
    """3 字符 OK, 48 字符 OK."""
    assert _validate_meeting_id("abc")[0] is True
    assert _validate_meeting_id("a" * 48)[0] is True
    assert _validate_meeting_id("ab")[0] is False
    assert _validate_meeting_id("a" * 49)[0] is False



# ── HTTP 端点测试 — v0.9.0 旧 Handler 已删除, 由 FastAPI E2E 覆盖 ──
