"""API utility functions — state payload, lists, timeline.
Extracted from ui_server.py. P1#2 (2026-07-04)
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from .config import DATA_DIR, DOCS_DIR, DOC_KINDS, DOC_LABELS


def _norm_text(text: str) -> str:
    """Normalize text for duplicate detection."""
    import re
    t = re.sub(r'[^\w\u4e00-\u9fff]', '', text.strip().lower())
    return t


def _is_duplicate_segment(segment: dict, seen_segments: list[dict]) -> bool:
    """Check if segment text is already in seen list (by normalized text)."""
    text = _norm_text(segment.get("text", ""))
    if not text:
        return True
    for s in seen_segments:
        if _norm_text(s.get("text", "")) == text:
            return True
    return False


def _state_payload(state, include_items: bool = True) -> dict[str, Any]:
    """Serialize MeetingState → JSON-safe dict."""
    from vpbuddy.state import Priority

    def _items(items, typ: str):
        return [
            {
                "id": getattr(i, "id", f"{typ}-{idx}"),
                "text": getattr(i, "text", ""),
                "priority": getattr(i, "priority", Priority.MEDIUM).value,
                "speaker_id": getattr(i, "speaker_id", ""),
                "created_at": getattr(i, "created_at", ""),
                "doc_path": str(DOCS_DIR / getattr(i, "meeting_id", "?") / f"{typ}.md") if hasattr(i, "meeting_id") else "",
            }
            for idx, i in enumerate(items)
        ]

    payload: dict[str, Any] = {
        "meeting_id": getattr(state, "meeting_id", "?"),
        "platform": getattr(state, "platform", ""),
        "project_name": getattr(state, "project_name", ""),
        "speaker_map": getattr(state, "speaker_map", {}) or {},
        "summary": getattr(state, "summary", "") or "",
        "status": getattr(state, "status", "active") if hasattr(state, "status") else "active",
        "created_at": getattr(state, "created_at", "") if hasattr(state, "created_at") else "",
        "last_updated": getattr(state, "last_updated", "") if hasattr(state, "last_updated") else "",
    }
    if include_items:
        payload["items"] = {
            "requirements": _items(state.requirements, "requirement"),
            "goals": _items(state.goals, "goal"),
            "features": _items(state.features, "feature"),
            "risks": _items(state.risks, "risk"),
            "open_questions": _items(state.open_questions, "question"),
        }
    return payload


def _validate_meeting_id(mid: str) -> tuple[bool, str]:
    """Validate meeting_id format."""
    import re
    if not mid or not re.match(r'^[\w\-\.]+$', mid):
        return False, "Invalid meeting_id format"
    if len(mid) > 128:
        return False, "meeting_id too long (max 128 chars)"
    return True, ""


def list_meetings() -> list[dict]:
    """List all active meetings from storage."""
    from .storage import MeetingStorage
    storage = MeetingStorage(DATA_DIR)
    meetings = []
    for meeting_id in storage.list_meetings():
        try:
            state = storage.load(meeting_id)
            payload = _state_payload(state, include_items=False)
            payload["docs"] = _doc_payloads(meeting_id)
            meetings.append(payload)
        except Exception:
            pass
    meetings.sort(key=lambda m: m.get("last_updated", ""), reverse=True)
    return meetings


def _doc_payloads(meeting_id: str) -> list[dict]:
    """Get all doc payloads for a meeting."""
    from .chat_engine import _doc_payload
    return [_doc_payload(meeting_id, k) for k in DOC_KINDS]


def get_timeline() -> list[dict]:
    """Get timeline of all meetings (summary view)."""
    from .storage import MeetingStorage
    storage = MeetingStorage(DATA_DIR)
    timeline = []
    for meeting_id in storage.list_meetings():
        try:
            state = storage.load(meeting_id)
            try:
                meta = _load_stream_meta(meeting_id)
            except Exception:
                meta = {}
            timeline.append({
                "meeting_id": meeting_id,
                "project_name": getattr(state, "project_name", ""),
                "last_updated": getattr(state, "last_updated", ""),
                "speakers": list(state.speaker_map.values()) if state.speaker_map else [],
                "segment_count": len(meta.get("transcript_segments", [])),
                "active": getattr(state, "status", "active") if hasattr(state, "status") else "active",
                "status": _meeting_status(meeting_id),
            })
        except Exception:
            pass
    timeline.sort(key=lambda m: m.get("last_updated", ""), reverse=True)
    return timeline


def _meeting_status(meeting_id: str) -> str:
    """Return meeting status string."""
    from .stream_meta import _load_stream_meta
    meta = _load_stream_meta(meeting_id)
    return meta.get("status", meta.get("state", "active"))


def get_status() -> dict:
    """Return server-wide status."""
    import time as _time
    return {
        "service": "VPBuddy UI Server",
        "uptime": _time.time(),
        "version": "unknown",
        "meeting_count": 0,
        "doc_dir": str(DOCS_DIR),
        "data_dir": str(DATA_DIR),
        "platform": "desktop_client",
        "asr_cache_hot": True,
    }
