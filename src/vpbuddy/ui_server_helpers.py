"""UI server 辅助模块 — 2026-06-28 ADR-0018

放一些跨 handler 复用的小函数, 避免 ui_server.py 越改越大。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List

logger = logging.getLogger(__name__)


def check_all_docs_stored_and_close(meeting_id: str, doc_kinds: List[str] | None = None) -> bool:
    """检查 6 个文档是否全部 stored (文件存在且非空), 是则:
    1. push_event("meeting-complete", {...}) — 让客户端收到 final 信号
    2. close_meeting(meeting_id) — 服务端 SSE 退出, 客户端自然断

    Returns: True if all 6 docs exist, False otherwise.

    2026-06-28: 张胜东决策 — 客户端 stop_capture 不再关 SSE, 让 SSE 继续
    接收 GPU 后台生成的 6 docs. 当 6 docs 全部 stored, GPU 端主动
    close_meeting 触发 SSE 退出. 客户端 UI 收到 meeting-complete 后
    显示"会议已完成".

    注意: 直接 import ui_server.DOCS_DIR (相对路径变量), 不复制路径
    避免双份真相 (DRY).
    """
    # 2026-06-28: 动态 import 避免循环 (ui_server 在某些场景也 import helpers)
    from .ui_server import DOCS_DIR
    if doc_kinds is None:
        doc_kinds = ["req", "arch", "tasks", "api", "risk", "demo"]
    meeting_dir = Path(DOCS_DIR) / meeting_id
    all_stored = True
    sizes = {}
    for kind in doc_kinds:
        path = meeting_dir / f"{kind}.md"
        if not path.exists() or path.stat().st_size == 0:
            all_stored = False
            sizes[kind] = 0
        else:
            sizes[kind] = path.stat().st_size
    if not all_stored:
        return False
    # 全部 stored → 推 meeting-complete + close_meeting
    try:
        from .realtime_server import push_event, close_meeting
        push_event(meeting_id, "meeting-complete", {
            "meeting_id": meeting_id,
            "status": "all_docs_stored",
            "doc_sizes": sizes,
        })
        logger.info(f"[{meeting_id}] 6 docs 全部 stored ({sum(sizes.values())} bytes), 推 meeting-complete + close_meeting")
        close_meeting(meeting_id)
    except Exception as e:
        logger.warning(f"[{meeting_id}] meeting-complete push/close failed: {e}")
    return True