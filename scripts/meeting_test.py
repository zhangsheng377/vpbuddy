#!/usr/bin/env python3
"""
2026-06-25: TTS 多角色会议音频生成 + 端到端测试
"""
import sys, os, json, time, threading, wave, struct, subprocess, tempfile, urllib.request, io, math, socket
from pathlib import Path
from urllib.parse import urlparse

GPU_URL = os.environ.get("GPU_URL", "http://192.168.10.63:8765")
SAMPLE_RATE = 16000
CHUNK_SEC = 30

MEETING_LINES = [
    ("产品经理", "大家好，今天我们讨论一下智能会议助手的产品规划。我们目标是在下个版本实现会议实时转写功能，目标用户是远程办公的团队。"),
    ("技术负责人", "从架构角度来说，我们需要设计一个完整的客户端服务端架构。客户端负责音频采集，服务端负责语音识别和文档生成。"),
    ("产品经理", "还有一个需求，我们需要支持多语言转写，至少要支持中文、英文和日文。会议纪要要能自动按话题分类。"),
    ("设计师", "用户体验方面，需要支持实时字幕显示，让用户可以随时查看转写内容。这是个核心体验功能。"),
    ("技术负责人", "风险方面有几个需要关注的。语音识别在嘈杂环境下准确率可能下降。服务端并发处理能力也需要提前做负载测试。"),
    ("开发", "任务拆解来看的话，首先实现音频采集模块，然后是服务端ASR集成和文档生成管线。API设计需要先出接口文档，大约需要两周。"),
    ("产品经理", "还有一个关键需求，会议结束后要能自动生成行动项和待办任务，并且关联到项目管理系统。"),
    ("设计师", "有个开放问题需要讨论，如何处理多人同时发言的情况。建议先做轮流发言模式，之后优化多人同时发言。"),
    ("技术负责人", "另一个风险是数据安全性。语音数据涉及客户隐私，所有音频处理必须在内部服务器完成，不能上传第三方云。"),
    ("开发", "API方面我们需要三个主要接口。音频上传接口、转写结果获取接口、和文档生成接口。每个都要有完整错误处理。"),
    ("产品经理", "总结一下本期目标，实现实时转写和自动纪要生成是核心。大家还有什么补充？"),
    ("技术负责人", "架构方面再做决定。为降低延迟采用流式处理，音频切片后边采集边上送，服务端增量返回转写结果。"),
]

VOICES = {
    "产品经理": "zh-CN-XiaoxiaoNeural",
    "技术负责人": "zh-CN-YunxiNeural",
    "开发": "zh-CN-YunjianNeural",
    "设计师": "zh-CN-XiaoyiNeural",
}

def gen_tts_segment(text, speaker, idx):
    """edge-tts 生成 MP3 → ffmpeg 转 WAV 16kHz mono"""
    mp3_path = f"/tmp/meeting_{idx:03d}_{speaker}.mp3"
    wav_path = mp3_path.replace(".mp3", ".wav")

    try:
        subprocess.run(
            ["edge-tts", "--voice", VOICES[speaker], "--text", text,
             "--write-media", mp3_path],
            check=True, timeout=30, capture_output=True
        )
    except subprocess.CalledProcessError:
        # 如果某个语音不可用，Xiaoxiao 备用
        print(f"    ⚠️ {VOICES[speaker]} 不可用，降级到 Xiaoxiao")
        subprocess.run(
            ["edge-tts", "--voice", "zh-CN-XiaoxiaoNeural", "--text", text,
             "--write-media", mp3_path],
            check=True, timeout=30, capture_output=True
        )
    subprocess.run(
        ["ffmpeg", "-y", "-i", mp3_path, "-ar", str(SAMPLE_RATE),
         "-ac", "1", "-sample_fmt", "s16", wav_path],
        check=True, timeout=15, capture_output=True
    )
    os.unlink(mp3_path)
    return wav_path

def load_wav(path):
    with wave.open(path, 'rb') as w:
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert w.getframerate() == SAMPLE_RATE
        frames = w.readframes(w.getnframes())
        return list(struct.unpack(f'<{len(frames)//2}h', frames))

def gen_meeting():
    print("=" * 60)
    print("生成多角色会议音频 (4 种不同 TTS 音色)")
    print("=" * 60)

    all_pcm = []
    for idx, (speaker, text) in enumerate(MEETING_LINES):
        print(f"  [{idx:02d}] {speaker}: {text[:45]}...")
        wav = gen_tts_segment(text, speaker, idx)
        pcm = load_wav(wav)
        all_pcm.extend(pcm)
        all_pcm.extend([0] * int(SAMPLE_RATE * 0.8))  # 0.8s gap

    dur = len(all_pcm) / SAMPLE_RATE
    print(f"\n  ✅ 总 {len(all_pcm)} samples ({dur:.1f}s, {len(MEETING_LINES)} 条发言)")
    return all_pcm

def encode_wav(pcm):
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(struct.pack(f'<{len(pcm)}h', *pcm))
    return buf.getvalue()

