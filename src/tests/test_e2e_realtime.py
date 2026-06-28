"""端到端实时链路测试 — 客户端-服务端 SSE 通信

测试场景:
1. 服务端 SSE 端点可访问
2. push_event → SSE 客户端能收到
3. stream_chunk 处理完后触发 SSE 推送

注意: 本测试不依赖 GPU/funasr, 用静音音频测试 HTTP + SSE 链路

运行:
    PYTHONPATH=src python -m pytest src/tests/test_e2e_realtime.py -v -s
"""
from __future__ import annotations
import json
import threading
import time
import urllib.request
from pathlib import Path

import pytest


# === 测试配置 ===
TEST_HOST = "127.0.0.1"
TEST_PORT = 18765


@pytest.fixture(scope="module")
def server():
    """启动测试用的 ui_server(不加载 GPU 模型)"""
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))

    # 绕过 conftest 里的 torch 导入
    # 直接 import ui_server 模块
    from vpbuddy import ui_server
    from vpbuddy import realtime_server

    # 修改配置用测试端口
    ui_server.DATA_DIR = Path("/tmp/vpbuddy_test_data")
    ui_server.DATA_DIR.mkdir(parents=True, exist_ok=True)

    # 在后台线程启动服务器
    server_thread = threading.Thread(
        target=ui_server.main,
        args=(["--host", TEST_HOST, "--port", str(TEST_PORT)],),
        daemon=True,
    )
    server_thread.start()
    time.sleep(1)

    yield f"http://{TEST_HOST}:{TEST_PORT}"


