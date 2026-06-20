"""Step 2 — PyannoteDiarizer 集成测试(需要 GPU + pyannote + ModelScope 模型)

不需要 HF_TOKEN!模型通过 ModelScope 镜像下载到本地。

跳过条件:
- pyannote.audio 未安装 → skip
- modelscope 未安装 → skip
- 本地模型未下载(会自动调 ModelScope 下载,需联网)
"""
import sys
import os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

pyannote = pytest.importorskip("pyannote.audio", reason="pyannote.audio 未安装")
pytest.importorskip("modelscope", reason="modelscope 未安装(用于拉 pyannote 模型)")


from vpbuddy.diarization import PyannoteDiarizer


SAMPLES_DIR = Path(__file__).resolve().parent.parent.parent / "samples"
TEST_AUDIO_16K = SAMPLES_DIR / "test_zh_16k_mono.wav"


def get_16k_audio() -> Path:
    """获取 16kHz mono PCM 音频(避免 pyannote 内部重采样错误)

    自动从原 wav 转换,缓存到 samples/test_zh_16k_mono.wav
    """
    src = SAMPLES_DIR / "test_zh_sample.wav"
    if not TEST_AUDIO_16K.exists():
        import torchaudio
        wf, sr = torchaudio.load(str(src))
        if wf.shape[0] > 1:
            wf = wf.mean(dim=0, keepdim=True)
        wf = torchaudio.functional.resample(wf, sr, 16000)
        torchaudio.save(str(TEST_AUDIO_16K), wf, 16000, encoding="PCM_S", bits_per_sample=16)
    return TEST_AUDIO_16K


def test_pyannote_load_pipeline():
    """加载 pyannote 3.1 pipeline(从本地 ModelScope 下载的模型)"""
    audio = get_16k_audio()
    diarizer = PyannoteDiarizer()
    pipeline = diarizer._load()
    assert pipeline is not None
    print(f"Pipeline loaded: {type(pipeline).__name__}")


def test_pyannote_diarize_returns_turns():
    """diarize 至少返回一些 speaker turns"""
    audio = get_16k_audio()
    diarizer = PyannoteDiarizer()
    turns = diarizer.get_speaker_turns(str(audio), num_speakers=1)
    assert isinstance(turns, list)
    assert len(turns) >= 1, "单说话人也应该至少 1 个 turn"
    for start, end, label in turns:
        assert end > start
        assert label.startswith("SPEAKER_")
    print(f"Got {len(turns)} turns, sample: {turns[:3]}")


def test_pyannote_num_speakers_1():
    """强制 num_speakers=1 时,只有 1 种 speaker label"""
    audio = get_16k_audio()
    diarizer = PyannoteDiarizer()
    turns = diarizer.get_speaker_turns(str(audio), num_speakers=1)
    labels = set(t[2] for t in turns)
    assert len(labels) == 1
    assert "SPEAKER_00" in labels or "SPEAKER_01" in labels
