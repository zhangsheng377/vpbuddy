"""测试 audio loopback 采集"""
import platform
import wave
from pathlib import Path
import pytest
from vpbuddy.loopback import (
    list_monitor_sources,
    capture_loopback,
    list_audio_capabilities,
    _write_silence_wav,
)


class TestListMonitorSources:
    def test_returns_list(self):
        """永远返 list(可能空,代表无 monitor 源)"""
        sources = list_monitor_sources()
        assert isinstance(sources, list)
        for s in sources:
            assert "name" in s
            assert "description" in s
            assert "backend" in s
            assert s["backend"] in ("pipewire", "pulseaudio")

    def test_pulseaudio_filter(self):
        """PipeWire 抓出来的源都标 backend=pipewire"""
        sources = list_monitor_sources()
        for s in sources:
            # backend 只允许 pipewire 或 pulseaudio
            assert s["backend"] in ("pipewire", "pulseaudio")
            # name 应含 monitor(过滤掉了非 monitor 源)
            assert "monitor" in s["name"].lower() or s["backend"] == "pulseaudio"


class TestSilenceFallback:
    def test_silence_wav_format(self, tmp_path):
        """静音 wav 格式正确(16kHz mono 16-bit)"""
        path = _write_silence_wav(tmp_path / "silence.wav", duration_sec=2.0, sample_rate=16000)
        assert path.exists()
        with wave.open(str(path), "rb") as wf:
            assert wf.getnchannels() == 1
            assert wf.getframerate() == 16000
            assert wf.getsampwidth() == 2
            assert wf.getnframes() == 2 * 16000  # 2 seconds

    def test_silence_wav_different_sample_rate(self, tmp_path):
        """支持不同采样率"""
        path = _write_silence_wav(tmp_path / "silence_44k.wav", duration_sec=1.0, sample_rate=44100)
        with wave.open(str(path), "rb") as wf:
            assert wf.getframerate() == 44100
            assert wf.getnframes() == 44100


class TestCaptureLoopback:
    def test_capture_with_silence_fallback_no_source(self, tmp_path):
        """无可用源 + silence_fallback=True → 生成静音"""
        out = tmp_path / "test.wav"
        result = capture_loopback(
            duration_sec=1.0,
            output_path=out,
            source_name=None,
            silence_fallback=True,
        )
        # 即使有源也允许,只要 file 存在
        assert result.exists()
        assert result.stat().st_size > 44  # wav header + samples

    def test_capture_creates_parent_dirs(self, tmp_path):
        """自动建父目录"""
        out = tmp_path / "deep" / "nested" / "loopback.wav"
        capture_loopback(duration_sec=0.5, output_path=out, silence_fallback=True)
        assert out.exists()

    def test_capture_short_audio(self, tmp_path):
        """短时长(0.5s)也能捕获/降级"""
        out = tmp_path / "short.wav"
        result = capture_loopback(duration_sec=0.5, output_path=out, silence_fallback=True)
        assert result.exists()
        # 验证 wav 可读
        with wave.open(str(result), "rb") as wf:
            assert wf.getnframes() >= 0

    def test_capture_no_source_no_silence_raises(self, tmp_path, monkeypatch):
        """无源 + silence_fallback=False + PipeWire 无源 → 应 raise

        注:此测试在有真实 monitor 源的环境会失败,因为会真捕获
        """
        # 模拟 list_monitor_sources 返空
        monkeypatch.setattr("vpbuddy.loopback.list_monitor_sources", lambda: [])
        with pytest.raises(RuntimeError, match="无可用 monitor source"):
            capture_loopback(
                duration_sec=0.5,
                output_path=tmp_path / "x.wav",
                source_name=None,
                silence_fallback=False,
            )


class TestAudioCapabilities:
    def test_capabilities_structure(self):
        """list_audio_capabilities 返完整诊断信息"""
        caps = list_audio_capabilities()
        assert "platform" in caps
        assert "tools" in caps
        assert "monitor_sources" in caps
        assert "macos_supported" in caps
        assert "windows_supported" in caps
        # tools 是 dict,值可能是 None(没装)或 str path
        for tool, path in caps["tools"].items():
            assert path is None or isinstance(path, str)
        # 在 Linux 跑测试
        if platform.system() == "Linux":
            assert caps["platform"] == "Linux"
