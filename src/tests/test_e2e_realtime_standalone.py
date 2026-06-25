#!/usr/bin/env python3
"""端到端实时链路测试 — 独立脚本, 不依赖 pytest/conftest

运行:
    PYTHONPATH=src python src/tests/test_e2e_realtime_standalone.py
    或:
    VP_E2E_BASE_URL=http://192.168.10.63:8765 PYTHONPATH=src python src/tests/test_e2e_realtime_standalone.py
        # 用 GPU 端已部署的 ui_server, 跳过本地起 server (KB embedding 预热慢)

默认连本地起的测试服务器 (127.0.0.1:18765), 但可设 VP_E2E_BASE_URL 指向 GPU 端真实 server。
"""
from __future__ import annotations
import json
import os
import socket
import sys
import threading
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from vpbuddy import ui_server
from vpbuddy import realtime_server

TEST_HOST = "127.0.0.1"
TEST_PORT = 18765
TEST_DATA_DIR = Path("/tmp/vpbuddy_test_data")
TEST_DOCS_DIR = Path("/tmp/vpbuddy_test_docs")

# 2026-06-25: 如果 GPU 端 8765 已部署新 cherry-pick 代码, 优先用它 (避免本地 KB 预热慢)
EXTERNAL_BASE_URL = os.environ.get("VP_E2E_BASE_URL", "").rstrip("/")
# SSE 实际连接用 host/port — 如果走 EXTERNAL, 从 URL 解析
SSE_HOST = "127.0.0.1"
SSE_PORT = 18765


def setup_server() -> str:
    """启动测试服务器 — 优先用外部 server (VP_E2E_BASE_URL), 否则本地起"""
    global SSE_HOST, SSE_PORT
    if EXTERNAL_BASE_URL:
        print(f"使用外部 server: {EXTERNAL_BASE_URL}")
        # 解析 host/port 给 read_sse_events 用
        from urllib.parse import urlparse
        u = urlparse(EXTERNAL_BASE_URL)
        SSE_HOST = u.hostname or "127.0.0.1"
        SSE_PORT = u.port or 80
        print(f"SSE 走 {SSE_HOST}:{SSE_PORT}")
        return EXTERNAL_BASE_URL

    print("本地起 server (KB embedding 预热可能要 30s+)...")
    TEST_DATA_DIR.mkdir(parents=True, exist_ok=True)
    TEST_DOCS_DIR.mkdir(parents=True, exist_ok=True)
    ui_server.DATA_DIR = TEST_DATA_DIR
    ui_server.DOCS_DIR = TEST_DOCS_DIR

    t = threading.Thread(
        target=ui_server.main,
        args=(["--host", TEST_HOST, "--port", str(TEST_PORT)],),
        daemon=True,
    )
    t.start()
    # 等 server 真起来 — KB 预热 + bind 端口都可能慢
    deadline = time.time() + 60
    while time.time() < deadline:
        try:
            with socket.create_connection((TEST_HOST, TEST_PORT), timeout=1):
                return f"http://{TEST_HOST}:{TEST_PORT}"
        except OSError:
            time.sleep(1)
    raise RuntimeError(f"本地 server {TEST_PORT} 端口 60s 内未起来")


def post(url, data):
    req = urllib.request.Request(
        url, data=json.dumps(data).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode())


def get(url):
    with urllib.request.urlopen(url, timeout=10) as r:
        return json.loads(r.read().decode())


