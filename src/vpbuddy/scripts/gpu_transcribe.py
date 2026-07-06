#!/usr/bin/env python3
"""VPBuddy GPU 端到端转写 CLI。

输入:任意 wav/mp3/m4a 音频(自动转 16kHz mono)
输出:VPBuddy 标准格式 transcript.json

设计原则(YAGNI):
- 不做 streaming/chunking(整文件同步,209s 音频 < 2s 处理完)
- 不做复杂的 ASR-Diarization 联合对齐(直接 funasr sentence_info 输出)
- 不写 REST API(VPBuddy engine 自己会用)
- speaker 校准:按时长排序重映射为 SPEAKER_00..07

P0 修复 (2026-07-04): AutoModel 进程级单例缓存 + 预热
  - _get_model() 缓存 4 个模型(ASR+VAD+punc+spk)的 AutoModel 实例
  - warmup_models() 可在服务器启动时调用,提前加载
  - transcribe() 不再每次新建 AutoModel
    
2026-06-21 张胜东 + Hermes 写
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from datetime import datetime, timezone

UTC = timezone.utc
from pathlib import Path

import numpy as np

# === 配置 ===
# funasr 1.1.18 用短名(不能用 iic/xxx 完整 ModelScope id),见 踩坑记录.md §10
DEFAULT_FUNASR_MODEL = os.environ.get("VPBUDDY_ASR", "paraformer-zh")  # paraformer-zh / sensevoice
DEFAULT_VAD = "fsmn-vad"
DEFAULT_PUNC = "ct-punc"
DEFAULT_SPK = "cam++"
DEFAULT_DEVICE = "cuda" if os.environ.get("CUDA_VISIBLE_DEVICES", "0") != "" else "cpu"
DEFAULT_BATCH_SIZE_S = int(os.environ.get("VPBUDDY_ASR_BATCH_SEC", "60"))  # funasr batch 窗口

# === P0 修复: 模块级 AutoModel 单例缓存 ===
_ASR_CACHE: dict[str, object] = {}
_ASR_CACHE_LOCK = threading.Lock()


def _asr_cache_key(asr: str, vad: str, punc: str, spk: str, device: str) -> str:
    return f"{asr}|{vad}|{punc}|{spk}|{device}"


def _get_model(
    asr: str = DEFAULT_FUNASR_MODEL,
    vad: str = DEFAULT_VAD,
    punc: str = DEFAULT_PUNC,
    spk: str = DEFAULT_SPK,
    device: str = DEFAULT_DEVICE,
):
    """获取缓存的 funasr AutoModel 实例 (线程安全单例).

    第 1 次调用加载 4 个模型进 GPU (~28s), 之后直接返回缓存实例 (< 0.01s).
    不同参数组合独立缓存 (但生产环境只用一套配置).
    """
    cache_key = _asr_cache_key(asr, vad, punc, spk, device)
    if cache_key not in _ASR_CACHE:
        with _ASR_CACHE_LOCK:
            # 双重检查锁
            if cache_key not in _ASR_CACHE:
                from funasr import AutoModel

                # 短名映射
                asr_short = (
                    "paraformer-zh"
                    if asr in ("paraformer", "paraformer-zh")
                    else "sensevoice-small"
                    if asr in ("sensevoice", "SenseVoiceSmall", "iic/SenseVoiceSmall")
                    else asr
                )
                print(f"[gpu_transcribe] 加载 ASR 模型: ASR={asr_short} VAD={vad} PUNC={punc} SPK={spk} DEVICE={device}")
                model = AutoModel(
                    model=asr_short,
                    vad_model=vad,
                    punc_model=punc,
                    spk_model=spk,
                    device=device,
                    disable_update=True,
                )
                _ASR_CACHE[cache_key] = model
                print(f"[gpu_transcribe] 模型加载完成, 缓存 key={cache_key}")
    return _ASR_CACHE[cache_key]


def warmup_models(
    asr: str = DEFAULT_FUNASR_MODEL,
    vad: str = DEFAULT_VAD,
    punc: str = DEFAULT_PUNC,
    spk: str = DEFAULT_SPK,
    device: str = DEFAULT_DEVICE,
) -> None:
    """预热模型: 服务器启动时调用, 避免首次请求等 28s.

    调用方示例:
        from vpbuddy.scripts.gpu_transcribe import warmup_models
        warmup_models()  # 放在 ui_server 启动代码中

    同时也跑一次空音频推理验证 GPU 可用.
    """
    import time
    t0 = time.time()
    model = _get_model(asr, vad, punc, spk, device)
    # 跑一次空推理, 触发 CUDA 初始化 + 模型 warmup
    dummy = np.zeros(16000 * 1, dtype=np.float32)
    _ = model.generate(input=dummy, fs=16000)
    elapsed = time.time() - t0
    print(f"[gpu_transcribe] 预热完成: {elapsed:.1f}s, GPU 设备={device}")


def clear_cache() -> None:
    """清空模型缓存 (测试/重载用)."""
    global _ASR_CACHE
    _ASR_CACHE = {}


# ============================================================
# 核心功能 (无状态函数)
# ============================================================

def audio_to_16k_mono(audio_path: str) -> tuple[np.ndarray, int]:
    """读取音频(任意格式)→ 16kHz mono float32 numpy array。"""
    import torchaudio

    wav, sr = torchaudio.load(audio_path)
    if wav.shape[0] > 1:
        wav = wav.mean(dim=0, keepdim=True)
    if sr != 16000:
        wav = torchaudio.functional.resample(wav, sr, 16000)
        sr = 16000
    return wav.squeeze(0).numpy().astype(np.float32), sr


def transcribe(
    audio: np.ndarray,
    sr: int,
    asr: str = DEFAULT_FUNASR_MODEL,
    vad: str = DEFAULT_VAD,
    punc: str = DEFAULT_PUNC,
    spk: str = DEFAULT_SPK,
    device: str = DEFAULT_DEVICE,
) -> list[dict]:
    """funasr 一站式: ASR + VAD + punc + 说话人 → [{start, end, text, spk}, ...]

    P0 修复: 使用 _get_model() 缓存, 不再每次新建 AutoModel.
    """
    model = _get_model(asr=asr, vad=vad, punc=punc, spk=spk, device=device)

    # P1 修复: 重置 streaming VAD 内部 cache, 避免跨次调用形状不匹配
    # RuntimeError: Sizes of tensors must match except in dimension 1.
    # Expected size 2 but got size 1 for tensor number 1 in the list.
    # fsmn-vad 是流式 VAD, model.generate() 后 cache 里残留了上一段音频的帧数状态
    if hasattr(model, "vad_model") and hasattr(model.vad_model, "cache"):
        model.vad_model.cache = {}

    result = model.generate(input=audio, fs=sr, batch_size_s=DEFAULT_BATCH_SIZE_S)

    if not result:
        return []
    # 优先 sentence_info(切句模式)
    if isinstance(result[0], dict) and "sentence_info" in result[0]:
        return result[0]["sentence_info"]
    # fallback: 整个文本当一句
    return [{
        "start": 0,
        "end": int(len(audio) / sr * 1000),
        "text": result[0].get("text", "").strip(),
        "spk": 0,
    }]


def process(audio_path: str, asr: str = DEFAULT_FUNASR_MODEL, device: str = DEFAULT_DEVICE) -> dict:
    """主入口:音频 → VPBuddy transcript.json"""
    import time

    t0 = time.time()
    print(f"[1/3] load audio: {audio_path}")
    audio, sr = audio_to_16k_mono(audio_path)
    duration = len(audio) / sr
    print(f"  → {duration:.1f}s, {sr}Hz mono")

    t1 = time.time()
    print(f"[2/3] transcribe ({asr}, {device})...")
    sentences = transcribe(audio, sr, asr=asr, device=device)
    print(f"  → {len(sentences)} sentences in {time.time()-t1:.1f}s")

    time.time()
    print("[3/3] format VPBuddy transcript.json")
    from collections import defaultdict
    spk_dur = defaultdict(float)
    for s in sentences:
        spk_dur[s.get("spk", 0)] += (s["end"] - s["start"]) / 1000
    ranked = sorted(spk_dur, key=lambda x: -spk_dur[x])
    spk_remap = {old: f"SPEAKER_{ranked.index(old):02d}" for old in spk_dur}

    segments = []
    for i, s in enumerate(sentences):
        text = s.get("text", "").strip() or s.get("sentence", "").strip()
        if not text:
            continue
        segments.append({
            "segment_id": f"SEG-{i:03d}",
            "start_sec": s["start"] / 1000,
            "end_sec": s["end"] / 1000,
            "text": text,
            "confidence": 0.95,
            "language": "zh",
            "speaker_id": spk_remap[s.get("spk", 0)],
            "speaker_name": None,
            "source": f"funasr-{asr}+{DEFAULT_SPK}",
        })

    distinct_speakers = sorted(set(seg["speaker_id"] for seg in segments))
    result = {
        "audio_path": str(audio_path),
        "language": "zh",
        "duration_sec": duration,
        "num_speakers": len(distinct_speakers),
        "segments": segments,
        "model_name": f"funasr-{asr}+{DEFAULT_SPK}",
        "device": device,
        "compute_type": "float16",
        "diarization_model": DEFAULT_SPK,
        "created_at": datetime.now(UTC).isoformat(),
    }
    print(f"  → {len(segments)} segments, {len(distinct_speakers)} speakers: {distinct_speakers}")
    print(f"  → done in {time.time()-t0:.1f}s total (RTF = {(time.time()-t1)/duration*1000:.4f})")
    return result


def self_test():
    """冒烟测试:用脚本自己生成 5 秒静音,确认 pipeline 跑通。"""
    print("=== SELF-TEST (5s 静音) ===")
    audio = np.zeros(16000 * 5, dtype=np.float32)
    model = _get_model(vad=DEFAULT_VAD, spk="")  # 仅 ASR + VAD, 无 spk (更快)
    result = model.generate(input=audio, fs=16000)
    print(f"  推理 OK,返回类型: {type(result).__name__}")
    print(f"  GPU 设备: {DEFAULT_DEVICE}")
    print(f"  ASR 模型: {DEFAULT_FUNASR_MODEL} (funasr 1.1.18 短名)")
    print("✓ 部署验证成功")


def main():
    parser = argparse.ArgumentParser(description="VPBuddy GPU 转写 CLI")
    parser.add_argument("audio", nargs="?", help="输入音频文件 (wav/mp3/m4a)")
    parser.add_argument("-o", "--output", help="输出 transcript.json 路径(默认 stdout)")
    parser.add_argument("--asr", choices=["paraformer-zh", "sensevoice-small"],
                        default=DEFAULT_FUNASR_MODEL, help="ASR 模型(默认 paraformer-zh)")
    parser.add_argument("--device", default=DEFAULT_DEVICE, help="cuda / cpu (默认 cuda)")
    parser.add_argument("--self-test", action="store_true", help="只跑冒烟测试,不读音频")
    args = parser.parse_args()

    if args.self_test:
        self_test()
        return

    if not args.audio:
        parser.error("需要指定音频文件(或用 --self-test)")

    if not os.path.exists(args.audio):
        print(f"✗ 文件不存在: {args.audio}", file=sys.stderr)
        sys.exit(1)

    result = process(args.audio, asr=args.asr, device=args.device)
    output = json.dumps(result, ensure_ascii=False, indent=2)

    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
        print(f"\n✓ 写入: {args.output} ({len(output)//1024} KB)")
    else:
        print("\n" + output)


if __name__ == "__main__":
    main()
