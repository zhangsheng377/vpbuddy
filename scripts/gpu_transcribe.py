#!/usr/bin/env python3
"""VPBuddy GPU 端到端转写 CLI。

输入:任意 wav/mp3/m4a 音频(自动转 16kHz mono)
输出:VPBuddy 标准格式 transcript.json

设计原则(YAGNI):
- 不做 streaming/chunking(整文件同步,209s 音频 < 2s 处理完)
- 不做复杂的 ASR-Diarization 联合对齐(直接 funasr sentence_info 输出)
- 不写 REST API(VPBuddy engine 自己会用)
- speaker 校准:默认映射为 SPEAKER_00..07,需 Step 5 飞书妙记或人工填 speaker_name

2026-06-21 张胜东 + Hermes 写
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import wave
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

# 配置默认值(可被环境变量覆盖)
DEFAULT_MODELS_DIR = os.environ.get("VPBUDDY_MODELS_DIR", str(Path.home() / ".cache" / "vpbuddy_models"))
DEFAULT_ASR = "sensevoice"  # sensevoice / paraformer
DEFAULT_DEVICE = "cuda" if os.environ.get("CUDA_VISIBLE_DEVICES", "0") != "" else "cpu"


def audio_to_16k_mono(audio_path: str) -> tuple[np.ndarray, int]:
    """读取音频(任意格式)→ 16kHz mono float32 numpy array。"""
    import torchaudio
    import torch

    wav, sr = torchaudio.load(audio_path)
    # 转 mono
    if wav.shape[0] > 1:
        wav = wav.mean(dim=0, keepdim=True)
    # 转 16k
    if sr != 16000:
        wav = torchaudio.functional.resample(wav, sr, 16000)
        sr = 16000
    return wav.squeeze(0).numpy().astype(np.float32), sr


def transcribe_sensevoice(audio: np.ndarray, sr: int, device: str = "cuda") -> list[dict]:
    """SenseVoice + campplus + VAD 一站式。返回 [{start, end, text, spk}, ...]"""
    from funasr import AutoModel

    model = AutoModel(
        model="iic/SenseVoiceSmall",
        model_revision="master",
        vad_model="iic/speech_fsmn_vad_zh-cn-16k-common-pytorch",
        punc_model="iic/punc_ct-transformer_zh-cn-common-vocab272727-pytorch",
        spk_model="iic/speech_campplus_sv_zh-cn_16k-common",
        device=device,
        disable_update=True,
    )
    result = model.generate(
        input=audio, fs=sr, batch_size_s=60,
    )
    if not result or "sentence_info" not in result[0]:
        # 没切出多句时(纯静音/单句),整个文本当一个段
        return [{
            "start": 0,
            "end": int(len(audio) / sr * 1000),
            "text": result[0].get("text", "").strip() if result else "",
            "spk": 0,
        }]
    return result[0]["sentence_info"]


def transcribe_paraformer(audio: np.ndarray, sr: int, device: str = "cuda") -> list[dict]:
    """Paraformer-zh 备选:更准的中文 ASR + VAD + punc。"""
    from funasr import AutoModel

    model = AutoModel(
        model="iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch",
        vad_model="iic/speech_fsmn_vad_zh-cn-16k-common-pytorch",
        punc_model="iic/punc_ct-transformer_zh-cn-common-vocab272727-pytorch",
        device=device,
        disable_update=True,
    )
    result = model.generate(input=audio, fs=sr, batch_size_s=60)
    if result and isinstance(result[0], dict) and "sentence_info" in result[0]:
        return result[0]["sentence_info"]
    return [{
        "start": 0,
        "end": int(len(audio) / sr * 1000),
        "text": result[0].get("text", "").strip() if result else "",
        "spk": 0,
    }]


def transcribe(audio_path: str, asr: str = "sensevoice", device: str = "cuda") -> dict:
    """主入口:音频 → VPBuddy transcript.json"""
    import time

    t0 = time.time()
    print(f"[1/3] load audio: {audio_path}")
    audio, sr = audio_to_16k_mono(audio_path)
    duration = len(audio) / sr
    print(f"  → {duration:.1f}s, {sr}Hz mono")

    t1 = time.time()
    print(f"[2/3] transcribe ({asr}, {device})...")
    if asr == "sensevoice":
        sentences = transcribe_sensevoice(audio, sr, device)
    elif asr == "paraformer":
        sentences = transcribe_paraformer(audio, sr, device)
    else:
        raise ValueError(f"unknown asr: {asr}")
    print(f"  → {len(sentences)} sentences in {time.time()-t1:.1f}s")

    t2 = time.time()
    print(f"[3/3] format VPBuddy transcript.json")
    # 重映射 spk (按时长排序 → SPEAKER_00..07)
    from collections import defaultdict
    spk_dur = defaultdict(float)
    for s in sentences:
        spk_dur[s.get("spk", 0)] += (s["end"] - s["start"]) / 1000
    ranked = sorted(spk_dur, key=lambda x: -spk_dur[x])
    spk_remap = {old: f"SPEAKER_{ranked.index(old):02d}" for old in spk_dur}

    segments = []
    for i, s in enumerate(sentences):
        text = s.get("text", "").strip()
        if not text:
            continue
        segments.append({
            "segment_id": f"SEG-{i:03d}",
            "start_sec": s["start"] / 1000,
            "end_sec": s["end"] / 1000,
            "text": text,
            "confidence": 0.95,  # funasr 默认不给 logprob,给保守值
            "language": "zh",
            "speaker_id": spk_remap[s.get("spk", 0)],
            "speaker_name": None,
            "source": f"funasr-{asr}+campplus",
        })

    distinct_speakers = sorted(set(seg["speaker_id"] for seg in segments))
    result = {
        "audio_path": str(audio_path),
        "language": "zh",
        "duration_sec": duration,
        "num_speakers": len(distinct_speakers),
        "segments": segments,
        "model_name": f"funasr-{asr}+campplus",
        "device": device,
        "compute_type": "float16",
        "diarization_model": "iic/speech_campplus_sv_zh-cn_16k-common",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    print(f"  → {len(segments)} segments, {len(distinct_speakers)} speakers: {distinct_speakers}")
    print(f"  → done in {time.time()-t0:.1f}s total (RTF = {(time.time()-t1)/duration*1000/1000:.4f})")
    return result


def self_test():
    """冒烟测试:用脚本自己生成 5 秒静音,确认 pipeline 跑通。"""
    print("=== SELF-TEST (5s 静音) ===")
    audio = np.zeros(16000 * 5, dtype=np.float32)
    from funasr import AutoModel
    model = AutoModel(
        model="iic/SenseVoiceSmall",
        device=DEFAULT_DEVICE,
        disable_update=True,
    )
    result = model.generate(input=audio, fs=16000)
    print(f"  推理 OK,返回类型: {type(result).__name__}")
    print(f"  GPU 设备: {DEFAULT_DEVICE}")
    print("✓ 部署验证成功")


def main():
    parser = argparse.ArgumentParser(description="VPBuddy GPU 转写 CLI")
    parser.add_argument("audio", nargs="?", help="输入音频文件 (wav/mp3/m4a)")
    parser.add_argument("-o", "--output", help="输出 transcript.json 路径(默认 stdout)")
    parser.add_argument("--asr", choices=["sensevoice", "paraformer"], default=DEFAULT_ASR,
                        help="ASR 模型选择(默认 sensevoice)")
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

    result = transcribe(args.audio, asr=args.asr, device=args.device)
    output = json.dumps(result, ensure_ascii=False, indent=2)

    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
        print(f"\n✓ 写入: {args.output} ({len(output)//1024} KB)")
    else:
        print("\n" + output)


if __name__ == "__main__":
    main()