def upload_audio(url, wav_data, fields=None):
    fields = fields or {}
    boundary = "----TestBoundary"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="audio"; filename="c.wav"\r\n'
        f"Content-Type: audio/wav\r\n\r\n"
    ).encode() + wav_data + b"\r\n"
    for key, value in fields.items():
        body += (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{key}"\r\n\r\n'
            f"{value}\r\n"
        ).encode()
    body += f"--{boundary}--\r\n".encode()

    req = urllib.request.Request(url, data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def make_silent_wav(duration_sec, sample_rate):
    n = int(sample_rate * duration_sec)
    ds = n * 2
    h = bytearray(44)
    h[0:4] = b"RIFF"
    h[4:8] = (36 + ds).to_bytes(4, "little")
    h[8:12] = b"WAVE"
    h[12:16] = b"fmt "
    h[16:20] = (16).to_bytes(4, "little")
    h[20:22] = (1).to_bytes(2, "little")
    h[22:24] = (1).to_bytes(2, "little")
    h[24:28] = sample_rate.to_bytes(4, "little")
    h[28:32] = (sample_rate * 2).to_bytes(4, "little")
    h[32:34] = (2).to_bytes(2, "little")
    h[34:36] = (16).to_bytes(2, "little")
    h[36:40] = b"data"
    h[40:44] = ds.to_bytes(4, "little")
    return bytes(h) + b"\x00" * ds


def read_sse_events(host, port, path, max_events=10, timeout_sec=10):
    """用原始 socket 读取 SSE 事件, 返回 (event_type, data) 列表"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout_sec)
    sock.connect((host, port))

    request = f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nAccept: text/event-stream\r\n\r\n"
    sock.sendall(request.encode())

    # 读响应头
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(1024)
        if not chunk:
            break
        buf += chunk

    # 分离 body
    parts = buf.split(b"\r\n\r\n", 1)
    body = parts[1] if len(parts) > 1 else b""

    # 继续读 SSE 事件
    events = []
    sock.settimeout(2)
    start = time.time()
    while len(events) < max_events and time.time() - start < timeout_sec:
        try:
            # 先解析响应头后已经读到的 body，再继续 recv
            while b"\n\n" in body:
                pos = body.index(b"\n\n")
                event_text = body[:pos].decode("utf-8")
                body = body[pos + 2:]

                event_type = "message"
                event_data = ""
                for line in event_text.split("\n"):
                    if line.startswith("event: "):
                        event_type = line[7:].strip()
                    elif line.startswith("data: "):
                        event_data = line[6:].strip()

                if event_data:
                    try:
                        data = json.loads(event_data)
                    except json.JSONDecodeError:
                        data = event_data
                    events.append((event_type, data))
            if len(events) >= max_events:
                break
            chunk = sock.recv(512)
            if not chunk:
                break
            body += chunk
            # 按 \n\n 分割事件
            while b"\n\n" in body:
                pos = body.index(b"\n\n")
                event_text = body[:pos].decode("utf-8")
                body = body[pos + 2:]

                event_type = "message"
                event_data = ""
                for line in event_text.split("\n"):
                    if line.startswith("event: "):
                        event_type = line[7:].strip()
                    elif line.startswith("data: "):
                        event_data = line[6:].strip()

                if event_data:
                    try:
                        data = json.loads(event_data)
                    except json.JSONDecodeError:
                        data = event_data
                    events.append((event_type, data))
        except socket.timeout:
            break
        except Exception:
            break

    sock.close()
    return events


def test_sse_endpoint_exists(server):
    """Test 1: SSE 端点可访问"""
    print("\n[Test 1] SSE 端点可访问...")
    resp = post(f"{server}/api/meetings/stream_start", {})
    assert "meeting_id" in resp, f"创建会议失败: {resp}"
    meeting_id = resp["meeting_id"]

    events = read_sse_events(SSE_HOST, SSE_PORT, f"/api/meetings/{meeting_id}/events", max_events=2, timeout_sec=5)
    assert len(events) > 0, f"没收到事件: {events}"
    assert events[0][0] == "connected", f"第一个事件不是 connected: {events[0]}"
    print(f"  PASS: 收到 {len(events)} 个事件, 第一个是 connected")


def test_push_and_receive_event(server):
    """Test 2: push_event → SSE 客户端能收到"""
    print("\n[Test 2] push_event → SSE 客户端能收到...")
    meeting_id = "TEST_PUSH_001"

    # 后台收集 SSE
    collected = []

    def collect():
        events = read_sse_events(SSE_HOST, SSE_PORT, f"/api/meetings/{meeting_id}/events", max_events=5, timeout_sec=8)
        collected.extend(events)

    t = threading.Thread(target=collect, daemon=True)
    t.start()
    time.sleep(0.5)

    realtime_server.push_event(meeting_id, "transcript-segment", {"text": "hello", "speaker_id": "S0"})
    realtime_server.push_event(meeting_id, "state-update", {"requirements": 1})
    realtime_server.push_event(meeting_id, "doc-update", {"status": "ok"})

    t.join(timeout=8)

    event_types = [e[0] for e in collected]
    assert "transcript-segment" in event_types, f"没收到 transcript-segment: {event_types}"
    assert "state-update" in event_types, f"没收到 state-update: {event_types}"
    assert "doc-update" in event_types, f"没收到 doc-update: {event_types}"
    print(f"  PASS: 收到 {len(collected)} 个事件, 类型: {event_types}")


def test_stream_chunk_with_sse(server):
    """Test 3: SSE 连接 + 模拟 chunk 处理后的推送"""
    print("\n[Test 3] SSE 连接 + 模拟 chunk 处理后的推送...")
    resp = post(f"{server}/api/meetings/stream_start", {})
    meeting_id = resp["meeting_id"]

    collected = []

    def collect():
        events = read_sse_events(SSE_HOST, SSE_PORT, f"/api/meetings/{meeting_id}/events", max_events=10, timeout_sec=10)
        collected.extend(events)

    t = threading.Thread(target=collect, daemon=True)
    t.start()
    time.sleep(0.5)

    # 模拟服务端处理完 chunk 后推送事件(不依赖 funasr)
    realtime_server.push_event(meeting_id, "transcript-segment", {
        "text": "这是一个测试转写",
        "speaker_id": "SPEAKER_00",
        "speaker_name": "VP",
        "start_sec": 0.0,
        "end_sec": 3.5,
    })
    realtime_server.push_event(meeting_id, "state-update", {
        "requirements": 1,
        "goals": 0,
        "features": 0,
        "risks": 0,
        "questions": 1,
    })
    realtime_server.push_event(meeting_id, "doc-update", {
        "status": "triggered",
        "kinds": ["req", "arch", "tasks", "api", "risk", "demo"],
        "message": "6 docs generation triggered",
    })

    t.join(timeout=10)

    event_types = [e[0] for e in collected]
    assert "connected" in event_types, f"没收到 connected: {event_types}"
    assert "transcript-segment" in event_types, f"没收到 transcript-segment: {event_types}"
    assert "state-update" in event_types, f"没收到 state-update: {event_types}"
    assert "doc-update" in event_types, f"没收到 doc-update: {event_types}"
    print(f"  PASS: 收到 {len(collected)} 个事件, 类型: {event_types}")


def test_multiple_clients_same_meeting(server):
    """Test 4: 多个客户端订阅同一个 meeting 的 SSE"""
    print("\n[Test 4] 多个客户端订阅同一个 meeting...")
    resp = post(f"{server}/api/meetings/stream_start", {})
    meeting_id = resp["meeting_id"]

    collected1 = []
    collected2 = []

    def collect1():
        events = read_sse_events(SSE_HOST, SSE_PORT, f"/api/meetings/{meeting_id}/events", max_events=5, timeout_sec=8)
        collected1.extend(events)

    def collect2():
        events = read_sse_events(SSE_HOST, SSE_PORT, f"/api/meetings/{meeting_id}/events", max_events=5, timeout_sec=8)
        collected2.extend(events)

    t1 = threading.Thread(target=collect1, daemon=True)
    t2 = threading.Thread(target=collect2, daemon=True)
    t1.start()
    t2.start()
    time.sleep(0.5)

    realtime_server.push_event(meeting_id, "transcript-segment", {"text": "多客户端测试", "speaker_id": "S0"})
    realtime_server.push_event(meeting_id, "state-update", {"requirements": 2})

    t1.join(timeout=8)
    t2.join(timeout=8)

    types1 = [e[0] for e in collected1]
    types2 = [e[0] for e in collected2]

    assert "transcript-segment" in types1, f"客户端1 没收到 transcript: {types1}"
    assert "transcript-segment" in types2, f"客户端2 没收到 transcript: {types2}"
    assert "state-update" in types1, f"客户端1 没收到 state-update: {types1}"
    assert "state-update" in types2, f"客户端2 没收到 state-update: {types2}"
    print(f"  PASS: 客户端1 收到 {len(collected1)} 个, 客户端2 收到 {len(collected2)} 个")


def test_different_meetings_isolated(server):
    """Test 5: 不同会议的 SSE 事件隔离"""
    print("\n[Test 5] 不同会议的 SSE 事件隔离...")
    resp1 = post(f"{server}/api/meetings/stream_start", {})
    mid1 = resp1["meeting_id"]
    resp2 = post(f"{server}/api/meetings/stream_start", {})
    mid2 = resp2["meeting_id"]

    collected1 = []
    collected2 = []

    def collect1():
        events = read_sse_events(SSE_HOST, SSE_PORT, f"/api/meetings/{mid1}/events", max_events=5, timeout_sec=6)
        collected1.extend(events)

    def collect2():
        events = read_sse_events(SSE_HOST, SSE_PORT, f"/api/meetings/{mid2}/events", max_events=5, timeout_sec=6)
        collected2.extend(events)

    t1 = threading.Thread(target=collect1, daemon=True)
    t2 = threading.Thread(target=collect2, daemon=True)
    t1.start()
    t2.start()
    time.sleep(0.5)

    # 只给 mid1 推事件
    realtime_server.push_event(mid1, "transcript-segment", {"text": "仅会议1的消息", "speaker_id": "S0"})

    t1.join(timeout=6)
    t2.join(timeout=6)

    types1 = [e[0] for e in collected1]
    types2 = [e[0] for e in collected2]

    assert "transcript-segment" in types1, f"mid1 没收到: {types1}"
    assert "transcript-segment" not in types2, f"mid2 不该收到 mid1 的事件: {types2}"
    print(f"  PASS: 会议隔离正常 (mid1={len(collected1)} 事件, mid2={len(collected2)} 事件)")


def test_heartbeat_event(server):
    """Test 6: SSE 心跳事件 (长时间无数据时保活)"""
    print("\n[Test 6] SSE 心跳事件...")
    resp = post(f"{server}/api/meetings/stream_start", {})
    meeting_id = resp["meeting_id"]

    collected = []

    def collect():
        # 等 5 秒, 看是否收到心跳
        events = read_sse_events(SSE_HOST, SSE_PORT, f"/api/meetings/{meeting_id}/events", max_events=10, timeout_sec=6)
        collected.extend(events)

    t = threading.Thread(target=collect, daemon=True)
    t.start()
    t.join(timeout=7)

    event_types = [e[0] for e in collected]
    # 至少应该有 connected 事件
    assert "connected" in event_types, f"没收到 connected: {event_types}"
    # heartbeat 事件可能有也可能没有(取决于 timeout 设置), 不强制断言
    print(f"  PASS: 连接正常, 收到 {len(collected)} 个事件, 类型: {event_types}")


def test_event_history_replay(server):
    """Test 7: SSE 历史事件补偿"""
    print("\n[Test 7] SSE 历史事件补偿...")
    meeting_id = "TEST_HISTORY_001"
    realtime_server.push_event(meeting_id, "transcript-segment", {"text": "历史消息1", "speaker_id": "S0"})
    realtime_server.push_event(meeting_id, "state-update", {"requirements": 1})

    events = read_sse_events(SSE_HOST, SSE_PORT, f"/api/meetings/{meeting_id}/events", max_events=5, timeout_sec=5)
    types = [e[0] for e in events]
    assert "transcript-segment" in types, f"没收到历史 transcript: {types}"
    assert "state-update" in types, f"没收到历史 state-update: {types}"
    print(f"  PASS: 历史补偿事件正常, 类型: {types}")


def test_state_and_docs_api(server):
    """Test 8: 会议状态 API + 6 文档 API"""
    print("\n[Test 8] 会议状态 API + 6 文档 API...")
    resp = post(f"{server}/api/meetings/stream_start", {})
    meeting_id = resp["meeting_id"]

    state = get(f"{server}/api/meetings/{meeting_id}/state")
    assert "state" in state, f"状态 API 缺少 state: {state}"
    assert "transcript_segments" in state, f"状态 API 缺少 transcript_segments: {state}"

    docs_dir = ui_server.DOCS_DIR / meeting_id
    docs_dir.mkdir(parents=True, exist_ok=True)
    (docs_dir / "req.md").write_text("# 测试需求文档\n", encoding="utf-8")
    docs = get(f"{server}/api/meetings/{meeting_id}/docs")
    assert len(docs["docs"]) == 6, f"文档数量不对: {docs}"
    req_doc = [d for d in docs["docs"] if d["kind"] == "req"][0]
    assert req_doc["status"] == "stored", f"req 文档状态不对: {req_doc}"
    assert "测试需求文档" in req_doc["content"], f"req 文档内容不对: {req_doc}"
    print("  PASS: 状态和文档 API 正常")


def test_stream_chunk_metadata_and_dedupe(server):
    """Test 9: stream_chunk 元数据、绝对时间和重复 chunk 去重"""
    print("\n[Test 9] stream_chunk 元数据、绝对时间和重复 chunk 去重...")
    import vpbuddy.sub_session_controller as sub_session_controller
    import types

    fake_gpu = types.ModuleType("vpbuddy.scripts.gpu_transcribe")
    fake_gpu.process = lambda _path: {
        "segments": [{
            "start_sec": 0.5,
            "end_sec": 1.5,
            "text": "必须支持实时文档展示",
            "speaker_id": "SPEAKER_00",
        }],
        "num_speakers": 1,
    }
    sys.modules["vpbuddy.scripts.gpu_transcribe"] = fake_gpu
    sub_session_controller.trigger_sub_session = lambda mid, kind, dry_run=False: {"triggered": True}

    resp = post(f"{server}/api/meetings/stream_start", {})
    meeting_id = resp["meeting_id"]
    wav = make_silent_wav(1, 16000)
    fields = {"chunk_index": "7", "chunk_start_sec": "28.0", "overlap_sec": "2.0", "client_sent_at": str(time.time())}

    result = upload_audio(f"{server}/api/meetings/{meeting_id}/stream_chunk", wav, fields)
    assert result["chunk_index"] == 7, f"chunk_index 不对: {result}"
    assert len(result["new_segments"]) == 1, f"首次上传应有 1 个新片段: {result}"
    assert result["new_segments"][0]["start_sec"] == 28.5, f"绝对时间不对: {result}"
    assert result["metrics"]["new_segments"] == 1, f"metrics 不对: {result}"

    duplicate = upload_audio(f"{server}/api/meetings/{meeting_id}/stream_chunk", wav, fields)
    assert duplicate["duplicate_chunk"] is True, f"重复 chunk 未识别: {duplicate}"
    assert duplicate["new_segments"] == [], f"重复 chunk 不应返回新片段: {duplicate}"
    print("  PASS: stream_chunk 元数据、绝对时间和重复 chunk 去重正常")


def main():
    print("=" * 60)
    print("VPBuddy 端到端实时链路测试")
    print("=" * 60)

    server = setup_server()
    print(f"测试服务器: {server}")

    try:
        test_sse_endpoint_exists(server)
        test_push_and_receive_event(server)
        test_stream_chunk_with_sse(server)
        test_multiple_clients_same_meeting(server)
        test_different_meetings_isolated(server)
        test_heartbeat_event(server)
        test_event_history_replay(server)
        test_state_and_docs_api(server)
        test_stream_chunk_metadata_and_dedupe(server)
        print("\n" + "=" * 60)
        print("所有测试通过!")
        print("=" * 60)
        return 0
    except AssertionError as e:
        print(f"\n测试失败: {e}")
        return 1
    except Exception as e:
        print(f"\n测试异常: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
