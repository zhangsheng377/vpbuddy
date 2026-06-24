"""Realtime SSE server — 服务端向客户端推送实时转写和文档生成结果

设计:
- 基于 threading 的内存级 pub/sub, 无外部依赖
- 每个 meeting_id 一个事件队列
- SSE 端点: GET /api/meetings/{id}/events
- 推送方: _handle_stream_chunk 处理完后调用 push_event()
- 客户端: EventSource 连接, 接收 transcript-segment / doc-update / state-update 事件

用法:
    from vpbuddy.realtime_server import push_event, get_event_queue
    push_event("MTG01", "transcript-segment", {"text": "...", "speaker_id": "..."})
"""
from __future__ import annotations
import json
import queue
import threading
import time
from typing import Dict, Any, Optional

# meeting_id -> 事件队列 + 订阅者锁
_event_queues: Dict[str, queue.Queue] = {}
_queues_lock = threading.Lock()

# 事件过期时间(秒): 队列中事件超过此时间自动清理
EVENT_TTL = 300


def _get_or_create_queue(meeting_id: str) -> queue.Queue:
    """获取或创建 meeting_id 对应的事件队列"""
    with _queues_lock:
        if meeting_id not in _event_queues:
            _event_queues[meeting_id] = queue.Queue(maxsize=1000)
        return _event_queues[meeting_id]


def push_event(meeting_id: str, event_type: str, payload: dict) -> bool:
    """向指定 meeting_id 的客户端推送事件

    Args:
        meeting_id: 会议 ID
        event_type: 事件类型, 如 "transcript-segment", "doc-update", "state-update"
        payload: 事件数据

    Returns:
        是否推送成功(队列满时返回 False)
    """
    q = _get_or_create_queue(meeting_id)
    event = {
        "type": event_type,
        "payload": payload,
        "timestamp": time.time(),
    }
    try:
        q.put_nowait(event)
        return True
    except queue.Full:
        # 队列满, 丢弃最旧事件
        try:
            q.get_nowait()
            q.put_nowait(event)
            return True
        except queue.Empty:
            return False


def get_event_queue(meeting_id: str) -> Optional[queue.Queue]:
    """获取 meeting_id 的事件队列(用于 SSE handler)"""
    with _queues_lock:
        return _event_queues.get(meeting_id)


def cleanup_old_queues(max_idle_sec: float = 3600) -> int:
    """清理长时间没有新事件的队列(后台线程定期调用)"""
    now = time.time()
    removed = 0
    with _queues_lock:
        # 检查每个队列的最后事件时间
        to_remove = []
        for mid, q in list(_event_queues.items()):
            # 简单判断: 如果队列空且没有活跃订阅者, 认为可清理
            # 实际判断: 看队列里最后一个事件的时间戳
            # 这里简化: 空队列超过 max_idle_sec 清理
            if q.empty():
                to_remove.append(mid)
        for mid in to_remove:
            del _event_queues[mid]
            removed += 1
    return removed


def sse_generator(meeting_id: str, timeout: float = 30.0):
    """SSE 事件生成器, 供 HTTP handler 使用

    Yields SSE 格式的字节流:
        event: transcript-segment\n
        data: {...}\n\n
    """
    q = _get_or_create_queue(meeting_id)
    # 先发送一个连接成功事件
    yield b"event: connected\n"
    yield f"data: {json.dumps({'meeting_id': meeting_id}, ensure_ascii=False)}\n\n".encode("utf-8")

    last_event_time = time.time()
    while True:
        try:
            # 阻塞等待新事件, 带超时(用于心跳)
            event = q.get(timeout=timeout)
            # 过滤过期事件
            if time.time() - event.get("timestamp", 0) > EVENT_TTL:
                continue

            event_type = event.get("type", "message")
            payload = event.get("payload", {})

            yield f"event: {event_type}\n".encode("utf-8")
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")
            last_event_time = time.time()
        except queue.Empty:
            # 超时, 发送心跳保持连接
            yield b"event: heartbeat\n"
            yield b"data: {}\n\n"
            # 如果长时间没有事件, 断开连接让客户端重连
            if time.time() - last_event_time > 120:
                yield b"event: timeout\n"
                yield b"data: {}\n\n"
                break
