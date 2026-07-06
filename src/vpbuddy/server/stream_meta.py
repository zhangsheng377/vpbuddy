"""Stream chunk metadata helpers.
Extracted from ui_server.py. P1#2 (2026-07-04)
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path

from .config import DATA_DIR

_LOCK_CACHE: dict[str, threading.Lock] = {}
_LOCK_CACHE_LOCK = threading.Lock()


def _get_stream_lock(meeting_id: str) -> threading.Lock:
    with _LOCK_CACHE_LOCK:
        if meeting_id not in _LOCK_CACHE:
            _LOCK_CACHE[meeting_id] = threading.Lock()
        return _LOCK_CACHE[meeting_id]


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
    with _get_stream_lock(meeting_id):
        fd, tmp_path = tempfile.mkstemp(suffix=".stream.json.tmp", dir=str(DATA_DIR))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(json.dumps(meta, ensure_ascii=False, indent=2, default=str))
                f.flush()
                os.fsync(fd)
            os.replace(tmp_path, _stream_meta_path(meeting_id))
        except:
            try:
                os.unlink(tmp_path)
            except:
                pass
            raise
