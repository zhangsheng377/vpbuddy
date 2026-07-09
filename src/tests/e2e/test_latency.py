"""e2e — ASR 延迟 + 文档生成延迟测量

NOTE: 此测试文件基于已废弃的 30s 切片 API (stream_chunk / stream_stop)，
现已迁移至 WebSocket 实时 ASR。待新的 WebSocket 延迟测试实现后重新启用。

跑法: RUN_E2E=1 pytest src/tests/e2e/test_latency.py -v -m e2e
"""
from __future__ import annotations

import pytest

pytest.skip("已废弃: 基于 30s 切片 API (stream_chunk)，待 WebSocket 版本实现", allow_module_level=True)


pytestmark = pytest.mark.e2e

GPU_URL = os.environ.get("VP_E2E_GPU_URL", "http://47.100.182.3:28765")
SR = 16000


# ============================================================
# 辅助函数
# ============================================================

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


def _generate_synthetic_wav(path: Path, dur_sec: float = 8.0) -> None:
    """生成 16kHz mono WAV (纯 Python). 信号=多段 formant 合成模拟语音."""
    ns = int(SR * dur_sec)
    samples = []
    for i in range(ns):
        t = i / SR
        val = (
            0.3 * math.sin(2 * math.pi * 300 * t) * (0.5 + 0.5 * math.sin(2 * math.pi * 4 * t))
            + 0.2 * math.sin(2 * math.pi * 800 * t) * (0.5 + 0.5 * math.sin(2 * math.pi * 3 * t))
            + 0.15 * math.sin(2 * math.pi * 2000 * t) * (0.5 + 0.5 * math.sin(2 * math.pi * 5 * t))
            + 0.1 * math.sin(2 * math.pi * (200 + 100 * math.sin(2 * math.pi * 0.5 * t)) * t)
            + 0.01 * random.gauss(0, 1)
        )
        val = max(-0.95, min(0.95, val))
        samples.append(int(val * 32767))
    _write_wav(path, SR, samples)


def _write_wav(path: Path, sample_rate: int, samples: list[int]) -> None:
    """写入 16bit mono WAV."""
    nch, bits = 1, 16
    bps = bits // 8
    ba = nch * bps
    ds = len(samples) * ba
    with open(path, "wb") as f:
        f.write(b"RIFF")
        f.write(struct.pack("<I", 36 + ds))
        f.write(b"WAVE")
        f.write(b"fmt ")
        f.write(struct.pack("<I", 16))
        f.write(struct.pack("<H", 1))
        f.write(struct.pack("<H", nch))
        f.write(struct.pack("<I", sample_rate))
        f.write(struct.pack("<I", sample_rate * ba))
        f.write(struct.pack("<H", ba))
        f.write(struct.pack("<H", bits))
        f.write(b"data")
        f.write(struct.pack("<I", ds))
        for s in samples:
            f.write(struct.pack("<h", s))


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture(scope="module")
def synth_wav_bytes(tmp_path_factory) -> bytes:
    p = tmp_path_factory.mktemp("wav") / "synth.wav"
    _generate_synthetic_wav(p, dur_sec=8.0)
    with open(p, "rb") as f:
        return f.read()


@pytest.fixture(scope="module")
def real_speech_wav_bytes() -> bytes:
    """尝试生成真实 TTS 语音. 需要 edge-tts + ffmpeg.

    edge-tts 生成微软小晓中文语音 (不需要 API Key).
    如果 edge-tts 不可用, 返回空 bytes 跳过测试.
    """
    try:
        import asyncio

        import edge_tts

        text = (
            "我们需要开发一个任务管理系统。该系统需要支持用户创建、编辑和删除任务。"
            "每个任务应该包含标题、详细描述、优先级标签和截止日期。"
            "管理员可以分配任务给不同成员，并实时查看整体进度。"
            "系统还应该支持文件附件功能，让用户可以上传相关文档和图片。"
            "同时需要提供搜索和筛选功能，让用户按关键字、优先级或状态快速找到任务。"
            "任务状态变更时，系统需要自动发送通知给相关成员。"
            "另外，我们需要一个数据看板，显示各项统计指标。"
            "包括完成任务数、逾期任务数和每个成员的负载情况。"
            "系统需要支持多人协作，多个用户可以同时编辑同一任务。"
            "系统要能处理并发冲突，还要提供版本历史功能。"
            "权限管理也很重要，不同角色有不同的操作权限。"
            "系统性能方面，要求页面加载时间不超过两秒。"
            "支持同时在线用户数不少于一百人。"
        )

        async def _gen():
            comm = edge_tts.Communicate(text, voice="zh-CN-XiaoxiaoNeural")
            await comm.save("/tmp/e2e_real_speech.mp3")
        asyncio.run(_gen())

        # Convert to 16kHz WAV
        import subprocess
        subprocess.run(
            ["ffmpeg", "-y", "-i", "/tmp/e2e_real_speech.mp3",
             "-ar", "16000", "-ac", "1", "/tmp/e2e_real_speech.wav"],
            capture_output=True, timeout=30,
        )
        # Trim to 30s (475152 bytes = 30s * 16000 * 2 + 44 header? 
        # Actually ffmpeg may produce slightly different, let's just read what we have)
        with open("/tmp/e2e_real_speech.wav", "rb") as f:
            return f.read()
    except (ImportError, FileNotFoundError, subprocess.CalledProcessError, Exception) as e:
        print(f"[WARN] edge-tts/ffmpeg 不可用, 跳过真实语音测试: {e}")
        return b""


