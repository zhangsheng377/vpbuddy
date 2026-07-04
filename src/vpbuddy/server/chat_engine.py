#!/usr/bin/env python3
"""VP chat engine with context (P1#2 2026-07-04)"""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import DATA_DIR, DOCS_DIR, DOC_KINDS, DOC_LABELS, _CHAT_AGENT_LOCK
from .stream_meta import _load_stream_meta

_CHAT_AGENT_CACHE: dict[str, Any] = {}


def _chat_path(meeting_id: str) -> Path:
    return DATA_DIR / f"{meeting_id}.chat.json"


def _load_chat_history(meeting_id: str) -> list[dict[str, Any]]:
    path = _chat_path(meeting_id)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
        return data.get("messages", [])
    except Exception:
        return []


def _save_chat_history(meeting_id: str, messages: list[dict[str, Any]]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _chat_path(meeting_id).write_text(
        json.dumps(messages[-500:], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _append_chat_message(
    meeting_id: str,
    role: str,
    content: str,
    *,
    source: str = "vp-chat",
    status: str = "ok",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    message = {
        "id": f"chat-{int(time.time() * 1000)}-{uuid.uuid4().hex[:6]}",
        "meeting_id": meeting_id,
        "role": role,
        "content": content,
        "source": source,
        "status": status,
        "created_at": datetime.now().isoformat(),
    }
    if extra:
        message.update(extra)
    history = _load_chat_history(meeting_id)
    history.append(message)
    _save_chat_history(meeting_id, history)
    return message


def _meeting_context_for_chat(meeting_id: str) -> dict[str, Any]:
    state_payload: dict[str, Any] = {"meeting_id": meeting_id, "items": []}
    try:
        from .storage import MeetingStorage
        storage = MeetingStorage(DATA_DIR)
        if storage.exists(meeting_id):
            from .api_utils import _state_payload
            state_payload = _state_payload(storage.load(meeting_id), include_items=True)
    except Exception as e:
        state_payload["error"] = str(e)

    docs = []
    for kind in DOC_KINDS:
        doc = _doc_payload(meeting_id, kind)
        docs.append(doc)
    state_payload["docs"] = docs
    return state_payload


def _get_chat_agent(meeting_id: str):
    """Get or create VP chat AIAgent (cached by meeting_id)."""
    if meeting_id not in _CHAT_AGENT_CACHE:
        from run_agent import AIAgent
        sid = f"meeting:{meeting_id}:vp-chat"
        with _CHAT_AGENT_LOCK:
            if meeting_id not in _CHAT_AGENT_CACHE:
                _CHAT_AGENT_CACHE[meeting_id] = AIAgent(
                    session_id=sid,
                    enabled_toolsets=["read", "write", "search", "bash"],
                )
    return _CHAT_AGENT_CACHE[meeting_id]


def _run_vp_chat(meeting_id: str, message: str, client_context: dict[str, Any] | None = None) -> dict[str, Any]:
    """VP chat endpoint: context building + AIAgent.chat() + history append."""
    msg = _append_chat_message(meeting_id, "user", message, source="vp-chat", status="pending")
    try:
        context = _meeting_context_for_chat(meeting_id)
        context.pop("items", None)
        prompt_parts = [
            "You are VPBuddy VP Chat assistant.",
            "",
            "Meeting context:",
            json.dumps(context, ensure_ascii=False, indent=2),
            "",
            "User message:", message,
        ]
        prompt = "\n".join(prompt_parts)

        agent = _get_chat_agent(meeting_id)
        response = agent.chat(prompt)

        reply = _append_chat_message(meeting_id, "assistant", response or "", source="vp-chat")
        msg["status"] = "ok"
        msg["assistant_message"] = reply
        msg["assistant_content"] = response
        _append_chat_message(meeting_id, "user", message, source="vp-chat", status="ok")
    except Exception as e:
        msg["status"] = "error"
        msg["error"] = str(e)
    return msg


def _doc_path(meeting_id: str, kind: str) -> Path:
    if kind == "demo":
        return DOCS_DIR / meeting_id / "demo" / "demo.html"
    return DOCS_DIR / meeting_id / f"{kind}.md"


def _doc_payload(meeting_id: str, kind: str) -> dict[str, Any]:
    path = _doc_path(meeting_id, kind)
    exists = path.exists()
    content = ""
    updated_at = None
    if exists:
        content = path.read_text(encoding="utf-8", errors="replace")
        updated_at = datetime.fromtimestamp(path.stat().st_mtime).isoformat()
    label_map = {
        "req": "需求文档", "arch": "架构文档", "tasks": "任务列表",
        "api": "API 文档", "risk": "风险评估", "demo": "演示 Demo",
    }
    return {
        "meeting_id": meeting_id,
        "kind": kind,
        "label": label_map.get(kind, kind),
        "status": "stored" if exists else "pending",
        "path": str(path),
        "content": content,
        "updated_at": updated_at,
        "doc_size": path.stat().st_size if exists else 0,
    }