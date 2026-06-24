#!/usr/bin/env python3
"""
VPBuddy 流式客户端 (Python prototype)
- 模拟 Tauri 客户端: 抓音频 → 30s 切片 → multipart POST 到 GPU
- 验证 GPU 端流式端点真能持续接收 + 累加
- 用法: python3 stream_client.py <audio.wav> [chunk_sec=30]
"""
import argparse
import io
import time
import wave
import requests
import soundfile as sf
import numpy as np


def wav_chunk_bytes(samples: np.ndarray, sr: int) -> bytes:
    """float [-1, 1] samples → 16kHz mono WAV bytes (16-bit PCM)"""
    # ⚠️ 必须先 *32767 再 astype, 直接 astype(np.int16) 会把 [-0.61, 0.75] 截成 0/-1 = 静音!
    i16 = (np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(i16.tobytes())
    return buf.getvalue()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("audio", help="输入音频文件 (任意格式, 自动转 16kHz mono)")
    parser.add_argument("--gpu", default="http://localhost:8765", help="GPU server URL")
    parser.add_argument("--chunk", type=int, default=30, help="chunk 时长(秒)")
    parser.add_argument("--max-chunks", type=int, default=0, help="最多推几个 chunk (0=全部)")
    args = parser.parse_args()

    # 1. 加载音频 + 转 16kHz mono
    print(f"[1] 加载 {args.audio} ...")
    data, sr = sf.read(args.audio)
    if data.ndim > 1:
        data = data.mean(axis=1)
    if sr != 16000:
        import torchaudio
        wav_t = torch.from_numpy(data).float().unsqueeze(0)
        wav_t = torchaudio.functional.resample(wav_t, sr, 16000)
        data = wav_t.squeeze(0).numpy()
        sr = 16000
    total_sec = len(data) / sr
    print(f"    {total_sec:.1f}s @ {sr}Hz")

    # 2. stream_start
    print(f"[2] stream_start ...")
    r = requests.post(f"{args.gpu}/api/meetings/stream_start", json={"platform": "python_client"})
    r.raise_for_status()
    data_json = r.json()
    mid = data_json["meeting_id"]
    print(f"    meeting_id: {mid}")

    # 3. 切片 + 推
    chunk_samples = sr * args.chunk
    n_chunks = int(np.ceil(len(data) / chunk_samples))
    if args.max_chunks > 0:
        n_chunks = min(n_chunks, args.max_chunks)
    print(f"[3] 推 {n_chunks} 个 {args.chunk}s chunk ...")

    for i in range(n_chunks):
        s = i * chunk_samples
        e = min((i + 1) * chunk_samples, len(data))
        chunk = data[s:e]
        wav = wav_chunk_bytes(chunk, sr)

        t0 = time.time()
        r = requests.post(
            f"{args.gpu}/api/meetings/{mid}/stream_chunk",
            files={"audio": ("chunk.wav", wav, "audio/wav")},
        )
        r.raise_for_status()
        d = r.json()
        dt = time.time() - t0
        segs = d.get("new_segments", [])
        items = d.get("state_items", {})
        print(f"    chunk {i+1}/{n_chunks}: {len(segs)} segs ({dt:.1f}s) state={items} total_segs={sum(1 for _ in segs)}")

        # 打印首段
        if segs:
            print(f"      1st: [{segs[0]['speaker_id']}] {segs[0]['text'][:60]}")

    print()
    print(f"[done] meeting_id: {mid}")
    print(f"       推 {n_chunks} 个 chunk 完毕")
    print(f"       6 docs 由 controller 后台异步生成 (~30-60s 后可查)")


if __name__ == "__main__":
    main()
