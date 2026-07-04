"""Stream chunk metadata helpers.
Extracted from ui_server.py. P1#2 (2026-07-04)
"""
from __future__ import annotations

import json
from pathlib import Path

from .config import DATA_DIR


def _stream_meta_path(meeting_id: str) -> Path:
    return DATA_DIR / f"{meeting_id}.stream.json"


def _load_stream_meta(meeting_id: str) -> dict:
    p = _stream_meta_path(meeting_id)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_stream_meta(meeting_id: str, meta: dict) -> None:
    _stream_meta_path(meeting_id).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
