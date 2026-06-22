"""音频 loopback 采集(VPBuddy ADR-0004:不依赖会议平台 SDK,直接采集系统音频)

支持平台:
- Linux PipeWire / PulseAudio:monitor source
- macOS:BlackHole / Soundflower / Loopback(待实现)
- Windows:WASAPI loopback(待实现)

为什么需要这个:
VPBuddy 真正的部署目标 = VP 自己桌面客户端,会议平台(腾讯会议/钉钉/企微)不开
音频 API 给 VPBuddy。所以直接捕获系统音频输出 → 不需要任何 SDK 集成。

输出:16kHz mono PCM WAV 文件,funasr/funasr/paraformer-zh 直接吃。
"""
import os
import platform
import shutil
import subprocess
import tempfile
import time
import wave
from pathlib import Path
from typing import List, Optional, Dict, Any


# === 平台检测 ===
_IS_LINUX = platform.system() == "Linux"
_IS_MACOS = platform.system() == "Darwin"
_IS_WINDOWS = platform.system() == "Windows"


def list_monitor_sources() -> List[Dict[str, str]]:
    """列出可用的 monitor 源(Linux PipeWire/PulseAudio)

    Returns:
        [
            {"name": "auto_null.monitor", "description": "虚拟输出 monitor", "backend": "pipewire"},
            ...
        ]
    """
    sources: List[Dict[str, str]] = []

    if not _IS_LINUX:
        return sources

    # === PipeWire 优先(pw-dump 拿所有 node,过滤 monitor port) ===
    if shutil.which("pw-dump"):
        try:
            r = subprocess.run(
                ["pw-dump"], capture_output=True, text=True, timeout=10
            )
            if r.returncode == 0:
                import json
                data = json.loads(r.stdout)
                # 找 Audio/Sink 节点,然后看有没有 port.alias = "monitor"
                for obj in data:
                    info = obj.get("info", {})
                    props = info.get("props", {})
                    if props.get("media.class") == "Audio/Sink":
                        sink_name = props.get("node.name", "")
                        sink_desc = props.get("node.description", sink_name)
                        # PipeWire 暴露 monitor 作为同名 .monitor 源
                        # 实际有没有要看 ports
                        ports = info.get("ports", [])
                        has_monitor_port = False
                        for p in ports:
                            alias = p.get("alias", "").lower()
                            name = p.get("name", "").lower()
                            if "monitor" in alias or "monitor" in name:
                                has_monitor_port = True
                                break
                        # 找 sinks 的所有 .monitor 别名 — 也尝试 sink name + ".monitor"
                        # 即使 pw-dump 没列出 monitor port,实际 ffmpeg 抓得到
                        monitor_name = f"{sink_name}.monitor"
                        sources.append({
                            "name": monitor_name,
                            "description": f"{sink_desc} monitor",
                            "backend": "pipewire",
                        })
                        if not has_monitor_port:
                            # pw-dump 没列 monitor port,但 ffmpeg 可能能抓
                            # 不去重,让 ffmpeg 试
                            pass
        except Exception:
            pass

    # === PulseAudio 降级(pactl list sources 拿 monitor source) ===
    if not sources and shutil.which("pactl"):
        try:
            r = subprocess.run(
                ["pactl", "list", "sources", "short"],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0:
                for line in r.stdout.strip().split("\n"):
                    parts = line.split("\t")
                    if len(parts) >= 2 and "monitor" in parts[1].lower():
                        sources.append({
                            "name": parts[1],
                            "description": f"PulseAudio monitor {parts[1]}",
                            "backend": "pulseaudio",
                        })
        except Exception:
            pass

    # === 探测 fallback:用 ffmpeg 试常见 monitor 源名 ===
    # 某些 PipeWire 配置下 pw-dump 不列 monitor 源,但 ffmpeg 能抓
    # 这里尝试一组硬编码的常见名字
    if not sources and shutil.which("ffmpeg"):
        common_names = [
            "auto_null.monitor",
            "default.monitor",
            "alsa_output.default.monitor",
        ]
        for name in common_names:
            if _probe_source_via_ffmpeg(name):
                sources.append({
                    "name": name,
                    "description": f"{name} (probed via ffmpeg)",
                    "backend": "pipewire",
                })
                break  # 只加第一个能用的

    return sources


def _probe_source_via_ffmpeg(source_name: str, backend: str = "pulse") -> bool:
    """用 ffmpeg 短时抓 0.2s 探测源是否可用"""
    if backend == "pipewire":
        url = f"pipewire://{source_name}"
    else:
        url = f"pulse://{source_name}"
    try:
        r = subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error",
                "-f", backend,
                "-i", url,
                "-t", "0.2",
                "-ar", "16000", "-ac", "1",
                "-f", "wav", "/dev/null",
            ],
            capture_output=True, timeout=3,
        )
        return r.returncode == 0
    except Exception:
        return False


def _capture_via_pw_cli(source_name: str, output_wav: Path, duration_sec: float) -> bool:
    """用 pw-cli + 简单 cat 不行;实际用 ffmpeg 抓 pipewire 协议最稳"""
    return _capture_via_ffmpeg(source_name, output_wav, duration_sec, backend="pipewire")


