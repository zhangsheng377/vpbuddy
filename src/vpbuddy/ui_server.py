"""
VPBuddy UI Server — 实时会议 AI 后端 API

提供:
- GET /                     → UI shell
- GET /docs/*               → 静态文档
- GET /api/meetings         → 会议列表
- GET /api/timeline         → 全部累积项按时间倒序
- GET /api/kb/search?q=     → 跨会议 RAG 检索
- GET /api/status           → Controller + 数据状态
- POST /api/meetings/upload → 上传音频自动转写+入库+触发 6 docs
- POST /api/meetings/stream_start → 创建流式会议
- POST /api/meetings/{id}/stream_chunk → 接收音频切片
- GET  /api/meetings/{id}/events → SSE 实时推送转写/文档/状态更新

用法: python -m vpbuddy.ui_server [--port 8765]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

# 🔒 HF 模型离线铁律 (2026-06-23 ADR-0011):
# 国内 huggingface.co 被墙,启动时强制默认走本地 cache。
# 用户装新模型时临时设 HF_HUB_OFFLINE=0 即可。
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

# ── 从 api_utils 导入共享常量和函数 (service layer, one source of truth) ──
# All items below were extracted from this file into src/vpbuddy/server/api_utils.py
# and src/vpbuddy/server/config.py (P1#2, completed 2026-07-08).
from .server.api_utils import (  # noqa: E402
    # 常量 (via config)
    ASR_CLEAN_MAX_CHARS,
    ASR_CLEAN_WINDOW_SIZE,
    ASR_CLEAN_WINDOW_TIMEOUT_S,
    DATA_DIR,
    DOCS_DIR,
    DOC_KINDS,
    DOC_LABELS,
    UI_DIR,
    # 模块级缓存和锁
    _CHAT_AGENT_CACHE,
    _CLEAN_AGENT_CACHE,
    _CLEAN_AGENT_LOCK,
    _get_meta_lock,
    _get_chat_lock,
    # Stream meta
    _load_stream_meta,
    _save_stream_meta,
    _stream_meta_path,
    # 文本处理
    _is_duplicate_segment,
    _norm_text,
    _parse_multipart,
    # 文档路径和负载
    _doc_path,
    _doc_payload,
    # Chat 历史
    _append_chat_message,
    _chat_path,
    _load_chat_history,
    _save_chat_history,
    # Chat agent
    _get_chat_agent,
    _meeting_context_for_chat,
    _run_vp_chat,
    # ASR 后处理
    _get_clean_agent,
    _run_asr_clean,
    # State / Meetings / Timeline / Status
    _state_payload,
    _validate_meeting_id,
    get_status,
    get_timeline,
    list_meetings,
)

# ── 模块级会议关闭函数 (v0.9.0: 供 FastAPI + VPBuddyHandler 共用) ──

def _close_meeting(meeting_id: str) -> dict:
    """关闭会议: SSE complete 事件 + 经验蒸馏 + 文档生成触发.

    被 ui_server.VPBuddyHandler._handle_meeting_close 和 FastAPI 路由共用.
    不依赖 self, 返回 dict 由调用方序列化.

    v0.9.0: 新增 task_manager 提交文档生成 (替代旧 controller 轮询).
    """
    from .realtime_server import close_meeting, push_event
    from .task_manager import get_task_manager
    from .sub_session_controller import _dispatch_kind, BATCH_DOCS_KIND, DEMO_KIND

    try:
        push_event(meeting_id, "meeting-complete", {
            "meeting_id": meeting_id,
            "status": "user_closed",
            "note": "用户主动结束 (ADR-0022)",
        })
        closed = close_meeting(meeting_id)

        # 清 proactive 节流
        try:
            from .agent_proactive import clear_throttle
            cleared = clear_throttle(meeting_id)
        except Exception:
            cleared = 0

        # 经验蒸馏 (#1)
        extracted_count = 0
        try:
            from .storage import MeetingStorage
            from .experience_store import extract_from_meeting_state, save_experiences
            storage = MeetingStorage(DATA_DIR)
            if storage.exists(meeting_id):
                state = storage.load(meeting_id)
                items = extract_from_meeting_state(meeting_id, state, meeting_title=meeting_id)
                if items:
                    save_experiences(meeting_id, items)
                    extracted_count = len(items)
                    print(f"[experience] 会议 {meeting_id} 提取 {extracted_count} 条经验候选")
        except Exception as e:
            print(f"[experience] 会议 {meeting_id} 经验提取失败: {e}")

        # v0.9.0: 通过 task_manager 提交文档生成 (替代旧 controller 轮询)
        doc_task_submitted = False
        try:
            def _doc_runner(gen_id: int, mid: str) -> dict:
                kinds = [BATCH_DOCS_KIND, DEMO_KIND]
                results = {}
                for kind in kinds:
                    try:
                        r = _dispatch_kind(mid, kind, dry_run=False)
                        results[kind] = {"triggered": r.get("triggered"), "error": r.get("error")}
                    except Exception as e:
                        results[kind] = {"triggered": False, "error": str(e)}
                return results
            get_task_manager().submit(meeting_id, _doc_runner)
            doc_task_submitted = True
        except Exception as e:
            print(f"[close_meeting] 文档生成任务提交失败: {e}")

        print(f"[close_meeting] {meeting_id}: 关闭 {closed} 个 SSE, 清 {cleared} 个 throttle, "
              f"提取 {extracted_count} 条经验, doc_task={doc_task_submitted}")
        return {
            "meeting_id": meeting_id,
            "closed_subscribers": closed,
            "proactive_cleared": cleared,
            "experiences_extracted": extracted_count,
            "doc_task_submitted": doc_task_submitted,
            "status": "closed",
        }
    except Exception as e:
        return {"error": str(e), "status": "close_failed"}
