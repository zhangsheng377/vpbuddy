"""Shared constants for VPBuddy server service layer.
Extracted from ui_server.py (P1#2, 2026-07-08).
"""
from __future__ import annotations

import os
import threading
from pathlib import Path

# 项目根目录 (src/vpbuddy → 向上两级)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# 默认路径: 优先使用环境变量, 否则使用项目根目录下的相对路径
# 不再硬编码特定用户路径 (/home/zsd/...)
DOCS_DIR = Path(os.environ.get("VPBUDDY_DOCS_DIR", str(_PROJECT_ROOT / "docs")))
DATA_DIR = Path(os.environ.get("VPBUDDY_DATA_DIR", str(_PROJECT_ROOT / "data" / "meetings")))
UI_DIR = Path(os.environ.get("VPBUDDY_UI_DIR", str(_PROJECT_ROOT / "ui")))

DOC_KINDS = ["req", "arch", "tasks", "api", "risk", "demo"]
DOC_LABELS = {
    "req": "需求文档",
    "arch": "架构文档",
    "tasks": "任务拆解",
    "api": "API 设计",
    "risk": "风险分析",
    "demo": "Demo",
}

# 2026-06-28: ASR 后处理窗口
ASR_CLEAN_WINDOW_SIZE = 5
ASR_CLEAN_WINDOW_TIMEOUT_S = 30.0
ASR_CLEAN_MAX_CHARS = 2000

# 输入长度限制 (防止超长输入打爆 LLM / 存储)
MAX_CHAT_MESSAGE_LENGTH = 20000   # VP Chat 单条消息最大字符数
MAX_MEETING_ID_LENGTH = 128       # meeting_id 最大长度
MAX_FILENAME_LENGTH = 255         # 上传文件名最大长度
MAX_UPLOAD_SIZE = 100 * 1024 * 1024  # 上传文件最大 100MB

_CHAT_AGENT_LOCK = threading.Lock()
