"""Step 2 — transcript.py 单元测试(纯 dataclass,无外部依赖)

覆盖:
- TranscriptSegment 序列化/反序列化
- DiarizedSegment 默认值
- TranscriptResult.stats()
- round-trip 一致性
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from vpbuddy.transcript import (
    TranscriptSegment,
    DiarizedSegment,
    TranscriptResult,
)


def test_segment_default_values():
    """默认字段:start=0, end=0, text='', confidence=1.0, language='zh'"""
    seg = TranscriptSegment()
    assert seg.start_sec == 0.0
    assert seg.end_sec == 0.0
    assert seg.text == ""
    assert seg.confidence == 1.0
    assert seg.language == "zh"
    assert seg.segment_id.startswith("SEG-")
    assert seg.duration() == 0.0


def test_segment_duration():
    """duration() = end - start(非负)"""
    seg = TranscriptSegment(start_sec=1.0, end_sec=4.5)
    assert seg.duration() == 3.5
    seg2 = TranscriptSegment(start_sec=5.0, end_sec=2.0)  # 反向 → 0
    assert seg2.duration() == 0.0


def test_segment_serialize():
    """to_dict / from_dict round-trip"""
    seg = TranscriptSegment(
        segment_id="SEG-ABCDEF",
        start_sec=1.5,
        end_sec=3.2,
        text="测试",
        confidence=0.85,
        language="zh",
    )
    d = seg.to_dict()
    assert d["segment_id"] == "SEG-ABCDEF"
    assert d["text"] == "测试"
    assert d["confidence"] == 0.85
    seg2 = TranscriptSegment.from_dict(d)
    assert seg2.start_sec == seg.start_sec
    assert seg2.text == seg.text
    assert seg2.confidence == seg.confidence


def test_segment_from_dict_no_id():
    """老数据没 segment_id 时自动生成"""
    d = {"start_sec": 1.0, "end_sec": 2.0, "text": "hi", "confidence": 0.9, "language": "en"}
    seg = TranscriptSegment.from_dict(d)
    assert seg.segment_id.startswith("SEG-")
    assert seg.text == "hi"


def test_diarized_segment_defaults():
    """DiarizedSegment 默认 speaker_id=UNKNOWN, speaker_name=None, source=whisper+pyannote"""
    seg = DiarizedSegment(start_sec=1.0, end_sec=2.0, text="hi")
    assert seg.speaker_id == "SPEAKER_UNKNOWN"
    assert seg.speaker_name is None
    assert seg.source == "whisper+pyannote"


def test_diarized_segment_with_speaker():
    """指定 speaker 时的字段"""
    seg = DiarizedSegment(
        start_sec=1.0, end_sec=3.0,
        text="你好",
        speaker_id="SPEAKER_00",
        speaker_name="张三",
    )
    d = seg.to_dict()
    assert d["speaker_id"] == "SPEAKER_00"
    assert d["speaker_name"] == "张三"


def test_result_stats_empty():
    """空 result 的 stats"""
    r = TranscriptResult()
    s = r.stats()
    assert s["total_segments"] == 0
    assert s["num_speakers"] == 0
    assert s["total_duration_sec"] == 0
    assert s["speech_duration_sec"] == 0


def test_result_stats_with_segments():
    """有 segments 时统计正确"""
    r = TranscriptResult(
        duration_sec=60.0,
        segments=[
            DiarizedSegment(start_sec=0.0, end_sec=5.0, text="A", speaker_id="SPEAKER_00"),
            DiarizedSegment(start_sec=5.0, end_sec=10.0, text="B", speaker_id="SPEAKER_01"),
            DiarizedSegment(start_sec=10.0, end_sec=12.0, text="C", speaker_id="SPEAKER_00"),
        ],
    )
    s = r.stats()
    assert s["total_segments"] == 3
    assert s["num_speakers"] == 2  # 00 + 01
    assert s["speech_duration_sec"] == 12


def test_result_roundtrip():
    """完整 TranscriptResult JSON 序列化"""
    r = TranscriptResult(
        audio_path="/tmp/test.wav",
        language="zh",
        duration_sec=30.0,
        num_speakers=2,
        segments=[
            DiarizedSegment(start_sec=0.0, end_sec=5.0, text="Hi", speaker_id="SPEAKER_00"),
        ],
        model_name="large-v3",
        device="cuda",
        compute_type="float16",
        diarization_model="pyannote/speaker-diarization-3.1",
        created_at="2026-06-20T22:00:00+00:00",
    )
    d = r.to_dict()
    assert d["audio_path"] == "/tmp/test.wav"
    assert d["segments"][0]["text"] == "Hi"
    assert d["metadata"]["model_name"] == "large-v3"
    # round-trip
    r2 = TranscriptResult.from_dict(d)
    assert r2.audio_path == r.audio_path
    assert len(r2.segments) == 1
    assert r2.segments[0].speaker_id == "SPEAKER_00"
    assert r2.model_name == "large-v3"
    assert r2.diarization_model == "pyannote/speaker-diarization-3.1"
