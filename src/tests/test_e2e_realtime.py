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
        """SSE 端点可访问并返回 connected 事件"""
        # 先创建一个会议
        resp = self._post(f"{server}/api/meetings/stream_start", {})
        assert "meeting_id" in resp
        meeting_id = resp["meeting_id"]

        # 连接 SSE
        url = f"{server}/api/meetings/{meeting_id}/events"
        req = urllib.request.Request(url)
        response = urllib.request.urlopen(req, timeout=5)
        assert response.status == 200
        assert "event-stream" in response.headers.get("Content-Type", "")

        # 读 connected 事件
        data = response.read(1024)
        assert b"event: connected" in data
        response.close()

    def test_push_and_receive_event(self, server):
        """push_event → SSE 客户端能收到"""
        from vpbuddy.realtime_server import push_event, _event_queues

        meeting_id = "TEST_PUSH_001"

        # 清除旧队列
        if meeting_id in _event_queues:
            del _event_queues[meeting_id]

        # 收集 SSE 事件
        received = []

        def collect():
            url = f"{server}/api/meetings/{meeting_id}/events"
            req = urllib.request.Request(url)
            try:
                resp = urllib.request.urlopen(req, timeout=10)
                buf = b""
                while len(received) < 4:
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

        # 推送 3 个事件
        push_event(meeting_id, "transcript-segment", {"text": "hello", "speaker_id": "S0"})
        push_event(meeting_id, "state-update", {"requirements": 1})
        push_event(meeting_id, "doc-update", {"status": "ok"})

        t.join(timeout=5)

        # 验证
        assert len(received) >= 4, f"只收到 {len(received)}: {received}"
        assert any("transcript-segment" in e for e in received)
        assert any("state-update" in e for e in received)
        assert any("doc-update" in e for e in received)
        assert any("hello" in e for e in received)

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
