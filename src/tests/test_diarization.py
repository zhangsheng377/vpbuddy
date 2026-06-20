"""Step 2 — PyannoteDiarizer 集成测试(需要 GPU + pyannote + HF_TOKEN)

跳过条件:
- pyannote.audio 未安装 → skip
- HF_TOKEN 未设置 → skip
"""
import sys
import os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

pyannote = pytest.importorskip("pyannote.audio", reason="pyannote.audio 未安装")


@pytest.fixture(scope="module", autouse=True)
def require_hf_token():
    """没有 HF_TOKEN 就 skip 整个文件"""
    if not os.environ.get("HF_TOKEN"):
        pytest.skip("需要 HF_TOKEN 环境变量(pyannote 模型是 gated 的)")


from vpbuddy.diarization import PyannoteDiarizer


@pytest.fixture(scope="module")
def audio_path():
    from tests.test_whisper import audio_path as _ap
    return _ap()


def test_pyannote_load_pipeline(audio_path):
    """加载 pyannote 3.1 pipeline"""
    diarizer = PyannoteDiarizer()
    pipeline = diarizer._load()
    assert pipeline is not None


def test_pyannote_diarize_returns_turns(audio_path):
    """diarize 至少返回一些 speaker turns"""
    diarizer = PyannoteDiarizer()
    turns = diarizer.get_speaker_turns(audio_path, num_speakers=1)  # 先用 num=1 验证机制
    assert isinstance(turns, list)
    assert len(turns) >= 1, "单说话人也应该至少 1 个 turn"
    for start, end, label in turns:
        assert end > start
        assert label.startswith("SPEAKER_")
    print(f"Got {len(turns)} turns, sample: {turns[:3]}")


def test_pyannote_num_speakers_1(audio_path):
    """强制 num_speakers=1 时,只有 1 种 speaker label"""
    diarizer = PyannoteDiarizer()
    turns = diarizer.get_speaker_turns(audio_path, num_speakers=1)
    labels = set(t[2] for t in turns)
    assert len(labels) == 1
    assert "SPEAKER_00" in labels or "SPEAKER_01" in labels  # pyannote 随机从 00 或 01 开始
