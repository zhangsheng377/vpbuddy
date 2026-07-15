"""task_manager — per-meeting 后台任务队列 + debounce (v0.9.0 #5)

设计:
- per-meeting 单任务队列 (debounce: 新 task 替换旧 task)
- generation_id 递增, 写入前检查防止旧任务超时后覆盖
- global ThreadPoolExecutor (bounded, reusable)
- 任务状态: queued / running / completed / timed_out / cancelled
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


class DocTaskStatus(Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


@dataclass
class DocTask:
    """一次文档生成任务 (per-meeting 单任务队列)."""

    generation_id: int
    meeting_id: str
    created_at: float = field(default_factory=time.time)
    status: DocTaskStatus = DocTaskStatus.QUEUED
    future: Future | None = None
    timeout_sec: float = 120.0  # 文档生成超时 (s)
    result: Any = None
    error: str | None = None


class MeetingTaskQueue:
    """单个会议的任务队列 (debounce: 只保留最新; running 时 defer 不挤压)."""

    def __init__(self, meeting_id: str):
        self.meeting_id = meeting_id
        self.lock = threading.Lock()
        self.current_task: DocTask | None = None
        self._generation_counter = 0
        self._pending_runner: Callable | None = None

    def submit(self, executor: ThreadPoolExecutor, runner: Callable) -> DocTask | None:
        """提交新任务 (debounce: running 时 defer, 完成后自动 kick)."""
        with self.lock:
            if self.current_task is not None and self.current_task.status == DocTaskStatus.RUNNING:
                self._pending_runner = runner
                return None

            self._generation_counter += 1
            gen_id = self._generation_counter

            task = DocTask(generation_id=gen_id, meeting_id=self.meeting_id)
            self.current_task = task
            task.status = DocTaskStatus.RUNNING

            def _wrapped():
                try:
                    result = runner(gen_id, self.meeting_id)
                    with self.lock:
                        if self.current_task is not None and self.current_task.generation_id == gen_id:
                            self.current_task.result = result
                            self.current_task.status = DocTaskStatus.COMPLETED
                    return result
                except Exception as e:
                    with self.lock:
                        if self.current_task is not None and self.current_task.generation_id == gen_id:
                            self.current_task.error = str(e)
                            self.current_task.status = DocTaskStatus.TIMED_OUT
                    return None
                finally:
                    with self.lock:
                        pending = self._pending_runner
                        self._pending_runner = None
                    if pending is not None:
                        self.submit(executor, pending)

            task.future = executor.submit(_wrapped)
            return task

    def has_running(self) -> bool:
        with self.lock:
            return (self.current_task is not None
                    and self.current_task.status == DocTaskStatus.RUNNING)

    @property
    def generation_id(self) -> int:
        return self._generation_counter

    def is_stale(self, gen_id: int) -> bool:
        """给定的 generation_id 是否已过时 (有新任务替换了)."""
        with self.lock:
            return self.current_task is None or self.current_task.generation_id != gen_id


class DocTaskManager:
    """全局文档任务管理器.

    - per-meeting 单任务队列 (debounce)
    - 全局 ThreadPoolExecutor (bounded)
    - 自动清理结束会议的队列
    """

    def __init__(self, max_workers: int = 4):
        self.executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="doc-task",
        )
        self._queues: dict[str, MeetingTaskQueue] = {}
        self._lock = threading.Lock()

    def get_or_create_queue(self, meeting_id: str) -> MeetingTaskQueue:
        with self._lock:
            if meeting_id not in self._queues:
                self._queues[meeting_id] = MeetingTaskQueue(meeting_id)
            return self._queues[meeting_id]

    def submit(
        self,
        meeting_id: str,
        runner: Callable[[int, str], Any],
    ) -> DocTask | None:
        """提交新任务 (debounce). 若 meeting 有 running 任务则 defer, 返回 None."""
        queue = self.get_or_create_queue(meeting_id)
        return queue.submit(self.executor, runner)

    def has_running(self, meeting_id: str) -> bool:
        with self._lock:
            queue = self._queues.get(meeting_id)
            if queue is None:
                return False
            return queue.has_running()

    def cancel_meeting(self, meeting_id: str):
        """取消某会议的全部待处理任务."""
        with self._lock:
            queue = self._queues.get(meeting_id)
            if queue is not None:
                with queue.lock:
                    if queue.current_task is not None:
                        queue.current_task.status = DocTaskStatus.CANCELLED
                        queue.current_task = None

    def cleanup_meeting(self, meeting_id: str):
        """清理已完成会议的任务队列."""
        with self._lock:
            self._queues.pop(meeting_id, None)

    def get_status(self, meeting_id: str | None = None) -> dict:
        """获取任务队列状态 (可观测)."""
        with self._lock:
            if meeting_id:
                q = self._queues.get(meeting_id)
                if q is None:
                    return {"meeting_id": meeting_id, "task": None}
                t = q.current_task
                return {
                    "meeting_id": meeting_id,
                    "generation_id": q.generation_id,
                    "task": {
                        "status": t.status.value if t else None,
                        "generation_id": t.generation_id if t else None,
                        "created_at": t.created_at if t else None,
                        "elapsed_sec": round(time.time() - t.created_at, 1) if t else None,
                    } if t else None,
                }
            else:
                return {
                    mid: {
                        "generation_id": q.generation_id,
                        "current_status": q.current_task.status.value if q.current_task else None,
                    }
                    for mid, q in self._queues.items()
                }


# === 全局单例 ===
_doc_task_manager: DocTaskManager | None = None


def get_task_manager(max_workers: int = 4) -> DocTaskManager:
    global _doc_task_manager
    if _doc_task_manager is None:
        _doc_task_manager = DocTaskManager(max_workers=max_workers)
    return _doc_task_manager
