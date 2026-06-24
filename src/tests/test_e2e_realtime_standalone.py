#!/usr/bin/env python3
"""端到端实时链路测试 — 独立脚本, 不依赖 pytest/conftest

运行:
    PYTHONPATH=src python src/tests/test_e2e_realtime_standalone.py
"""
from __future__ import annotations
import json
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


def setup_server():
    """启动测试服务器"""
    TEST_DATA_DIR.mkdir(parents=True, exist_ok=True)
    ui_server.DATA_DIR = TEST_DATA_DIR

    t = threading.Thread(
        target=ui_server.main,
        args=(["--host", TEST_HOST, "--port", str(TEST_PORT)],),
        daemon=True,
    )
    t.start()
    time.sleep(2)
    return f"http://{TEST_HOST}:{TEST_PORT}"


def post(url, data):
    req = urllib.request.Request(
        url, data=json.dumps(data).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode())


def upload_audio(url, wav_data):
    boundary = "----TestBoundary"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="audio"; filename="c.wav"\r\n'
        f"Content-Type: audio/wav\r\n\r\n"
    ).encode() + wav_data + f"\r\n--{boundary}--\r\n".encode()

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

    events = read_sse_events(TEST_HOST, TEST_PORT, f"/api/meetings/{meeting_id}/events", max_events=2, timeout_sec=5)
    assert len(events) > 0, f"没收到事件: {events}"
    assert events[0][0] == "connected", f"第一个事件不是 connected: {events[0]}"
    print(f"  PASS: 收到 {len(events)} 个事件, 第一个是 connected")


def test_push_and_receive_event(server):
    """Test 2: push_event → SSE 客户端能收到"""
    print("\n[Test 2] push_event → SSE 客户端能收到...")
    meeting_id = "TEST_PUSH_001"

    if meeting_id in realtime_server._event_queues:
        del realtime_server._event_queues[meeting_id]

    # 后台收集 SSE
    collected = []

    def collect():
        events = read_sse_events(TEST_HOST, TEST_PORT, f"/api/meetings/{meeting_id}/events", max_events=5, timeout_sec=8)
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
        events = read_sse_events(TEST_HOST, TEST_PORT, f"/api/meetings/{meeting_id}/events", max_events=10, timeout_sec=10)
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
