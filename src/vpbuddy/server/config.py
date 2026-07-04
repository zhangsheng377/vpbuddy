"""VPBuddy server configuration constants.

Extracted from ui_server.py. P1#2 (2026-07-04)
"""
from __future__ import annotations

import os
import threading
from pathlib import Path

# Auto-computed project root. P1#1 (2026-07-04)
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Directories (env overridable)
DOCS_DIR = Path(os.environ.get("VPBUDDY_DOCS_DIR", PROJECT_ROOT / "docs"))
DATA_DIR = Path(os.environ.get("VPBUDDY_DATA_DIR", PROJECT_ROOT / "data" / "meetings"))
UI_DIR = Path(os.environ.get("VPBUDDY_UI_DIR", PROJECT_ROOT / "ui"))
CONTROLLER_PID_FILE = Path("/tmp/vpbuddy_controller.pid")
CONTROLLER_LOG = Path("/tmp/vpbuddy_controller.log")

# Document kinds
DOC_KINDS = ["req", "arch", "tasks", "api", "risk", "demo"]
DOC_LABELS = {
    "req": "需求文档",
    "arch": "架构文档",
    "tasks": "任务列表",
    "api": "API 文档",
    "risk": "风险评估",
    "demo": "演示 Demo",
}

# ASR clean constants
_CHAT_AGENT_LOCK = threading.Lock()
_CLEAN_AGENT_LOCK = threading.Lock()
ASR_CLEAN_WINDOW_SIZE = 5
ASR_CLEAN_WINDOW_TIMEOUT_S = 30.0
ASR_CLEAN_MAX_CHARS = 2000
ASR_CLEAN_DEFAULT_MODEL = os.environ.get("VPBUDDY_LLM_MODEL", "qwen3:8b")