def _capture_via_parec(source_name: str, output_wav: Path, duration_sec: float) -> bool:
    """PulseAudio:parec 抓 monitor source + sox 转 wav"""
    try:
        # parec 输出 raw PCM:s16le 44100Hz stereo
        with tempfile.NamedTemporaryFile(suffix=".raw", delete=False) as raw_f:
            raw_path = Path(raw_f.name)
        try:
            r = subprocess.run(
                [
                    "parec", "--device=" + source_name,
                    "--format=s16le", "--channels=2", "--rate=44100",
                    "--file-format=raw",
                ],
                stdout=open(raw_path, "wb"),
                stderr=subprocess.DEVNULL,
                timeout=duration_sec + 5,
            )
            if r.returncode != 0:
                return False
            # sox 转 wav 16k mono
            r2 = subprocess.run(
                ["sox", "-r", "44100", "-e", "signed", "-b", "16",
                 "-c", "2", str(raw_path),
                 "-r", "16000", "-c", "1", str(output_wav)],
                capture_output=True, timeout=30,
            )
            return r2.returncode == 0 and output_wav.exists()
        finally:
            raw_path.unlink(missing_ok=True)
    except Exception:
        return False


def _capture_via_ffmpeg(
    source_name: str,
    output_wav: Path,
    duration_sec: float,
    backend: str = "pulse",
) -> bool:
    """ffmpeg 直接抓 pulse:// 或 pipewire:// monitor 源,转 wav 16k mono"""
    if backend == "pipewire":
        url = f"pipewire://{source_name}"
    else:
        url = f"pulse://{source_name}"

    try:
        r = subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error",
                "-f", backend,
                "-i", url,
                "-t", str(duration_sec),
                "-ar", "16000", "-ac", "1",
                "-f", "wav", str(output_wav),
            ],
            capture_output=True, timeout=duration_sec + 30,
        )
        if r.returncode != 0:
            # debug: 把 ffmpeg 错误打到 stdout 方便诊断
            import sys as _sys
            print(f"[loopback] ffmpeg failed: {r.stderr.decode()[:300]}", file=_sys.stderr)
            return False
        return output_wav.exists() and output_wav.stat().st_size > 44
    except Exception:
        return False


def capture_loopback(
    duration_sec: float = 60.0,
    output_path: Optional[Path] = None,
    source_name: Optional[str] = None,
    sample_rate: int = 16000,
    silence_fallback: bool = True,
) -> Path:
    """从 monitor 源捕获系统音频

    Args:
        duration_sec: 捕获时长(秒)
        output_path: 输出 wav 路径(默认 tmp)
        source_name: 指定 monitor source,None = 自动选第一个
        sample_rate: 输出采样率(funasr 默认 16k)
        silence_fallback: 没有可用源时是否生成静音占位(开发环境用)

    Returns:
        wav 文件路径

    Raises:
        RuntimeError: 捕获失败且 silence_fallback=False
    """
    if output_path is None:
        output_path = Path(tempfile.mktemp(suffix=".wav", prefix="vpbuddy_loopback_"))
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # 1. 选 source
    sources = list_monitor_sources()
    if source_name is None:
        if sources:
            source_name = sources[0]["name"]
            print(f"[loopback] 自动选 monitor source: {source_name} ({sources[0]['backend']})")
        else:
            if silence_fallback:
                print("[loopback] ⚠️  无可用 monitor source,生成静音占位(开发环境)")
                return _write_silence_wav(output_path, duration_sec, sample_rate)
            else:
                raise RuntimeError("无可用 monitor source,设置 silence_fallback=True 或检查音频设备")

    # 2. 选 backend
    has_pw = shutil.which("pw-dump") is not None
    has_pa = shutil.which("pactl") is not None
    has_ffmpeg = shutil.which("ffmpeg") is not None
    has_parec = shutil.which("parec") is not None
    has_sox = shutil.which("sox") is not None

    # 3. 尝试捕获(优先级:pulse:// (PipeWire-pulse 兼容层) > parec+sox)
    # 注:ffmpeg 的 pipewire:// 协议需要显式 --enable-libpipewire,大多数发行版只启用了 libpulse
    #     PipeWire 跑 pipewire-pulse 暴露标准 PulseAudio 接口,所以 pulse:// 永远可用
    success = False
    if has_ffmpeg:
        success = _capture_via_ffmpeg(source_name, output_path, duration_sec, "pulse")
    if not success and has_parec and has_sox:
        success = _capture_via_parec(source_name, output_path, duration_sec)

    # 4. 失败 fallback
    if not success:
        if silence_fallback:
            print(f"[loopback] ⚠️  capture 失败,生成静音占位: {output_path}")
            return _write_silence_wav(output_path, duration_sec, sample_rate)
        else:
            raise RuntimeError(f"loopback capture failed for source: {source_name}")

    print(f"[loopback] ✅ 捕获 {duration_sec}s → {output_path} ({output_path.stat().st_size}B)")
    return output_path


def _write_silence_wav(path: Path, duration_sec: float, sample_rate: int = 16000) -> Path:
    """生成静音 wav(开发环境 fallback 或单元测试用)"""
    n_samples = int(duration_sec * sample_rate)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00\x00" * n_samples)
    return path


def list_audio_capabilities() -> Dict[str, Any]:
    """诊断:列出当前系统音频能力(供 CLI / UI 展示)"""
    return {
        "platform": platform.system(),
        "tools": {
            "pw-dump": shutil.which("pw-dump"),
            "pactl": shutil.which("pactl"),
            "ffmpeg": shutil.which("ffmpeg"),
            "parec": shutil.which("parec"),
            "sox": shutil.which("sox"),
        },
        "monitor_sources": list_monitor_sources(),
        "macos_supported": _IS_MACOS,
        "windows_supported": _IS_WINDOWS,
    }
