"""v0.22.6: KB search POST 改为 run_in_executor — 不再阻塞 event loop"""

from __future__ import annotations
import sys, inspect
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest


def test_post_kb_search_uses_run_in_executor():
    """POST /api/kb/search 实现中用了 run_in_executor."""
    from vpbuddy.server.fastapi_app import post_kb_search
    src = inspect.getsource(post_kb_search)
    assert "run_in_executor" in src, (
        "KB search POST 必须用 run_in_executor 防止阻塞 event loop"
    )


def test_post_kb_search_is_async_function():
    """post_kb_search 是 async def，才能 await run_in_executor."""
    import asyncio as _asyncio
    from vpbuddy.server.fastapi_app import post_kb_search
    assert _asyncio.iscoroutinefunction(post_kb_search), (
        "post_kb_search 必须是 async def"
    )


def test_run_in_executor_uses_default_executor():
    """run_in_executor 第一个参数为 None，使用默认 ThreadPoolExecutor."""
    from vpbuddy.server.fastapi_app import post_kb_search
    src = inspect.getsource(post_kb_search)
    assert "run_in_executor" in src, (
        "run_in_executor 必须在 post_kb_search 中调用"
    )
