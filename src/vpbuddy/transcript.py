"""Transcript 数据结构 — Step 2 输出格式

设计原则(YAGNI):
- 不引入 pydantic BaseModel(已经验证 Pydantic 在 Step 1 用,但 transcript 是纯数据输出,用 dataclass 更轻)
- 不写 BaseProvider 抽象(WhisperProvider 直接是具体类)
- 不写 streaming/chunking 接口(整文件同步,Step 2.5 再加)
- speaker_name 默认 None(需人工/Step 5 飞书妙记校准填入)
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict, Any
from uuid import uuid4


@dataclass
class TranscriptSegment:
    """Whisper 转写出来的一段(无说话人信息)"""
    segment_id: str = field(default_factory=lambda: f"SEG-{uuid4().hex[:6].upper()}")
    start_sec: float = 0.0
    end_sec: float = 0.0
    text: str = ""
    confidence: float = 1.0  # faster-whisper 返回 avg_logprob 需 exp 转换
    language: str = "zh"

    def duration(self) -> float:
        return max(0.0, self.end_sec - self.start_sec)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TranscriptSegment":
        # 兼容 segment_id 缺失(老数据)
        if "segment_id" not in d:
            d = {**d, "segment_id": f"SEG-{uuid4().hex[:6].upper()}"}
        return cls(**d)


@dataclass
class DiarizedSegment:
    """Whisper 转写 + pyannote 说话人标签融合后的段"""
    segment_id: str = field(default_factory=lambda: f"SEG-{uuid4().hex[:6].upper()}")
    start_sec: float = 0.0
    end_sec: float = 0.0
    text: str = ""
    confidence: float = 1.0
    language: str = "zh"
    # === 说话人(Step 2 核心新增)===
    speaker_id: str = "SPEAKER_UNKNOWN"  # pyannote 输出 SPEAKER_00/01/...
    speaker_name: Optional[str] = None   # Step 5 飞书妙记校准后填入
    source: str = "whisper+pyannote"     # 来源标记(双轨时会区分)

    def duration(self) -> float:
        return max(0.0, self.end_sec - self.start_sec)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "DiarizedSegment":
        if "segment_id" not in d:
            d = {**d, "segment_id": f"SEG-{uuid4().hex[:6].upper()}"}
        return cls(**d)


@dataclass
class TranscriptResult:
    """整个转写任务的输出"""
    audio_path: str = ""
    language: str = "zh"
    duration_sec: float = 0.0
    num_speakers: int = 0
    segments: List[DiarizedSegment] = field(default_factory=list)
    # === 元数据 ===
    model_name: str = ""           # whisper 模型
    device: str = ""               # cuda/cpu
    compute_type: str = ""         # float16/int8/float32
    diarization_model: str = ""    # pyannote 模型
    created_at: str = ""           # ISO timestamp

    def stats(self) -> Dict[str, int]:
        """统计信息"""
        speaker_ids = set(s.speaker_id for s in self.segments)
        return {
            "total_segments": len(self.segments),
            "num_speakers": len(speaker_ids),
            "total_duration_sec": int(self.duration_sec),
            "speech_duration_sec": int(sum(s.duration() for s in self.segments)),
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "audio_path": self.audio_path,
            "language": self.language,
            "duration_sec": self.duration_sec,
            "num_speakers": self.num_speakers,
            "segments": [s.to_dict() for s in self.segments],
            "metadata": {
                "model_name": self.model_name,
                "device": self.device,
                "compute_type": self.compute_type,
                "diarization_model": self.diarization_model,
                "created_at": self.created_at,
            },
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TranscriptResult":
        meta = d.pop("metadata", {})
        segments = [DiarizedSegment.from_dict(s) for s in d.pop("segments", [])]
        return cls(segments=segments, **{**d, **meta})
