"""VPBuddy — 会议垂直 AI 应用(运行在 Hermes Agent 之上)

MVP 进度:
- Step 1: 会议结构化状态(MeetingState + storage)
- Step 2: ASR + 说话人分离(Whisper + pyannote)
- Step 3-6: 待规划(交付物生成 / UI / 飞书妙记校准 / 多平台)

ADR-0004 Step 2 设计:见 docs/decisions/0004-MVP-Step2-ASR设计.md
"""
__version__ = "0.2.0"  # Step 2 完成

# === Step 1 ===
from .state import (
    MeetingState, Platform, Priority, ItemStatus,
    Requirement, Goal, Feature, Risk, Question,
)
from .storage import MeetingStorage, create_storage

# === Step 2 ===
# transcript.py — 纯 dataclass,无外部依赖
from .transcript import TranscriptSegment, DiarizedSegment, TranscriptResult

# whisper_provider.py — 需要 faster-whisper
try:
    from .whisper_provider import WhisperProvider
except ImportError:
    WhisperProvider = None  # 没装 faster-whisper 时为 None

# diarization.py — 需要 pyannote-audio
try:
    from .diarization import PyannoteDiarizer
except ImportError:
    PyannoteDiarizer = None  # 没装 pyannote 时为 None

# engine.py — 需要 faster-whisper + pyannote
try:
    from .engine import TranscriptionEngine
except ImportError:
    TranscriptionEngine = None  # 依赖缺失时为 None


__all__ = [
    # 元数据
    "__version__",
    # Step 1
    "MeetingState", "Platform", "Priority", "ItemStatus",
    "Requirement", "Goal", "Feature", "Risk", "Question",
    "MeetingStorage", "create_storage",
    # Step 2
    "TranscriptSegment", "DiarizedSegment", "TranscriptResult",
    "WhisperProvider", "PyannoteDiarizer", "TranscriptionEngine",
]
