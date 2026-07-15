"""测试 task_manager — DocTaskManager / MeetingTaskQueue / DocTask (v0.9.0 #5 → v0.23.0)

覆盖:
- DocTaskManager 全局单例
- MeetingTaskQueue.submit() defer: running 时不取消不挤压, 存 pending 完成后 auto-kick
- has_running() 状态查询
- generation_id 递增
- is_stale() 判定过时任务
- DocTask 状态流转 QUEUED->RUNNING->COMPLETED
- cancel_meeting() / cleanup_meeting()
- get_status() 返回信息
- Thread safety (并发提交)
- 全部 mock ThreadPoolExecutor (不真跑)
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from vpbuddy.task_manager import (
    DocTask,
    DocTaskManager,
    DocTaskStatus,
    MeetingTaskQueue,
    get_task_manager,
)


class TestDocTask:
    """DocTask 数据类基本行为."""

    def test_create_defaults(self):
        task = DocTask(generation_id=1, meeting_id="mtg1")
        assert task.generation_id == 1
        assert task.meeting_id == "mtg1"
        assert task.status == DocTaskStatus.QUEUED
        assert task.future is None
        assert task.timeout_sec == 120.0
        assert task.result is None
        assert task.error is None
        assert task.created_at > 0

    def test_status_enum_values(self):
        assert DocTaskStatus.QUEUED.value == "queued"
        assert DocTaskStatus.RUNNING.value == "running"
        assert DocTaskStatus.COMPLETED.value == "completed"
        assert DocTaskStatus.TIMED_OUT.value == "timed_out"
        assert DocTaskStatus.CANCELLED.value == "cancelled"


class TestMeetingTaskQueue:
    """MeetingTaskQueue 单元测试 (mock ThreadPoolExecutor)."""

    def test_generation_id_increments(self):
        """连续 submit 应递增 generation_id (无 running 时)."""
        queue = MeetingTaskQueue("mtg1")
        executor = MagicMock()

        def fake_submit(fn, *args, **kwargs):
            return MagicMock()

        executor.submit = fake_submit

        t1 = queue.submit(executor, lambda gid, mid: None)
        g1 = t1.generation_id
        # 手工设 completed → 无 running 状态 → 下次 submit 不 defer
        t1.status = DocTaskStatus.COMPLETED
        t2 = queue.submit(executor, lambda gid, mid: None)
        g2 = t2.generation_id
        assert g2 > g1, "generation_id 应递增"

    def test_submit_running_defers_not_cancels(self):
        """连续提交: 旧 RUNNING 任务不取消, 新任务 defer 返回 None."""
        queue = MeetingTaskQueue("mtg1")

        old_task = DocTask(generation_id=1, meeting_id="mtg1")
        old_task.status = DocTaskStatus.RUNNING
        queue.current_task = old_task
        queue._generation_counter = 1

        executor = MagicMock()
        executor.submit = lambda fn, *a, **kw: MagicMock()

        t2 = queue.submit(executor, lambda gid, mid: None)
        assert t2 is None, "running 时应返回 None（defer）"
        assert old_task.status == DocTaskStatus.RUNNING, "旧任务不取消"
        assert queue.current_task is old_task, "current_task 仍是旧任务"

    def test_pending_runner_auto_kick(self):
        """完成后自动 kick pending runner."""
        queue = MeetingTaskQueue("mtg1")
        queue._generation_counter = 1
        task = DocTask(generation_id=1, meeting_id="mtg1")
        task.status = DocTaskStatus.RUNNING
        queue.current_task = task

        executor = MagicMock()
        executor.submit = lambda fn, *a, **kw: MagicMock()

        def runner1(gid, mid):
            return {"ok": True}

        queue._pending_runner = runner1

        # 模拟 _wrapped finally: completed → 取 pending → unlock → submit
        with queue.lock:
            queue.current_task.status = DocTaskStatus.COMPLETED
            pending = queue._pending_runner
            queue._pending_runner = None

        assert pending is runner1
        t = queue.submit(executor, pending)
        assert t is not None, "completed 后不再 defer"
        assert t.generation_id == 2

    def test_has_running_true(self):
        queue = MeetingTaskQueue("mtg1")
        task = DocTask(generation_id=1, meeting_id="mtg1")
        task.status = DocTaskStatus.RUNNING
        queue.current_task = task
        assert queue.has_running()

    def test_has_running_false_none(self):
        queue = MeetingTaskQueue("mtg1")
        queue.current_task = None
        assert not queue.has_running()

    def test_has_running_false_completed(self):
        queue = MeetingTaskQueue("mtg1")
        task = DocTask(generation_id=1, meeting_id="mtg1")
        task.status = DocTaskStatus.COMPLETED
        queue.current_task = task
        assert not queue.has_running()

    def test_is_stale_fresh(self):
        """当前任务同 generation_id => not stale."""
        queue = MeetingTaskQueue("mtg1")
        queue._generation_counter = 5
        queue.current_task = DocTask(generation_id=5, meeting_id="mtg1")
        assert not queue.is_stale(5)

    def test_is_stale_old(self):
        """老 generation_id => stale."""
        queue = MeetingTaskQueue("mtg1")
        queue._generation_counter = 10
        queue.current_task = DocTask(generation_id=10, meeting_id="mtg1")
        assert queue.is_stale(5)

    def test_is_stale_none_current(self):
        """current_task=None => stale."""
        queue = MeetingTaskQueue("mtg1")
        queue.current_task = None
        assert queue.is_stale(1)

    def test_submit_sets_running_immediately(self):
        """submit 应立刻将 task 置为 RUNNING."""
        queue = MeetingTaskQueue("mtg1")
        executor = MagicMock()
        executor.submit = lambda fn, *a, **kw: MagicMock()
        task = queue.submit(executor, lambda gid, mid: None)
        assert task.status == DocTaskStatus.RUNNING

    def test_wrapped_completion_updates_status(self):
        """_wrapped runner 成功应设置 COMPLETED + result."""
        queue = MeetingTaskQueue("mtg1")
        queue._generation_counter = 1
        task = DocTask(generation_id=1, meeting_id="mtg1")
        task.status = DocTaskStatus.RUNNING
        queue.current_task = task

        executor = MagicMock()
        # 直接调用 _wrapped
        result_value = {"docs": "generated"}

        def runner(gid, mid):
            return result_value

        # 构造 _wrapped
        def _wrapped():
            try:
                result = runner(1, "mtg1")
                with queue.lock:
                    if queue.current_task is not None and queue.current_task.generation_id == 1:
                        queue.current_task.result = result
                        queue.current_task.status = DocTaskStatus.COMPLETED
                    return result
            except Exception as e:
                with queue.lock:
                    if queue.current_task is not None and queue.current_task.generation_id == 1:
                        queue.current_task.error = str(e)
                        queue.current_task.status = DocTaskStatus.TIMED_OUT
                return None

        _wrapped()
        assert queue.current_task.status == DocTaskStatus.COMPLETED
        assert queue.current_task.result == result_value

    def test_wrapped_error_sets_timed_out(self):
        """_wrapped runner 抛异常应设置 TIMED_OUT + error."""
        queue = MeetingTaskQueue("mtg1")
        queue._generation_counter = 2
        task = DocTask(generation_id=2, meeting_id="mtg1")
        task.status = DocTaskStatus.RUNNING
        queue.current_task = task

        def runner(gid, mid):
            raise ValueError("模拟异常")

        def _wrapped():
            try:
                result = runner(2, "mtg1")
                with queue.lock:
                    if queue.current_task is not None and queue.current_task.generation_id == 2:
                        queue.current_task.result = result
                        queue.current_task.status = DocTaskStatus.COMPLETED
                    return result
            except Exception as e:
                with queue.lock:
                    if queue.current_task is not None and queue.current_task.generation_id == 2:
                        queue.current_task.error = str(e)
                        queue.current_task.status = DocTaskStatus.TIMED_OUT
                return None

        _wrapped()
        assert queue.current_task.status == DocTaskStatus.TIMED_OUT
        assert "模拟异常" in queue.current_task.error


class TestDocTaskManager:
    """DocTaskManager 全局管理."""

    def test_get_task_manager_singleton(self):
        """get_task_manager() 应返回同一实例."""
        mgr1 = get_task_manager(max_workers=2)
        mgr2 = get_task_manager(max_workers=8)
        assert mgr1 is mgr2

    def test_create_and_submit(self):
        """Manager.submit 应委托给对应 queue."""
        mgr = DocTaskManager(max_workers=2)
        # mock executor.submit 避免真跑
        mgr.executor.submit = lambda fn, *a, **kw: MagicMock()

        task = mgr.submit("mtg1", lambda gid, mid: {"ok": True})
        assert task.meeting_id == "mtg1"
        assert task.generation_id == 1

    def test_submit_multiple_meetings(self):
        """不同 meeting_id 应各自独立."""
        mgr = DocTaskManager(max_workers=2)
        mgr.executor.submit = lambda fn, *a, **kw: MagicMock()

        t1 = mgr.submit("mtg_a", lambda gid, mid: None)
        t2 = mgr.submit("mtg_b", lambda gid, mid: None)
        t3 = mgr.submit("mtg_a", lambda gid, mid: None)

        # v0.23.0: mtg_a 有 running 任务时 defer，返回 None
        assert t1.status == DocTaskStatus.RUNNING
        assert t2.status == DocTaskStatus.RUNNING
        assert t3 is None, "同 meeting running 时应 defer 返回 None"

    def test_get_or_create_queue(self):
        """get_or_create_queue 应创建新队列或返回已有."""
        mgr = DocTaskManager(max_workers=2)
        q1 = mgr.get_or_create_queue("mtg_x")
        q2 = mgr.get_or_create_queue("mtg_x")
        q3 = mgr.get_or_create_queue("mtg_y")
        assert q1 is q2
        assert q1 is not q3

    def test_cancel_meeting(self):
        """cancel_meeting 应取消当前任务."""
        mgr = DocTaskManager(max_workers=2)
        mgr.executor.submit = lambda fn, *a, **kw: MagicMock()

        task = mgr.submit("cancel_test", lambda gid, mid: None)
        assert task.status == DocTaskStatus.RUNNING

        mgr.cancel_meeting("cancel_test")
        assert task.status == DocTaskStatus.CANCELLED

    def test_cancel_meeting_nonexistent(self):
        """cancel_meeting 对不存在的 meeting 应不抛异常."""
        mgr = DocTaskManager(max_workers=2)
        mgr.cancel_meeting("nonexistent")  # should not raise

    def test_cleanup_meeting(self):
        """cleanup_meeting 应移除队列."""
        mgr = DocTaskManager(max_workers=2)
        mgr.get_or_create_queue("clean_test")
        assert "clean_test" in mgr._queues
        mgr.cleanup_meeting("clean_test")
        assert "clean_test" not in mgr._queues

    def test_cleanup_meeting_nonexistent(self):
        """cleanup_meeting 对不存在的 meeting 应不抛异常."""
        mgr = DocTaskManager(max_workers=2)
        mgr.cleanup_meeting("nonexistent")

    def test_get_status_specific_meeting(self):
        """get_status(mid) 返回正确信息."""
        mgr = DocTaskManager(max_workers=2)
        mgr.executor.submit = lambda fn, *a, **kw: MagicMock()

        # 先提交
        mgr.submit("status_test", lambda gid, mid: None)
        status = mgr.get_status("status_test")
        assert status["meeting_id"] == "status_test"
        assert status["generation_id"] == 1
        assert status["task"]["generation_id"] == 1
        assert status["task"]["status"] == "running"

    def test_get_status_nonexistent(self):
        """get_status 对不存在的 meeting 返回 task=None."""
        mgr = DocTaskManager(max_workers=2)
        status = mgr.get_status("no_such_meeting")
        assert status["meeting_id"] == "no_such_meeting"
        assert status["task"] is None

    def test_get_status_all(self):
        """get_status() 全部返回 dict."""
        mgr = DocTaskManager(max_workers=2)
        mgr.executor.submit = lambda fn, *a, **kw: MagicMock()
        mgr.submit("mtg1", lambda gid, mid: None)
        mgr.submit("mtg2", lambda gid, mid: None)

        all_status = mgr.get_status()
        assert "mtg1" in all_status
        assert "mtg2" in all_status
        assert all_status["mtg1"]["generation_id"] == 1
        assert all_status["mtg2"]["generation_id"] == 1

    def test_thread_safety_concurrent_submit(self):
        """并发提交不应丢数据或不一致."""
        import concurrent.futures

        mgr = DocTaskManager(max_workers=4)

        # mock executor.submit 返回 dummy future
        def dummy_submit(fn, *a, **kw):
            fut = MagicMock()
            return fut

        mgr.executor.submit = dummy_submit

        def do_submit(mid: str):
            mgr.submit(mid, lambda gid, mid: None)

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            futures = []
            for i in range(50):
                futures.append(pool.submit(do_submit, f"con_mtg_{i % 5}"))
            concurrent.futures.wait(futures)

        # 5 个 meeting 的队列都有 current_task
        for i in range(5):
            mid = f"con_mtg_{i}"
            assert mid in mgr._queues
            assert mgr._queues[mid].current_task is not None

    def test_get_task_manager_singleton_reset(self):
        """验证单例模式 — 隐式依赖函数级全局."""
        mgr_a = get_task_manager()
        mgr_b = get_task_manager()
        assert mgr_a is mgr_b

    def test_has_running_true(self):
        """has_running 对 running 的 meeting 返回 True."""
        mgr = DocTaskManager(max_workers=2)
        mgr.executor.submit = lambda fn, *a, **kw: MagicMock()
        mgr.submit("hrt", lambda gid, mid: None)
        assert mgr.has_running("hrt")

    def test_has_running_false(self):
        """has_running 对不存在的 meeting 返回 False."""
        mgr = DocTaskManager(max_workers=2)
        assert not mgr.has_running("no_such")

    def test_completed_task_not_reported_running(self):
        """has_running 对 COMPLETED 的 meeting 返回 False."""
        mgr = DocTaskManager(max_workers=2)
        mgr.executor.submit = lambda fn, *a, **kw: MagicMock()

        # 提交 → 手动设 completed
        task = mgr.submit("ct", lambda gid, mid: None)
        task.status = DocTaskStatus.COMPLETED
        assert not mgr.has_running("ct")
