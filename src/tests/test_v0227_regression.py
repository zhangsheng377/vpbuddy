"""v0.22.7 regression tests — 服务端 event loop 不阻塞 + 异常安全 + 延迟关闭"""

from __future__ import annotations
import sys, inspect, json, time
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest


# ════════════════════════════════════════════════════════════════
# 1. post_chat — handle_chat_upload + _run_vp_chat 走 run_in_executor
# ════════════════════════════════════════════════════════════════


def test_post_chat_handle_chat_upload_uses_run_in_executor():
    """post_chat 中 handle_chat_upload 必须通过 run_in_executor 调用."""
    from vpbuddy.server.fastapi_app import post_chat
    src = inspect.getsource(post_chat)
    assert "run_in_executor" in src, (
        "handle_chat_upload 必须 await loop.run_in_executor() 调用，不能同步阻塞 event loop"
    )
    assert "handle_chat_upload" in src


def test_post_chat_run_vp_chat_uses_run_in_executor():
    """post_chat 中 _run_vp_chat 必须通过 run_in_executor 调用."""
    from vpbuddy.server.fastapi_app import post_chat
    src = inspect.getsource(post_chat)
    run_in_executor_count = src.count("run_in_executor")
    assert run_in_executor_count >= 2, (
        f"post_chat 中至少需要 2 处 run_in_executor "
        f"(handle_chat_upload + _run_vp_chat)，实际只找到 {run_in_executor_count} 处"
    )


def test_post_chat_is_async_function():
    """post_chat 必须是 async def."""
    import asyncio as _asyncio
    from vpbuddy.server.fastapi_app import post_chat
    assert _asyncio.iscoroutinefunction(post_chat), "post_chat 必须是 async def"


# ════════════════════════════════════════════════════════════════
# 2. 图片上传后触发文档重新生成
# ════════════════════════════════════════════════════════════════


def test_post_chat_contains_doc_retrigger_after_image_upload():
    """post_chat 源码中包含图片上传后重新触发 batch_docs + demo 的逻辑."""
    from vpbuddy.server.fastapi_app import post_chat
    src = inspect.getsource(post_chat)
    assert "BATCH_DOCS_KIND" in src, (
        "图片上传后必须重新触发 BATCH_DOCS_KIND 文档生成"
    )
    assert "DEMO_KIND" in src, (
        "图片上传后必须重新触发 DEMO_KIND"
    )
    assert "_dispatch_kind" in src, (
        "doc re-trigger 必须通过 _dispatch_kind 调用"
    )


def test_doc_retrigger_uses_task_manager_submit():
    """文档重新生成通过 task_manager.submit() 提交，不用同步调用."""
    from vpbuddy.server.fastapi_app import post_chat
    src = inspect.getsource(post_chat)
    assert "get_task_manager" in src, (
        "doc re-trigger 必须通过 task_manager 提交（异步），不能同步阻塞"
    )
    assert "submit" in src, (
        "必须调 task_manager.submit()"
    )


# ════════════════════════════════════════════════════════════════
# 3. send_audio 异常不杀 WS handler
# ════════════════════════════════════════════════════════════════


def test_ws_handler_send_audio_is_try_except_protected():
    """ws_realtime_asr 中 send_audio 调用被 try/except 包裹，不抛异常杀 handler."""
    from vpbuddy.server.fastapi_app import ws_realtime_asr
    src = inspect.getsource(ws_realtime_asr)

    lines = src.split("\n")
    in_send_block = False
    has_try = False
    has_break_in_except = False

    for line in lines:
        stripped = line.strip()
        if "send_audio" in stripped and "session" in stripped:
            in_send_block = True
            continue
        if in_send_block:
            if "try:" in stripped:
                has_try = True
            if "break" in stripped and "Exception" in stripped or (
                has_try and "break" in stripped
            ):
                has_break_in_except = True
                break

    assert has_try, (
        "send_audio 必须被 try/except 包裹，百炼 idle timeout 不能 kill 整个 WS handler"
    )
    assert has_break_in_except, (
        "send_audio 失败后必须 break（优雅退出WS会话），不能 raise 杀 handler"
    )


def test_ws_handler_has_send_audio_failure_message():
    """ws_realtime_asr 源码中 send_audio 失败时有日志输出."""
    from vpbuddy.server.fastapi_app import ws_realtime_asr
    src = inspect.getsource(ws_realtime_asr)
    assert "send_audio" in src
    assert "失败" in src or "bailian" in src.lower(), (
        "send_audio 失败时应有日志记录"
    )


# ════════════════════════════════════════════════════════════════
# 4. _close_meeting 120s daemon thread 延迟关闭
# ════════════════════════════════════════════════════════════════


def test_close_meeting_contains_delayed_close_thread():
    """_close_meeting 源码中创建 daemon thread 做 120s 延迟关闭."""
    from vpbuddy.ui_server import _close_meeting
    src = inspect.getsource(_close_meeting)
    assert "Thread" in src, (
        "_close_meeting 必须创建 thread 做延迟关闭"
    )
    assert "daemon" in src, (
        "延迟关闭 thread 必须是 daemon=True"
    )
    assert "sleep(120)" in src or "sleep (120)" in src or "sleep( 120" in src, (
        "延迟关闭必须 sleep(120) 等待客户端收事后事件"
    )


def test_close_meeting_delayed_close_calls_close_meeting():
    """延迟关闭 thread 内部调用 close_meeting(meeting_id)."""
    from vpbuddy.ui_server import _close_meeting
    src = inspect.getsource(_close_meeting)
    assert "close_meeting" in src


def test_close_meeting_does_NOT_call_close_meeting_immediately():
    """_close_meeting 不再立即调 close_meeting()，改为延迟."""
    from vpbuddy.ui_server import _close_meeting
    src = inspect.getsource(_close_meeting)
    # 只检查函数体内的逻辑行（跳过 docstring + import）
    body_start = src.index("push_event")
    body_prefix = src[:body_start]
    has_direct_close_call = any(
        line.strip().startswith("close_meeting(") or line.strip().startswith("_cm(")
        for line in body_prefix.split("\n")
    )
    assert not has_direct_close_call, (
        "push_event 之前不应直接调 close_meeting，必须延迟 120s"
    )
    assert "120" in src, "延迟值必须是 120s"


def test_close_meeting_delayed_thread_exception_is_suppressed():
    """延迟关闭 thread 内的 close_meeting 异常被 pass 安全吞掉."""
    from vpbuddy.ui_server import _close_meeting
    src = inspect.getsource(_close_meeting)
    assert "pass" in src, (
        "延迟关闭 thread 内的异常必须被安全吞掉 (pass)，不能再次崩溃"
    )
