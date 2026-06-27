"""Realtime SSE server — 服务端向客户端推送实时转写和文档生成结果

设计:
- 基于 threading 的内存级 pub/sub, 无外部依赖
- 每个 meeting_id 有多个订阅者队列 (fan-out)
- SSE 端点: GET /api/meetings/{id}/events
- 推送方: _handle_stream_chunk 处理完后调用 push_event()
- 客户端: EventSource 连接, 接收 transcript-segment / doc-update / state-update 事件

用法:
    from vpbuddy.realtime_server import push_event
    push_event("MTG01", "transcript-segment", {"text": "...", "speaker_id": "..."})
"""
from __future__ import annotations
import json
import queue
import threading
import time
from collections import deque
from typing import Dict, Any, List

# meeting_id -> [subscriber_queue1, subscriber_queue2, ...]
_subscribers: Dict[str, List[queue.Queue]] = {}
_event_history: Dict[str, deque] = {}
_subscribers_lock = threading.Lock()

EVENT_TTL = 300
HISTORY_LIMIT = 500


def _add_subscriber(meeting_id: str) -> queue.Queue:
    """为 meeting_id 添加一个新订阅者, 返回其专属队列"""
    q = queue.Queue(maxsize=500)
    with _subscribers_lock:
        if meeting_id not in _subscribers:
            _subscribers[meeting_id] = []
        _subscribers[meeting_id].append(q)
    return q


def _remove_subscriber(meeting_id: str, q: queue.Queue) -> None:
    """移除订阅者"""
    with _subscribers_lock:
        if meeting_id in _subscribers:
            try:
                _subscribers[meeting_id].remove(q)
            except ValueError:
                pass
            if not _subscribers[meeting_id]:
                del _subscribers[meeting_id]


def push_event(meeting_id: str, event_type: str, payload: dict) -> int:
    """向指定 meeting_id 的所有客户端推送事件 (fan-out)

    Args:
        meeting_id: 会议 ID
        event_type: 事件类型, 如 "transcript-segment", "doc-update", "state-update"
        payload: 事件数据

    Returns:
        成功推送的订阅者数量
    """
    event = {
        "id": f"{int(time.time() * 1000)}-{event_type}",
        "type": event_type,
        "payload": payload,
        "timestamp": time.time(),
    }

    with _subscribers_lock:
        history = _event_history.setdefault(meeting_id, deque(maxlen=HISTORY_LIMIT))
        history.append(event)
        subs = list(_subscribers.get(meeting_id, []))

    sent = 0
    for q in subs:
        try:
            q.put_nowait(event)
            sent += 1
        except queue.Full:
            # 队列满, 丢弃最旧事件再放
            try:
                q.get_nowait()
                q.put_nowait(event)
                sent += 1
            except queue.Empty:
                pass
    return sent


def get_event_history(meeting_id: str, since_id: str | None = None, limit: int = 200) -> List[dict]:
    """返回会议近期事件历史, 用于断线重连后补偿。"""
    with _subscribers_lock:
        events = list(_event_history.get(meeting_id, []))
    if since_id:
        for idx, event in enumerate(events):
            if event.get("id") == since_id:
                events = events[idx + 1:]
                break
    return events[-limit:]


def get_subscriber_count(meeting_id: str) -> int:
    """获取指定会议的订阅者数量"""
    with _subscribers_lock:
        return len(_subscribers.get(meeting_id, []))


def close_meeting(meeting_id: str) -> int:
    """关闭某会议的所有 SSE 订阅者 (客户端 stop_capture 调)

    2026-06-27: 加这个 API 是因为旧实现 sse_generator 阻塞在 q.get(timeout=30),
    客户端 stop_capture 后不会主动关 SSE TCP, 服务端要等下一个 heartbeat/timeout
    (最长 120s) 才能退出 generator, 期间队列残留 → 内存泄漏 + 下次同 ID 重连残留旧事件。

    实现: 把所有订阅者队列都放入一个 _POISON 事件, generator 一收到立即 break。
    Returns: 关闭的订阅者数量。
    """
    _POISON = object()  # 哨兵, 不是 dict, generator 判断 isinstance 跳过
    with _subscribers_lock:
        subs = list(_subscribers.get(meeting_id, []))
        if meeting_id in _subscribers:
            del _subscribers[meeting_id]
        if meeting_id in _event_history:
            del _event_history[meeting_id]
    closed = 0
    for q in subs:
        try:
            q.put_nowait(_POISON)
            closed += 1
        except queue.Full:
            try:
                q.get_nowait()
                q.put_nowait(_POISON)
                closed += 1
            except Exception:
                pass
    return closed


def cleanup_meetings_without_subscribers() -> int:
    """清理没有订阅者的会议 (清理内存)"""
    with _subscribers_lock:
        removed = 0
        to_remove = [mid for mid, subs in _subscribers.items() if not subs]
        for mid in to_remove:
            del _subscribers[mid]
            removed += 1
        return removed


def _format_sse(event: dict) -> bytes:
    event_type = event.get("type", "message")
    payload = event.get("payload", {})
    event_id = event.get("id")
    chunks = []
    if event_id:
        chunks.append(f"id: {event_id}\n")
    chunks.append(f"event: {event_type}\n")
    chunks.append(f"data: {json.dumps(payload, ensure_ascii=False)}\n\n")
    return "".join(chunks).encode("utf-8")


def sse_generator(meeting_id: str, timeout: float = 30.0, last_event_id: str | None = None):
    """SSE 事件生成器, 供 HTTP handler 使用

    Yields SSE 格式的字节流:
        event: transcript-segment\n
        data: {...}\n\n
    """
    q = _add_subscriber(meeting_id)

    try:
        yield b"event: connected\n"
        yield f"data: {json.dumps({'meeting_id': meeting_id, 'subscribers': get_subscriber_count(meeting_id)}, ensure_ascii=False)}\n\n".encode("utf-8")

        # 先补发断线期间的历史事件
        for event in get_event_history(meeting_id, last_event_id, limit=200):
            if time.time() - event.get("timestamp", 0) <= EVENT_TTL:
                yield _format_sse(event)

        last_event_time = time.time()
        # 2026-06-27: heartbeat 用合法 JSON, 否则客户端 reqwest-eventsource 解析失败断开
        heartbeat_payload = json.dumps({"type": "heartbeat", "ts": time.time()}, ensure_ascii=False).encode("utf-8")
        timeout_payload = json.dumps({"type": "timeout"}, ensure_ascii=False).encode("utf-8")
        while True:
            try:
                event = q.get(timeout=timeout)
                # 2026-06-27: POISON = close_meeting() 发的哨兵, 收到立刻退出 generator
                if not isinstance(event, dict):
                    break
                if time.time() - event.get("timestamp", 0) > EVENT_TTL:
                    continue

                yield _format_sse(event)
                last_event_time = time.time()
            except queue.Empty:
                # 2026-06-27: 修复前 b"data: {}\n\n" 把字面 "{}" 当 JSON 推, 客户端解析失败断开
                yield b"event: heartbeat\n"
                yield b"data: " + heartbeat_payload + b"\n\n"
                last_event_time = time.time()
                if time.time() - last_event_time > 120:
                    yield b"event: timeout\n"
                    yield b"data: " + timeout_payload + b"\n\n"
                    break
    finally:
        _remove_subscriber(meeting_id, q)
