"""Step 2 — TranscriptionEngine 测试

- 单元测试(assign_speaker):不需要 GPU/HF_TOKEN
- 集成测试(e2e, serialize):需要 faster-whisper + pyannote + HF_TOKEN
"""
import sys
import os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
import json

pytest.importorskip("faster_whisper", reason="faster-whisper 未安装")
pytest.importorskip("pyannote.audio", reason="pyannote.audio 未安装")

# 不在 module 级 skip —— 让 unit test 也能跑(GPU 集成测试在 fixture 里 skip)
requires_token = pytest.mark.skipif(
    not os.environ.get("HF_TOKEN"),
    reason="需要 HF_TOKEN 才能跑 pyannote + 部分 whisper 模型",
)


from vpbuddy.engine import TranscriptionEngine
from vpbuddy.transcript import TranscriptResult, DiarizedSegment


@pytest.fixture(scope="module")
def audio_path():
    from tests.test_whisper import audio_path as _ap
    return _ap()


def test_engine_assign_speaker_with_empty_turns():
    """空 turns → SPEAKER_UNKNOWN(不需要 GPU)"""
    eng = TranscriptionEngine.__new__(TranscriptionEngine)  # bypass init
    from vpbuddy.transcript import TranscriptSegment
    seg = TranscriptSegment(start_sec=1.0, end_sec=2.0, text="hi")
    label = eng._assign_speaker(seg, [])
    assert label == "SPEAKER_UNKNOWN"


def test_engine_assign_speaker_picks_nearest_midpoint():
    """_assign_speaker 选 turn 中点最近的 speaker"""
    eng = TranscriptionEngine.__new__(TranscriptionEngine)  # bypass init
    from vpbuddy.transcript import TranscriptSegment
    seg = TranscriptSegment(start_sec=5.0, end_sec=6.0, text="hi")  # mid=5.5
    turns = [
        (0.0, 1.0, "SPEAKER_00"),  # mid=0.5, dist=5.0
        (4.5, 6.5, "SPEAKER_01"),  # mid=5.5, dist=0.0 ← winner
        (10.0, 11.0, "SPEAKER_02"),  # mid=10.5, dist=5.0
    ]
    label = eng._assign_speaker(seg, turns)
    assert label == "SPEAKER_01"


@requires_token
def test_engine_end_to_end_single_speaker(audio_path):
    """端到端:单说话人音频 → 所有 segments 同一 speaker"""
    eng = TranscriptionEngine.default(
        model_size="large-v3", device="cuda",
        compute_type="float16",
        hf_token=os.environ.get("HF_TOKEN"),
    )
    result = eng.process(audio_path, language="zh", num_speakers=1)
    assert isinstance(result, TranscriptResult)
    assert len(result.segments) >= 1
    # 单说话人:所有 segments 同一 speaker_id
    speaker_ids = set(s.speaker_id for s in result.segments)
    assert len(speaker_ids) == 1, f"期望 1 个 speaker,得到 {speaker_ids}"
    # 元数据
    assert result.num_speakers == 1
    assert result.duration_sec > 0
    assert result.model_name == "large-v3"
    print(f"E2E OK: {len(result.segments)} segs, 1 speaker, {result.duration_sec:.1f}s")


@requires_token
def test_engine_end_to_end_auto_detect(audio_path):
    """端到端:自动检测说话人数(num_speakers=None)"""
    eng = TranscriptionEngine.default(
        model_size="large-v3", device="cuda",
        compute_type="float16",
        hf_token=os.environ.get("HF_TOKEN"),
    )
    result = eng.process(audio_path, language="zh")  # 不指定 num
    # 自动检测可能 ≥ 1
    assert result.num_speakers >= 1
    print(f"Auto-detect: {result.num_speakers} speakers")


@requires_token
def test_engine_serialize_to_json(audio_path, tmp_path):
    """完整 result 序列化到 JSON 文件"""
    eng = TranscriptionEngine.default(
        model_size="large-v3", device="cuda",
        compute_type="float16",
        hf_token=os.environ.get("HF_TOKEN"),
    )
    result = eng.process(audio_path, language="zh", num_speakers=1)
    out = tmp_path / "transcript.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result.to_dict(), f, ensure_ascii=False, indent=2)
    # round-trip
    with open(out, encoding="utf-8") as f:
        loaded = json.load(f)
    assert "segments" in loaded
    assert "metadata" in loaded
    assert loaded["metadata"]["model_name"] == "large-v3"
    print(f"Wrote {out} ({out.stat().st_size} bytes, {len(loaded['segments'])} segs)")
