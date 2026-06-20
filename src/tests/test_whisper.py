"""Step 2 — WhisperProvider 集成测试(需要 GPU + faster-whisper)

跳过条件:本地没装 faster-whisper → skip
样本音频:samples/test_zh_sample.wav(任何 16kHz 中文语音文件)
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

# Skip 整个文件 if faster-whisper not available
faster_whisper = pytest.importorskip("faster_whisper", reason="faster-whisper 未安装")

from vpbuddy.whisper_provider import WhisperProvider
from vpbuddy.transcript import TranscriptSegment, TranscriptResult


# === 测试样本路径 ===
SAMPLES_DIR = Path(__file__).resolve().parent.parent.parent / "samples"
TEST_AUDIO = SAMPLES_DIR / "test_zh_sample.wav"

# 提供 2 个真实音频路径选项(MVP:用户给一段)
# 找不到时,跳过集成测试
@pytest.fixture(scope="module")
def audio_path():
    """找一个可用的测试音频(优先 samples,fallback 用户家目录任何 wav/mp3)"""
    if TEST_AUDIO.exists():
        return TEST_AUDIO
    # fallback: 找 /home/zsd 或 /mnt 下任何 wav/mp3
    import subprocess
    for d in ["/home/zsd", "/mnt/nfs_fn/zsd_server"]:
        try:
            r = subprocess.run(
                ["find", d, "-maxdepth", "4", "-name", "*.wav", "-size", "+100k", "-size", "-30M"],
                capture_output=True, text=True, timeout=10,
            )
            for line in r.stdout.split("\n"):
                line = line.strip()
                if line and Path(line).exists():
                    return Path(line)
        except Exception:
            continue
    pytest.skip("没有可用的测试音频文件")


def test_whisper_load_model(audio_path):
    """加载 large-v3 模型(不转写,只验证 load)"""
    provider = WhisperProvider(model_size="large-v3", device="cuda", compute_type="float16")
    model = provider._load()
    assert model is not None
    print(f"Loaded model: {provider.model_size} on {provider.device}")


def test_whisper_transcribe_returns_segments(audio_path):
    """转写一段音频,验证返回 segments 列表"""
    provider = WhisperProvider(model_size="large-v3", device="cuda", compute_type="float16")
    segs = provider.transcribe_file(audio_path, language="zh")
    assert isinstance(segs, list)
    assert len(segs) >= 1, "至少应该有 1 段转写"
    for seg in segs:
        assert isinstance(seg, TranscriptSegment)
        assert seg.end_sec >= seg.start_sec
        assert seg.text != ""
        assert 0.0 <= seg.confidence <= 1.0
    print(f"Transcribed {len(segs)} segments from {audio_path.name}")


def test_whisper_confidence_in_range(audio_path):
    """所有 confidence 都在 [0, 1] 范围"""
    provider = WhisperProvider(model_size="large-v3", device="cuda", compute_type="float16")
    segs = provider.transcribe_file(audio_path, language="zh")
    for seg in segs:
        assert 0.0 <= seg.confidence <= 1.0, f"bad confidence: {seg.confidence}"


def test_whisper_to_result(audio_path):
    """transcribe_to_result 返回完整 TranscriptResult"""
    provider = WhisperProvider(model_size="large-v3", device="cuda", compute_type="float16")
    result = provider.transcribe_to_result(audio_path, language="zh")
    assert isinstance(result, TranscriptResult)
    assert result.model_name == "large-v3"
    assert result.device == "cuda"
    assert result.audio_path == str(audio_path)
    assert result.duration_sec > 0


def test_whisper_rtf_under_threshold(audio_path):
    """实时因子 RTF < 0.5(RTX 3090 Ti 大模型)"""
    import time
    provider = WhisperProvider(model_size="large-v3", device="cuda", compute_type="float16")
    t0 = time.time()
    segs = provider.transcribe_file(audio_path, language="zh")
    elapsed = time.time() - t0
    duration = segs[-1].end_sec if segs else 0
    rtf = elapsed / max(duration, 0.1)
    print(f"RTF={rtf:.3f} (elapsed={elapsed:.1f}s, audio={duration:.1f}s)")
    # 大模型 GPU 推理 RTF 应该 < 0.3(快于实时 3 倍以上)
    assert rtf < 0.5, f"RTF too slow: {rtf}"