# ============================================================
# 测试 1: SYNTHETIC ASR Pipeline 延迟
# ============================================================

def test_asr_pipeline_responds(synth_wav_bytes):
    """验证 ASR pipeline 全链路通 (合成语音).

    funasr 30s batch: 即使是 8s 音频, 也会被填充到 30s 处理.
    所以 processing_ms 约 27-30s.
    """
    start = _http_post(f"{GPU_URL}/api/meetings/stream_start", b"", timeout=10)
    mid = start.get("meeting_id", "")
    assert mid, f"stream_start 无 meeting_id"

    body, ct = _build_multipart(synth_wav_bytes, chunk_index=0, sync_mode=True)
    t0 = time.time()
    result = _http_post(f"{GPU_URL}/api/meetings/{mid}/stream_chunk",
                        body, content_type=ct, timeout=180)
    wall_ms = int((time.time() - t0) * 1000)
    m = result.get("metrics", {})

    print(f"\n  合成语音 (8s):")
    print(f"    Server processing_ms: {m.get('processing_ms', -1)}ms")
    print(f"    Wall clock (client): {wall_ms}ms")
    print(f"    Segments: {len(result.get('new_segments', []))}")

    assert m.get("processing_ms", -1) > 0, f"无 processing_ms: {m}"
    assert m["processing_ms"] > 1000, f"ASR 没跑 funasr: {m['processing_ms']}ms"


def test_asr_pipeline_warm(synth_wav_bytes):
    """Warm ASR (model 已加载) 应正常运行."""
    start = _http_post(f"{GPU_URL}/api/meetings/stream_start", b"", timeout=10)
    mid = start["meeting_id"]
    body, ct = _build_multipart(synth_wav_bytes, chunk_index=0, sync_mode=True)
    result = _http_post(f"{GPU_URL}/api/meetings/{mid}/stream_chunk",
                        body, content_type=ct, timeout=180)
    m = result.get("metrics", {})
    print(f"\n  Warm ASR processing_ms: {m.get('processing_ms', -1)}ms")
    assert m.get("processing_ms", -1) > 0, f"Warm ASR failed"


# ============================================================
# 测试 2: 真实中文语音延迟 (核心测量)
# ============================================================

@pytest.mark.skipif("os.environ.get('HAS_REAL_SPEECH') != '1'",
                    reason="需要 edge-tts + ffmpeg: pip install edge-tts")
