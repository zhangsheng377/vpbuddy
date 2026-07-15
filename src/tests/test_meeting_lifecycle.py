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
    _close_meeting,
    _finalized_meetings,
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



# ── v0.23.0: _close_meeting 幂等性测试 ──


def test_close_meeting_idempotent(monkeypatch, tmp_path):
    """重复调用 _close_meeting 返回同一缓存结果，不重复提交."""
    import tempfile
    # 设 DATA_DIR 为临时目录（避免 state 不存在等错误）
    monkeypatch.setattr("vpbuddy.ui_server.DATA_DIR", tmp_path)

    # 清理状态
    _finalized_meetings.clear()

    pushed_events = []
    submitted_docs = []

    monkeypatch.setattr(
        "vpbuddy.realtime_server.push_event",
        lambda mid, t, p: pushed_events.append((mid, t)),
    )
    monkeypatch.setattr(
        "vpbuddy.task_manager.get_task_manager",
        lambda: type("m", (), {
            "submit": lambda self, mid, runner: submitted_docs.append(mid),
        })(),
    )
    monkeypatch.setattr(
        "vpbuddy.agent_proactive.clear_throttle",
        lambda mid: 0,
    )

    # 第一次调用
    r1 = _close_meeting("idem_test")
    assert r1["status"] == "closed"
    assert r1["meeting_id"] == "idem_test"
    # meeting-complete 被推了一次
    complete_events = [e for e in pushed_events if e[1] == "meeting-complete"]
    assert len(complete_events) >= 1

    pushed_events_after = len(pushed_events)
    submitted_after = len(submitted_docs)

    # 第二次调用 → 应返回缓存
    r2 = _close_meeting("idem_test")
    assert r2 is r1  # 同一对象
    assert len(pushed_events) == pushed_events_after  # 没再推事件
    assert len(submitted_docs) == submitted_after  # 没再提交 doc task

    _finalized_meetings.clear()


def test_close_meeting_different_ids_independent(monkeypatch, tmp_path):
    """不同 meeting_id 各自独立 finalize."""
    _finalized_meetings.clear()

    monkeypatch.setattr("vpbuddy.ui_server.DATA_DIR", tmp_path)
    monkeypatch.setattr("vpbuddy.realtime_server.push_event", lambda mid, t, p: None)
    monkeypatch.setattr("vpbuddy.task_manager.get_task_manager", lambda: type("m", (object,), {"submit": staticmethod(lambda mid, runner: None)})())
    monkeypatch.setattr("vpbuddy.agent_proactive.clear_throttle", lambda mid: 0)

    r1 = _close_meeting("meeting_a")
    r2 = _close_meeting("meeting_b")
    assert r1 is not r2
    assert r1["meeting_id"] == "meeting_a"
    assert r2["meeting_id"] == "meeting_b"

    # 各自的重复调用是幂等的
    r1b = _close_meeting("meeting_a")
    assert r1 is r1b
    r2b = _close_meeting("meeting_b")
    assert r2 is r2b

    _finalized_meetings.clear()


# ── HTTP 端点测试 — v0.9.0 旧 Handler 已删除, 由 FastAPI E2E 覆盖 ──
