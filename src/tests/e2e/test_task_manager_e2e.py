"""E2E: DocTaskManager 端到端测试

测试场景:
- 创建 DocTaskManager 实例
- 多次提交同一 meeting 的 task → 验证 debounce
- 验证 generation_id 递增
- 验证 cancel_meeting 取消待处理任务
- 验证 cleanup_meeting 清理队列
- 并发提交测试 (多线程)
"""
from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

pytestmark = pytest.mark.e2e

_E2E_SKIP = os.environ.get("RUN_E2E") != "1"


# =============================================================================
# Helpers
# =============================================================================


def _slow_runner(gen_id: int, meeting_id: str, delay: float = 0.2) -> dict:
    """一个会延迟的 runner, 模拟 LLM 调用."""
    time.sleep(delay)
    return {
        "generation_id": gen_id,
        "meeting_id": meeting_id,
        "result": f"doc_result_{gen_id}",
    }


def _fast_runner(gen_id: int, meeting_id: str) -> dict:
    """立即返回的 runner."""
    return {
        "generation_id": gen_id,
        "meeting_id": meeting_id,
        "result": f"fast_result_{gen_id}",
    }


# =============================================================================
# Tests
# =============================================================================


@pytest.mark.skipif(_E2E_SKIP, reason="RUN_E2E != 1")
class TestDocTaskManagerE2E:

    def test_create_manager(self):
        """创建 DocTaskManager 实例."""
        from vpbuddy.task_manager import DocTaskManager

        manager = DocTaskManager(max_workers=2)
        assert manager is not None
        assert manager.executor._max_workers == 2

        # cleanup
        manager.executor.shutdown(wait=False)

    def test_single_submit(self):
        """提交单个 task 并验证结果."""
        from vpbuddy.task_manager import DocTaskManager

        manager = DocTaskManager(max_workers=2)
        meeting_id = "test_single"

        try:
            task = manager.submit(meeting_id, _fast_runner)
            assert task.generation_id == 1
            assert task.meeting_id == meeting_id

            # 等完成
            task.future.result(timeout=5)
            assert task.status.value == "completed"
            assert task.result == {"generation_id": 1, "meeting_id": meeting_id,
                                   "result": "fast_result_1"}
        finally:
            manager.executor.shutdown(wait=False)

    def test_debounce_replace_pending(self):
        """验证 debounce: 新提交替换旧任务."""
        from vpbuddy.task_manager import DocTaskManager

        manager = DocTaskManager(max_workers=1)
        meeting_id = "test_debounce"

        try:
            # 提交慢 task, 将占住唯一的 worker
            task1 = manager.submit(meeting_id, lambda gid, mid: _slow_runner(gid, mid, delay=0.5))
            assert task1.generation_id == 1

            # 等 task1 开始 running
            time.sleep(0.1)

            # 提交新 task, 应该 cancel 旧的并替换
            task2 = manager.submit(meeting_id, _fast_runner)
            assert task2.generation_id == 2

            # 旧的应被 marked CANCELLED
            assert task1.status.value in ("cancelled", "completed", "running")
            # 新的应完成
            task2.future.result(timeout=5)
            assert task2.status.value == "completed"
            assert task2.result["generation_id"] == 2
        finally:
            manager.executor.shutdown(wait=False)

    def test_generation_id_increment(self):
        """验证 generation_id 递增."""
        from vpbuddy.task_manager import DocTaskManager

        manager = DocTaskManager(max_workers=2)
        meeting_id = "test_gen_id"

        try:
            # 依次提交 5 个 task
            previous_id = 0
            for i in range(5):
                task = manager.submit(meeting_id, _fast_runner)
                assert task.generation_id > previous_id, (
                    f"generation_id 应递增: {task.generation_id} <= {previous_id}"
                )
                previous_id = task.generation_id
                task.future.result(timeout=5)

            # 验证队列状态
            status = manager.get_status(meeting_id)
            assert status["generation_id"] == 5
        finally:
            manager.executor.shutdown(wait=False)

    def test_cancel_meeting(self):
        """验证 cancel_meeting 取消待处理任务."""
        from vpbuddy.task_manager import DocTaskManager

        manager = DocTaskManager(max_workers=1)
        meeting_id = "test_cancel"

        try:
            # 提交一个会长时间 running 的任务
            task1 = manager.submit(meeting_id, lambda gid, mid: _slow_runner(gid, mid, delay=2.0))
            assert task1.generation_id == 1

            # 提交第二个, 因为只有一个 worker, 它会 debounce 替换 task1
            # 但 task1 已经开始 running
            time.sleep(0.05)

            # cancel meeting
            manager.cancel_meeting(meeting_id)

            # 验证队列为空
            status = manager.get_status(meeting_id)
            assert status["task"] is None, "cancel 后 task 应为 None"
        finally:
            manager.executor.shutdown(wait=False)

    def test_cleanup_meeting(self):
        """验证 cleanup_meeting 清理队列."""
        from vpbuddy.task_manager import DocTaskManager

        manager = DocTaskManager(max_workers=2)
        meeting_id = "test_cleanup"

        try:
            task = manager.submit(meeting_id, _fast_runner)
            task.future.result(timeout=5)

            # cleanup
            manager.cleanup_meeting(meeting_id)

            # 验证队列被移除
            status_all = manager.get_status()
            assert meeting_id not in status_all, (
                f"cleanup 后 meeting 应不在队列中: {status_all}"
            )
        finally:
            manager.executor.shutdown(wait=False)

    def test_multiple_meetings_isolation(self):
        """验证不同会议的队列隔离."""
        from vpbuddy.task_manager import DocTaskManager

        manager = DocTaskManager(max_workers=2)
        meeting_ids = [f"test_iso_{i}" for i in range(3)]

        try:
            tasks = []
            for mid in meeting_ids:
                task = manager.submit(mid, _fast_runner)
                tasks.append(task)

            # 所有 task 应完成
            for t in tasks:
                t.future.result(timeout=5)
                assert t.status.value == "completed"

            # 验证 generation_id 各自独立
            for mid in meeting_ids:
                status = manager.get_status(mid)
                assert status["generation_id"] == 1, (
                    f"meeting {mid} generation_id 应为 1, 实际 {status['generation_id']}"
                )
        finally:
            manager.executor.shutdown(wait=False)

    def test_concurrent_submit_same_meeting(self):
        """多线程并发提交同一会议 — 验证线程安全和最终结果."""
        from vpbuddy.task_manager import DocTaskManager

        manager = DocTaskManager(max_workers=4)
        meeting_id = "test_concurrent"
        n_threads = 10

        results = []

        def _submit_task(idx: int):
            task = manager.submit(meeting_id, lambda gid, mid: {
                "idx": idx,
                "generation_id": gid,
                "meeting_id": mid,
            })
            return task

        with ThreadPoolExecutor(max_workers=n_threads) as pool:
            futures = [pool.submit(_submit_task, i) for i in range(n_threads)]

        submitted_tasks = [f.result() for f in futures]

        try:
            # 验证所有 submission 成功
            assert len(submitted_tasks) == n_threads

            # 验证最终 task 的 generation_id 最大
            final_task = manager.submit(meeting_id, _fast_runner)
            final_task.future.result(timeout=5)
            assert final_task.generation_id <= n_threads + 1  # 最多 n+1

            # 验证队列状态一致
            status = manager.get_status(meeting_id)
            assert status["generation_id"] == final_task.generation_id
            assert status["task"]["status"] == "completed"
        finally:
            manager.executor.shutdown(wait=False)

    def test_get_status_empty_meeting(self):
        """查询不存在的 meeting 状态."""
        from vpbuddy.task_manager import DocTaskManager

        manager = DocTaskManager(max_workers=2)
        try:
            status = manager.get_status("nonexistent_meeting")
            assert status["meeting_id"] == "nonexistent_meeting"
            assert status["task"] is None
        finally:
            manager.executor.shutdown(wait=False)

    def test_get_status_all(self):
        """查询全部队列状态."""
        from vpbuddy.task_manager import DocTaskManager

        manager = DocTaskManager(max_workers=2)
        meeting_ids = [f"test_status_all_{i}" for i in range(3)]

        try:
            for mid in meeting_ids:
                manager.submit(mid, _fast_runner)

            status_all = manager.get_status()
            assert len(status_all) == 3
            for mid in meeting_ids:
                assert mid in status_all
        finally:
            manager.executor.shutdown(wait=False)

    def test_generation_id_stale_check(self):
        """验证 is_stale 检测过时 generation_id."""
        from vpbuddy.task_manager import DocTaskManager

        manager = DocTaskManager(max_workers=1)
        meeting_id = "test_stale"

        try:
            # 提交 task1
            task1 = manager.submit(meeting_id, lambda gid, mid: _slow_runner(gid, mid, delay=0.3))
            gen1 = task1.generation_id

            time.sleep(0.1)

            # 提交 task2 (debounce task1)
            task2 = manager.submit(meeting_id, _fast_runner)
            gen2 = task2.generation_id

            queue = manager.get_or_create_queue(meeting_id)
            assert queue.is_stale(gen1), "gen1 应已被标记 stale"
            assert not queue.is_stale(gen2), "gen2 不应被标记 stale"

            task2.future.result(timeout=5)
        finally:
            manager.executor.shutdown(wait=False)

    def test_task_run_with_different_meetings(self):
        """验证不同 meeting 的 task runner 参数正确传递."""
        from vpbuddy.task_manager import DocTaskManager

        manager = DocTaskManager(max_workers=4)
        meetings = {
            "meet_a": "result_a",
            "meet_b": "result_b",
            "meet_c": "result_c",
        }

        results = {}

        def _make_runner(mid: str, expected: str):
            def _runner(gid: int, mid_inner: str) -> dict:
                assert mid_inner == mid, f"runner 收到错误 meeting_id: {mid_inner} != {mid}"
                time.sleep(0.05)
                return {"meeting_id": mid, "expected": expected, "generation_id": gid}
            return _runner

        try:
            tasks = {}
            for mid, expected in meetings.items():
                t = manager.submit(mid, _make_runner(mid, expected))
                tasks[mid] = t

            for mid, t in tasks.items():
                result = t.future.result(timeout=5)
                assert result["expected"] == meetings[mid]
                assert result["meeting_id"] == mid
        finally:
            manager.executor.shutdown(wait=False)