class TestRealtimeSSE:
    """SSE 实时推送端到端测试"""

    def test_sse_endpoint_exists(self, server):
        """SSE 端点可访问并返回 connected 事件 (用 raw socket 绕 urllib chunked 解析坑)"""
        # 先创建一个会议
        resp = self._post(f"{server}/api/meetings/stream_start", {})
        assert "meeting_id" in resp
        meeting_id = resp["meeting_id"]

        # 连接 SSE — raw socket (跟 v0.1.1-rc5 SSE 30s timeout 修法一致)
        import socket
        from urllib.parse import urlparse
        u = urlparse(f"{server}/api/meetings/{meeting_id}/events")
        sock = socket.create_connection((u.hostname, u.port or 80), timeout=10)
        try:
            sock.sendall(
                f"GET {u.path} HTTP/1.1\r\n"
                f"Host: {u.hostname}:{u.port or 80}\r\n"
                f"Accept: text/event-stream\r\n"
                f"Connection: keep-alive\r\n\r\n".encode()
            )
            # 读 HTTP header + 第一个 SSE chunk (含 connected 事件)
            buf = b""
            deadline = time.time() + 10
            while b"event: connected" not in buf and time.time() < deadline:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                buf += chunk
            # 验证 HTTP header
            assert b"200 OK" in buf, f"非 200 响应: {buf[:200]}"
            assert b"text/event-stream" in buf, f"非 SSE content-type: {buf[:200]}"
            # 验证 connected 事件
            assert b"event: connected" in buf, f"connected 事件缺失: {buf[:500]}"
        finally:
            sock.close()

    def test_push_and_receive_event(self, server):
        """push_event → SSE 客户端能收到 (简化: 验证 server 端推送成功即可, 完整 SSE 流测试在 test_sse_e2e)"""
        from vpbuddy.realtime_server import push_event, close_meeting, get_subscriber_count, _event_history

        meeting_id = "TEST_PUSH_001"

        # 清除旧订阅 + 历史 (新实现用 _subscribers, 旧 _event_queues 已废)
        close_meeting(meeting_id)

        # 收集 SSE 事件 — 用 raw socket 更可靠
        import socket
        received = []
        sock = None
        try:
            from urllib.parse import urlparse
            u = urlparse(f"{server}/api/meetings/{meeting_id}/events")
            sock = socket.create_connection((u.hostname, u.port or 80), timeout=5)
            sock.sendall(
                f"GET {u.path} HTTP/1.1\r\n"
                f"Host: {u.hostname}:{u.port or 80}\r\n"
                f"Accept: text/event-stream\r\n"
                f"Connection: keep-alive\r\n\r\n".encode()
            )
            # 读 HTTP header
            buf = b""
            while b"\r\n\r\n" not in buf:
                chunk = sock.recv(1024)
                if not chunk:
                    break
                buf += chunk
            # 现在读 SSE body
            body = buf.split(b"\r\n\r\n", 1)[1]
            sock.settimeout(5)

            import select
            # 等事件推到
            for _ in range(3):
                # 推 3 个事件
                push_event(meeting_id, "transcript-segment", {"text": "hello", "speaker_id": "S0"})
                push_event(meeting_id, "state-update", {"requirements": 1})
                push_event(meeting_id, "doc-update", {"status": "ok"})
                time.sleep(0.5)
                # 读所有可用数据
                while True:
                    ready = select.select([sock], [], [], 0.2)
                    if not ready[0]:
                        break
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    body += chunk
                # 检查累计
                events = body.decode("utf-8", errors="replace").split("\n\n")
                received = [e for e in events if e.strip()]
                if len(received) >= 4:
                    break
        finally:
            if sock:
                sock.close()

        # 验证 — 至少收到 connected + 3 push 事件
        assert len(received) >= 4, f"只收到 {len(received)}: {received}"
        all_text = "\n".join(received)
        assert "connected" in all_text, f"connected 事件缺失: {received}"
        assert "transcript-segment" in all_text
        assert "state-update" in all_text
        assert "doc-update" in all_text
        assert "hello" in all_text

    def test_stream_chunk_with_sse(self, server):
        """上传静音 chunk → 服务端返回 HTTP 结果 + SSE 推送"""
        # 1. 创建会议
        resp = self._post(f"{server}/api/meetings/stream_start", {})
        meeting_id = resp["meeting_id"]

        # 2. 后台 SSE 收集
        received = []

        def collect():
            url = f"{server}/api/meetings/{meeting_id}/events"
            req = urllib.request.Request(url)
            try:
                resp = urllib.request.urlopen(req, timeout=15)
                buf = b""
                start = time.time()
                while time.time() - start < 8:
                    chunk = resp.read(512)
                    if not chunk:
                        break
                    buf += chunk
                    while b"\n\n" in buf:
                        pos = buf.index(b"\n\n")
                        received.append(buf[:pos].decode("utf-8"))
                        buf = buf[pos + 2:]
                resp.close()
            except Exception as e:
                received.append(f"ERROR:{e}")

        t = threading.Thread(target=collect, daemon=True)
        t.start()
        time.sleep(0.3)

        # 3. 上传静音 WAV
        wav = self._make_silent_wav(1.0, 16000)
        result = self._upload_audio(f"{server}/api/meetings/{meeting_id}/stream_chunk", wav)

        # 4. HTTP 返回正常
        assert "meeting_id" in result
        assert result["meeting_id"] == meeting_id

        # 5. 等 SSE
        t.join(timeout=8)

        # 6. 验证 SSE 收到事件
        assert len(received) > 0, f"没收到 SSE: {received}"
        event_types = []
        for e in received:
            if "event:" in e:
                et = e.split("event:")[1].split("\n")[0].strip()
                event_types.append(et)

        assert "connected" in event_types
        # 静音可能没 segment, 但至少 connected 要有
        print(f"事件类型: {event_types}")
        print(f"事件数: {len(received)}")

    # === 辅助 ===

    def _post(self, url, data):
        req = urllib.request.Request(
            url, data=json.dumps(data).encode(),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode())

    def _upload_audio(self, url, wav_data):
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

    def _make_silent_wav(self, duration_sec, sample_rate):
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
