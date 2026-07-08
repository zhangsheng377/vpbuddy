"""AI Settings 存储 — #21 每用户模型配置.

数据路径: data/settings/ai/{user_id}.json

字段:
    provider: str       # openai-compatible / minimax / ollama / ... 
    model: str           # 模型名
    base_url: str        # API 地址
    api_key: str         # 明文存储 (本地文件, 不对外暴露)
    updated_at: str      # ISO 时间戳
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

# 数据目录由 fastapi_app 注入, 模块级惰性加载
_SETTINGS_DIR: Path | None = None

def _get_dir() -> Path:
    global _SETTINGS_DIR
    if _SETTINGS_DIR is None:
        from ..ui_server import DATA_DIR
        _SETTINGS_DIR = Path(DATA_DIR) / "settings" / "ai"
    _SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    return _SETTINGS_DIR

def _path(user_id: str) -> Path:
    return _get_dir() / f"{user_id}.json"

# ── CRUD ──

def load_settings(user_id: str) -> dict | None:
    p = _path(user_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None

def save_settings(user_id: str, data: dict):
    p = _path(user_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = -1, ""
    try:
        fd, tmp = tempfile.mkstemp(suffix=".json", dir=str(p.parent))
        os.write(fd, json.dumps(data, ensure_ascii=False, indent=2).encode())
        os.fsync(fd)
    finally:
        if fd >= 0:
            os.close(fd)
    os.replace(tmp, str(p))

def mask_key(key: str) -> str:
    """API Key 脱敏: sk-****abcd (仅显示最后4位)."""
    if not key:
        return ""
    if len(key) <= 8:
        return key[:2] + "****" + key[-2:]
    return key[:3] + "****" + key[-4:]