def test_real_speech_asr_latency():
    """真实中文语音延迟 — 用户实际体验的核心指标.

    生成 30s 中文需求语音 (edge-tts 小晓), POST 到 GPU server,
    测量 ASR processing_ms + 客户端墙钟.

    2026-07-04 实测结果:
      processing_ms: 27,949ms
      wall_clock:    31,556ms
      segments:      9 (全部正确识别)
      转写内容: "我们需要开发一个任务管理系统..." - 准确
    """
    # 生成并修剪到 30s
    try:
        import asyncio
        import edge_tts
    except ImportError:
        pytest.skip("需要 edge-tts")

    text = (
        "我们需要开发一个任务管理系统。该系统需要支持用户创建、编辑和删除任务。"
        "每个任务应该包含标题、详细描述、优先级标签和截止日期。"
        "管理员可以分配任务给不同成员，并实时查看整体进度。"
        "系统还应该支持文件附件功能，让用户可以上传相关文档和图片。"
        "同时需要提供搜索和筛选功能，让用户按关键字、优先级或状态快速找到任务。"
        "任务状态变更时，系统需要自动发送通知给相关成员。"
        "另外，我们需要一个数据看板，显示各项统计指标。"
        "包括完成任务数、逾期任务数和每个成员的负载情况。"
        "系统需要支持多人协作，多个用户可以同时编辑同一任务。"
        "系统要能处理并发冲突，还要提供版本历史功能。"
        "权限管理也很重要，不同角色有不同的操作权限。"
        "系统性能方面，要求页面加载时间不超过两秒。"
        "支持同时在线用户数不少于一百人。"
    )

    async def _gen():
        comm = edge_tts.Communicate(text, voice="zh-CN-XiaoxiaoNeural")
        await comm.save("/tmp/real_asr_test.mp3")
    asyncio.run(_gen())

    import subprocess
    subprocess.run(
        ["ffmpeg", "-y", "-i", "/tmp/real_asr_test.mp3",
         "-ar", "16000", "-ac", "1", "-t", "30", "/tmp/real_asr_test.wav"],
        capture_output=True, timeout=30,
    )
    with open("/tmp/real_asr_test.wav", "rb") as f:
        wav_30s = f.read()

    # stream_start
    start = _http_post(f"{GPU_URL}/api/meetings/stream_start", b"", timeout=10)
    mid = start["meeting_id"]

    # send chunk
    body, ct = _build_multipart(wav_30s, chunk_index=0, sync_mode=True)
    t0 = time.time()
    result = _http_post(f"{GPU_URL}/api/meetings/{mid}/stream_chunk",
                        body, content_type=ct, timeout=180)
    wall_ms = int((time.time() - t0) * 1000)
    m = result.get("metrics", {})
    proc_ms = m.get("processing_ms", -1)
    segs = result.get("new_segments", [])

    print(f"\n  ⏱️  真实中文语音 (30s TTS):")
    print(f"     Server processing_ms: {proc_ms}ms ({proc_ms/1000:.1f}s)")
    print(f"     客户端墙钟:          {wall_ms}ms ({wall_ms/1000:.1f}s)")
    print(f"     网络+排队:            {wall_ms - proc_ms}ms")
    print(f"     Segments: {len(segs)}")
    for s in segs[:5]:
        print(f"       [{s['start_sec']:5.1f}s] {s['text'][:80]}")
    if len(segs) > 5:
        print(f"       ... ({len(segs) - 5} more)")

    # 关键断言: processing_ms 存在且 > 0
    assert proc_ms > 0, f"无 processing_ms: {m}"
    # 30s batch: processing 应该 > 5000ms (模型推理时间)
    assert proc_ms > 5000, f"ASR 太快了, 可能没处理 30s 音频: {proc_ms}ms"
    # 应有至少 1 段转写 (中文语音)
    assert len(segs) >= 1, f"中文语音无转写段: {result}"

    print(f"\n  ✅ 真实中文语音 ASR 延迟测量完成")


# ============================================================
# 测试 3: Docs 端点 + meeting state 验证
# ============================================================

def test_docs_and_state_after_chunk(synth_wav_bytes):
    """验证 chunk 处理后: docs endpoint 可达 + meeting state 有数据.

    注意: GPU 测试服务器无 Hermes LLM, 文档体为空 (0 chars).
    正式场景文档在 ASR 完成后 ~30-60s 由 LLM 生成.
    """
    start = _http_post(f"{GPU_URL}/api/meetings/stream_start", b"", timeout=10)
    mid = start["meeting_id"]

    body, ct = _build_multipart(synth_wav_bytes, chunk_index=0, sync_mode=True)
    result = _http_post(f"{GPU_URL}/api/meetings/{mid}/stream_chunk",
                        body, content_type=ct, timeout=180)

    assert result.get("docs_triggered", False), f"docs_triggered 应为 True"

    # Docs endpoint
    docs_resp = _http_get(f"{GPU_URL}/api/meetings/{mid}/docs", timeout=10)
    assert "docs" in docs_resp
    docs = docs_resp["docs"]
    assert len(docs) == 6, f"期望 6 docs, 实际 {len(docs)}"
    kinds = [d.get("kind") for d in docs]
    assert all(k in kinds for k in ["req", "arch", "tasks", "api", "risk", "demo"]), \
        f"doc kinds: {kinds}"
    print(f"\n  6 doc blocks OK: {kinds}")

    # State endpoint
    state = _http_get(f"{GPU_URL}/api/meetings/{mid}/state", timeout=10)
    assert "transcript_segments" in state
    assert "metrics" in state
    assert "processed_chunks" in state
    metrics = state.get("metrics", [])
    print(f"  Chunks processed: {len(state['processed_chunks'])}")
    if metrics:
        last = metrics[-1]
        print(f"  Last processing_ms: {last.get('processing_ms', -1)}ms")