def run_test(gpu_url, pcm):
    chunk_n = SAMPLE_RATE * CHUNK_SEC
    chunks = [pcm[i:i+chunk_n] for i in range(0, len(pcm), chunk_n)][:6]
    print(f"\n  共 {len(chunks)} 个 chunk (30s 每块)")

    # 创建会议
    req = urllib.request.Request(
        f"{gpu_url}/api/meetings/stream_start",
        data=json.dumps({"platform": "tts_test"}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        meeting_id = json.loads(r.read())["meeting_id"]
    print(f"  ✅ meeting_id = {meeting_id}")

    # SSE 收集器
    sse_events = []
    host = urlparse(gpu_url).hostname or "192.168.10.63"
    port = urlparse(gpu_url).port or 8765

    def sse_collect():
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(10)
            sock.connect((host, port))
            req_t = f"GET /api/meetings/{meeting_id}/events HTTP/1.1\r\nHost: {host}:{port}\r\nAccept: text/event-stream\r\n\r\n"
            sock.sendall(req_t.encode())
            buf = b""
            deadline = time.time() + 180
            while time.time() < deadline and len(sse_events) < 100:
                try:
                    c = sock.recv(1024)
                    if not c: break
                    buf += c
                    while b"\n\n" in buf:
                        pos = buf.index(b"\n\n")
                        ev = buf[:pos].decode()
                        buf = buf[pos+2:]
                        et, ed = "message", ""
                        for ln in ev.split("\n"):
                            if ln.startswith("event: "): et = ln[7:].strip()
                            elif ln.startswith("data: "): ed = ln[6:].strip()
                        if ed: sse_events.append((et, ed))
                except socket.timeout: continue
                except: break
            sock.close()
        except Exception as e:
            print(f"  ⚠️ SSE 收集器错误: {e}")

    st = threading.Thread(target=sse_collect, daemon=True)
    st.start()
    time.sleep(1)

    # 上传 chunk
    for ci, ch in enumerate(chunks):
        wav = encode_wav(ch)
        bdry = "----TT"
        body = (
            f"--{bdry}\r\nContent-Disposition: form-data; name=\"audio\"; filename=\"c.wav\"\r\n"
            f"Content-Type: audio/wav\r\n\r\n"
        ).encode() + wav + b"\r\n"
        for k, v in (("chunk_index", str(ci)), ("chunk_start_sec", f"{ci*CHUNK_SEC:.3f}"),
                     ("overlap_sec", "0.0"), ("client_sent_at", str(time.time()))):
            body += f"--{bdry}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
        body += f"--{bdry}--\r\n".encode()

        t0 = time.time()
        req = urllib.request.Request(
            f"{gpu_url}/api/meetings/{meeting_id}/stream_chunk?sync=false",
            data=body, headers={"Content-Type": f"multipart/form-data; boundary={bdry}"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                resp = json.loads(r.read())
            ms = (time.time() - t0) * 1000
            if resp.get("status") == "accepted":
                print(f"  ✅ chunk#{ci} accepted ({ms:.0f}ms)")
            else:
                print(f"  ⚠️  chunk#{ci}: {json.dumps(resp, ensure_ascii=False)[:100]}")
        except Exception as e:
            print(f"  ❌ chunk#{ci}: {e}")
        time.sleep(1)

    # 等 SSE + 后台处理
    print("\n  等待后台处理 (30s)...")
    st.join(timeout=30)

    # SSE 统计
    ev_counts = {}
    for et, _ in sse_events:
        ev_counts[et] = ev_counts.get(et, 0) + 1
    print(f"\n  SSE 共 {len(sse_events)} 事件: {json.dumps(ev_counts, ensure_ascii=False)}")

    # 检查 docs
    time.sleep(5)  # 等 6 docs 写完
    print(f"\n  === 6 文档检查 ===")
    try:
        with urllib.request.urlopen(f"{gpu_url}/api/meetings/{meeting_id}/docs", timeout=15) as r:
            dd = json.loads(r.read())
        for d in dd.get("docs", []):
            k, s = d.get("kind"), d.get("status")
            c = d.get("content", "")[:150]
            print(f"  [{k}] status={s}: {c[:100]}...")
    except Exception as e:
        print(f"  ❌ docs: {e}")

    # 检查 state
    print(f"\n  === 事实数统计 ===")
    try:
        with urllib.request.urlopen(f"{gpu_url}/api/meetings/{meeting_id}/state", timeout=15) as r:
            sd = json.loads(r.read())
        stt = sd.get("state", {})
        for k in ["requirements","goals","features","risks","open_questions"]:
            items = stt.get(k, [])
            print(f"  {k}: {len(items)}")
            for it in items[:2]:
                print(f"    - {it.get('text','')[:80]}")
    except Exception as e:
        print(f"  ❌ state: {e}")

    return meeting_id, sse_events

if __name__ == "__main__":
    pcm = gen_meeting()
    mid, ev = run_test(GPU_URL, pcm)
    print(f"\n  ✅ 测试完成! meeting_id={mid}")
    print(f"  SSE 端点: {GPU_URL}/api/meetings/{mid}/events")
