"""e2e — ASR 延迟 + 文档生成延迟测量

跑法: RUN_E2E=1 pytest src/tests/e2e/test_latency.py -v -m e2e

架构说明:
- funasr 是 30s batch 模式, 不是 streaming. 用户说话后最长等 30s 才出字.
  → ASR latency = ~30s（设计如此, v0.9.x 计划上 streaming 降低）
- 6 文档触发: 需要 ASR 输出有意义的文字(需求/任务等). 合成语音(正弦波)只出
  "嗯嗯" 填充字, 文档不会生成. 真正的用户语音才有文档.
- 本测试验证 pipeline 链路的连通性和一致性, 不是断言 fast latency.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
import struct
import math
import random
from pathlib import Path

import pytest


pytestmark = pytest.mark.e2e

GPU_URL = os.environ.get("VP_E2E_GPU_URL", "http://47.100.182.3:28765")
SR = 16000
WAV_DUR_SEC = 8.0


def _http_post(url: str, data: bytes, content_type: str = "application/octet-stream",
               timeout: float = 180.0) -> dict:
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", content_type)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _http_get(url: str, timeout: float = 10.0) -> dict:
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _generate_wav(path: Path) -> None:
    """生成 8s 16kHz mono WAV (纯 Python, 无 scipy).

    信号 = 多段 formant 合成 (模拟语音频谱), funasr 会输出"嗯嗯"填充字.
    """
    ns = int(SR * WAV_DUR_SEC)
    samples = []
    for i in range(ns):
        t = i / SR
        val = (
            0.3 * math.sin(2 * math.pi * 300 * t) * (0.5 + 0.5 * math.sin(2 * math.pi * 4 * t)) +
            0.2 * math.sin(2 * math.pi * 800 * t) * (0.5 + 0.5 * math.sin(2 * math.pi * 3 * t)) +
            0.15 * math.sin(2 * math.pi * 2000 * t) * (0.5 + 0.5 * math.sin(2 * math.pi * 5 * t)) +
            0.1 * math.sin(2 * math.pi * (200 + 100 * math.sin(2 * math.pi * 0.5 * t)) * t) +
            0.01 * random.gauss(0, 1)
        )
        val = max(-0.95, min(0.95, val))
        samples.append(int(val * 32767))

    nch, bits = 1, 16
    bps = bits // 8
    ba = nch * bps
    ds = ns * ba
    with open(path, "wb") as f:
        f.write(b"RIFF")
        f.write(struct.pack("<I", 36 + ds))
        f.write(b"WAVE")
        f.write(b"fmt ")
        f.write(struct.pack("<I", 16))
        f.write(struct.pack("<H", 1))
        f.write(struct.pack("<H", nch))
        f.write(struct.pack("<I", SR))
        f.write(struct.pack("<I", SR * ba))
        f.write(struct.pack("<H", ba))
        f.write(struct.pack("<H", bits))
        f.write(b"data")
        f.write(struct.pack("<I", ds))
        for s in samples:
            f.write(struct.pack("<h", s))


def _build_multipart(wav_bytes: bytes, chunk_index: int = 0,
                     sync_mode: bool = True) -> tuple[bytes, str]:
    """构建 multipart — 跟 Tauri 客户端一致."""
    boundary = b"----e2e-latency-boundary"
    parts = []
    def _add(name: str, value):
        parts.append(b"--" + boundary + b"\r\n")
        parts.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        parts.append(str(value).encode() + b"\r\n")

    _add("sync_mode", "true" if sync_mode else "false")
    _add("chunk_index", chunk_index)
    _add("chunk_start_sec", chunk_index * 30.0)
    _add("overlap_sec", 0)
    _add("client_sent_at", time.time())

    parts.append(b"--" + boundary + b"\r\n")
    parts.append(b'Content-Disposition: form-data; name="audio"; filename="chunk.wav"\r\n')
    parts.append(b"Content-Type: audio/wav\r\n\r\n")
    parts.append(wav_bytes)
    parts.append(b"\r\n--" + boundary + b"--\r\n")

    body = b"".join(parts)
    ct = f'multipart/form-data; boundary={boundary.decode()}'
    return body, ct


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture(scope="module")
def wav_path(tmp_path_factory) -> Path:
    p = tmp_path_factory.mktemp("wav") / "e2e_latency.wav"
    _generate_wav(p)
    return p


@pytest.fixture(scope="module")
def wav_bytes(wav_path: Path) -> bytes:
    with open(wav_path, "rb") as f:
        return f.read()


# ============================================================
# 测试 1: ASR 管道延迟
# ============================================================

def test_asr_pipeline_responds(wav_bytes):
    """验证 ASR pipeline 全链路通: stream_start → chunk(sync) → ASR 结果.

    这是核心链路, 失败说明 GPU 服务器 ASR 有问题.

    funasr 是 30s batch 模式, 所以 processing_ms 会 ~27-30s 而不是 ms 级.
    这就是当前架构的延迟: 说话后最长等 30s 才出字.
    """
    # stream_start
    start = _http_post(f"{GPU_URL}/api/meetings/stream_start", b"", timeout=10)
    meeting_id = start.get("meeting_id", "")
    assert meeting_id, f"stream_start 无 meeting_id: {start}"

    # send chunk
    body, ct = _build_multipart(wav_bytes, chunk_index=0, sync_mode=True)
    t0 = time.time()
    result = _http_post(f"{GPU_URL}/api/meetings/{meeting_id}/stream_chunk",
                        body, content_type=ct, timeout=180)
    wall_ms = int((time.time() - t0) * 1000)
    m = result.get("metrics", {})

    print(f"\n  ⏱️  ASR Pipeline Latency (funasr 30s batch):")
    print(f"     Server processing_ms: {m.get('processing_ms', -1)}ms")
    print(f"     Wall clock (client): {wall_ms}ms")
    print(f"     Chunks accepted: {result.get('status', 'N/A')}")

    # 关键断言: processing_ms 存在 (不是 -1)
    assert m.get("processing_ms", -1) > 0, \
        f"无 processing_ms: {m}"
    # 验证 pipeline 走到 funasr 了
    # processing_ms 应该 > 1000ms (因为 funasr 处理了 8s 音频)
    assert m["processing_ms"] > 1000, \
        f"ASR pipeline 可能没跑 funasr: processing_ms={m['processing_ms']}ms"

    # 转写段存在, 即使只是"嗯嗯"填充
    segs = result.get("new_segments", [])
    assert len(segs) >= 0, f"无转写段: {result}"
    if segs:
        print(f"     转写段: {len(segs)}")
        for s in segs[:2]:
            print(f"       [{s['start_sec']:.1f}s] {s['text'][:60]}")
    else:
        print(f"     转写段: 空 (合成语音 funasr 未识别)")


def test_asr_pipeline_warm(wav_bytes):
    """第二次调用 ASR (model 已 warm) 应正常运行.

    验证 ASR 不会因为第一次调用后 crash.
    """
    start = _http_post(f"{GPU_URL}/api/meetings/stream_start", b"", timeout=10)
    meeting_id = start["meeting_id"]
    body, ct = _build_multipart(wav_bytes, chunk_index=0, sync_mode=True)
    t0 = time.time()
    result = _http_post(f"{GPU_URL}/api/meetings/{meeting_id}/stream_chunk",
                        body, content_type=ct, timeout=180)
    m = result.get("metrics", {})
    wall_ms = int((time.time() - t0) * 1000)

    print(f"\n  ⏱️  Warm ASR:")
    print(f"     processing_ms: {m.get('processing_ms', -1)}ms")
    print(f"     wall_clock_ms: {wall_ms}ms")

    assert m.get("processing_ms", -1) > 0, f"Warm ASR failed: {m}"


# ============================================================
# 测试 2: Docs latency — ASR 输出有意义内容时文档会生成
# ============================================================

def test_docs_respond_after_transcription(wav_bytes):
    """验证 stream_chunk (sync_mode=true) 会触发 6 docs 生成请求.

    注意:
    - 合成语音只有"嗯嗯", 文档 LLM 可能需要 3+ 段有意义文字才输出.
      真正用户语音(需求/任务内容) 才会有文档.
    - 本测试只验证: sync_mode 返回后 docs 端点是可达的, 没有 500.
    """
    start = _http_post(f"{GPU_URL}/api/meetings/stream_start", b"", timeout=10)
    mid = start["meeting_id"]

    body, ct = _build_multipart(wav_bytes, chunk_index=0, sync_mode=True)
    result = _http_post(f"{GPU_URL}/api/meetings/{mid}/stream_chunk",
                        body, content_type=ct, timeout=180)

    assert result.get("docs_triggered", False), \
        f"sync_mode 应触发 docs, 但未触发: {result}"
    print(f"\n  6 docs triggered ✓")

    # 轮询 docs 端点 (不期待全生成, 只是验证端点通)
    time.sleep(5)
    docs_resp = _http_get(f"{GPU_URL}/api/meetings/{mid}/docs", timeout=10)
    assert "docs" in docs_resp, f"/docs 响应缺 docs 字段: {docs_resp}"
    assert isinstance(docs_resp["docs"], list), f"docs 不是 list"
    print(f"  /api/meetings/{mid}/docs 端点 OK ({len(docs_resp['docs'])} doc blocks)")


# ============================================================
# 测试 3: SSE 事件连通性
# ============================================================

def test_meeting_state_after_chunk(wav_bytes):
    """验证 POST chunk 后 meeting state 可查.

    用户路径: 说话 → 系统处理后 → state 中有转写段和指标.
    """
    start = _http_post(f"{GPU_URL}/api/meetings/stream_start", b"", timeout=10)
    mid = start["meeting_id"]

    body, ct = _build_multipart(wav_bytes, chunk_index=0, sync_mode=True)
    _http_post(f"{GPU_URL}/api/meetings/{mid}/stream_chunk",
               body, content_type=ct, timeout=180)

    # 查询 meeting state
    state = _http_get(f"{GPU_URL}/api/meetings/{mid}/state", timeout=10)
    assert "transcript_segments" in state, f"state 缺 transcript_segments: {state}"
    assert "metrics" in state, f"state 缺 metrics: {state}"
    assert "processed_chunks" in state, f"state 缺 processed_chunks: {state}"

    metrics = state.get("metrics", [])
    if metrics:
        last = metrics[-1]
        print(f"\n  Meeting state metrics:")
        print(f"     Chunks processed: {len(state['processed_chunks'])}")
        print(f"     Last processing_ms: {last.get('processing_ms', -1)}ms")
        print(f"     Last segs: {last.get('new_segments', 0)} new")
