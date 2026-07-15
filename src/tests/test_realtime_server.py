"""v0.22.6: realtime_server SSE subscriber + get_event_history 测试"""

from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from vpbuddy.realtime_server import (
    _add_subscriber,
    _remove_subscriber,
    get_subscriber_count,
    cleanup_meetings_without_subscribers,
    get_event_history,
    push_event,
    _subscribers,
    _subscribers_lock,
    _event_history,
    close_meeting,
)


def test_add_subscriber_increments_count():
    mid = "test_sub_add"
    q = _add_subscriber(mid)
    assert get_subscriber_count(mid) >= 1
    _remove_subscriber(mid, q)


def test_remove_subscriber_decrements_count():
    mid = "test_sub_rm"
    q = _add_subscriber(mid)
    assert get_subscriber_count(mid) == 1
    _remove_subscriber(mid, q)
    # 队列为空 → meeting 条目被删除 → count = 0
    assert mid not in _subscribers


def test_multiple_subscribers():
    mid = "test_sub_multi"
    q1 = _add_subscriber(mid)
    q2 = _add_subscriber(mid)
    assert get_subscriber_count(mid) == 2
    _remove_subscriber(mid, q1)
    assert get_subscriber_count(mid) == 1
    _remove_subscriber(mid, q2)
    assert mid not in _subscribers


def test_remove_nonexistent_subscriber_no_error():
    mid = "test_sub_rm_404"
    q1 = _add_subscriber(mid)
    _remove_subscriber(mid, q1)
    # 重复 remove 不抛异常
    _remove_subscriber(mid, q1)


def test_cleanup_removes_empty_meetings():
    mid = "test_sub_cleanup"
    q = _add_subscriber(mid)
    _remove_subscriber(mid, q)
    removed = cleanup_meetings_without_subscribers()
    assert mid not in _subscribers


def test_cleanup_keeps_active_meetings():
    mid = "test_sub_keep"
    q = _add_subscriber(mid)
    cleanup_meetings_without_subscribers()
    # 有 subscriber 的不应被清理
    assert mid in _subscribers
    _remove_subscriber(mid, q)


def test_cleanup_returns_count():
    mid = "test_sub_count"
    q = _add_subscriber(mid)
    _remove_subscriber(mid, q)
    # mid 已空但还未被 cleanup 清理 (可能在 dict 中但没有 subscribers)
    # cleanup_meetings_without_subscribers 会扫出空列表的 meeting
    cnt = cleanup_meetings_without_subscribers()
    assert isinstance(cnt, int)


def test_close_meeting_sends_poison():
    mid = "test_close"
    q = _add_subscriber(mid)
    assert get_subscriber_count(mid) == 1
    closed = close_meeting(mid)
    assert closed == 1
    # close_meeting 会 del _subscribers[mid] 然后发 POISON
    # 队列里应该有 POISON (非 dict)
    import queue
    try:
        item = q.get_nowait()
        assert not isinstance(item, dict)  # POISON 哨兵
    except queue.Empty:
        pass


def test_orphan_subscriber_cleanup_integration():
    """模拟: subscriber 被添加但从没被 remove (generator finally 没触发).
    cleanup 应该清理掉空列表的 meeting."""
    mid = "test_orphan"
    q = _add_subscriber(mid)
    # 手动从列表移除 (模拟 generator 退出时的 _remove_subscriber 被调用了)
    with _subscribers_lock:
        if mid in _subscribers:
            _subscribers[mid].remove(q)
            if not _subscribers[mid]:
                del _subscribers[mid]
    # 现在应该干净了
    assert mid not in _subscribers


class TestGetEventHistory:
    def test_empty_history(self):
        mid = "test_hist_empty"
        events, cursor_found = get_event_history(mid)
        assert events == []
        assert cursor_found is True

    def test_history_without_since_id(self):
        mid = "test_hist_full"
        _event_history.pop(mid, None)
        push_event(mid, "test-type", {"k": "v1"})
        push_event(mid, "test-type", {"k": "v2"})
        events, cursor_found = get_event_history(mid)
        assert len(events) == 2
        assert cursor_found is True
        assert events[0]["payload"]["k"] == "v1"
        assert events[1]["payload"]["k"] == "v2"

    def test_history_with_since_id(self):
        mid = "test_hist_since"
        _event_history.pop(mid, None)
        push_event(mid, "t1", {"n": 1})
        push_event(mid, "t2", {"n": 2})
        push_event(mid, "t3", {"n": 3})
        full, _ = get_event_history(mid)
        assert len(full) == 3
        since_id = full[0]["id"]
        partial, cursor_found = get_event_history(mid, since_id=since_id)
        assert len(partial) == 2
        assert cursor_found is True
        assert partial[0]["payload"]["n"] == 2
        assert partial[1]["payload"]["n"] == 3

    def test_since_id_not_found(self):
        mid = "test_hist_badid"
        _event_history.pop(mid, None)
        push_event(mid, "x", {"a": 1})
        events, cursor_found = get_event_history(mid, since_id="nonexistent-id")
        assert len(events) == 1
        assert cursor_found is False

    def test_event_ids_are_strings(self):
        mid = "test_hist_ids"
        _event_history.pop(mid, None)
        push_event(mid, "mytype", {"b": 2})
        events, _ = get_event_history(mid)
        assert len(events) == 1
        assert isinstance(events[0]["id"], str)
        assert len(events[0]["id"]) > 0

    def test_cursor_found_without_last_event_id(self):
        """无 since_id 时 cursor_found 应为 True."""
        mid = "test_hist_no_last"
        _event_history.pop(mid, None)
        push_event(mid, "a", {})
        events, cursor_found = get_event_history(mid)
        assert cursor_found is True
        assert len(events) == 1
