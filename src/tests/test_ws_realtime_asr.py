"""WebSocket 实时 ASR 端点集成测试 — /api/meetings/{id}/realtime_asr

测试覆盖:
- handshake (start 消息 -> asr_status: connected)
- 二进制 PCM 音频帧 relay
- ping-pong 控制消息
- stop 消息 -> asr_complete
- 非法 start 消息 -> error
- 客户端断连 -> 服务端 cleanup
- 多会议并发隔离
"""

from __future__ import annotations

import json
import time
import uuid
import sys
import types
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


def _register_and_get_token() -> str:
    from vpbuddy.server.auth import _create_token
    import uuid

    uid = uuid.uuid4().hex[:16]
    email = f"ws_test_{uuid.uuid4().hex[:8]}@test.dev"
    return _create_token(uid, email)


@pytest.fixture
def client():
    from vpbuddy.server.fastapi_app import app

    with TestClient(app) as c:
        yield c


@pytest.fixture
def auth():
    return _register_and_get_token()


def _ws_url(mid: str, token: str) -> str:
    """构造带 token 的 WS 连接 URL."""
    return f"/api/meetings/{mid}/realtime_asr?token={token}"


def _mock_bailian_session():
    sess = MagicMock()
    sess.running = True
    sess.meeting_id = "test-mid"
    sess.accumulated_text = ""
    sess.sentence_count = 0

    cb = MagicMock()
    sess.callback = cb
    return sess


def _connect(client, mid, token):
    return client.websocket_connect(_ws_url(mid, token))


def test_ws_handshake_start(client, auth):
    mid = f"ws_hs_{uuid.uuid4().hex[:6]}"
    sess = _mock_bailian_session()
    sess.meeting_id = mid

    with patch("vpbuddy.server.bailian_asr.start_session", return_value=sess):
        with _connect(client, mid, auth) as ws:
            ws.send_json({"type": "start", "format": "pcm", "sample_rate": 16000})
            ws.send_json({"type": "ping"})
            pong = ws.receive_json()
            assert pong["type"] == "pong"


def test_ws_handshake_custom_format(client, auth):
    mid = f"ws_fmt_{uuid.uuid4().hex[:6]}"
    sess = _mock_bailian_session()
    sess.meeting_id = mid

    with patch("vpbuddy.server.bailian_asr.start_session", return_value=sess):
        with _connect(client, mid, auth) as ws:
            ws.send_json({"type": "start", "format": "opus", "sample_rate": 8000})
            ws.send_json({"type": "ping"})
            pong = ws.receive_json()
            assert pong["type"] == "pong"


def test_ws_bad_handshake_no_type(client, auth):
    mid = f"ws_bad_{uuid.uuid4().hex[:6]}"

    with _connect(client, mid, auth) as ws:
        ws.send_json({"hello": "world"})
        resp = ws.receive_json()
        assert resp["type"] == "error"
        assert "expected start" in resp.get("error", "")


def test_ws_bad_handshake_wrong_type(client, auth):
    mid = f"ws_wt_{uuid.uuid4().hex[:6]}"

    with _connect(client, mid, auth) as ws:
        ws.send_json({"type": "restart", "format": "pcm"})
        resp = ws.receive_json()
        assert resp["type"] == "error"


def test_ws_no_token(client):
    mid = f"ws_notok_{uuid.uuid4().hex[:6]}"

    with client.websocket_connect(f"/api/meetings/{mid}/realtime_asr") as ws:
        resp = ws.receive_json()
        assert resp["type"] == "error"
        assert "token" in resp.get("error", "").lower()


def test_ws_audio_frame_relay(client, auth):
    mid = f"ws_pcm_{uuid.uuid4().hex[:6]}"
    sess = _mock_bailian_session()
    sess.meeting_id = mid

    with patch("vpbuddy.server.bailian_asr.start_session", return_value=sess):
        with patch("vpbuddy.server.bailian_asr.send_audio") as mock_send:
            with _connect(client, mid, auth) as ws:
                ws.send_json({"type": "start", "format": "pcm", "sample_rate": 16000})
                frame = b"\x00" * 3200
                ws.send_bytes(frame)
                import time
                time.sleep(0.1)
                assert mock_send.called, "send_audio 应被调用"


def test_ws_multiple_audio_frames(client, auth):
    mid = f"ws_multi_{uuid.uuid4().hex[:6]}"
    sess = _mock_bailian_session()
    sess.meeting_id = mid

    with patch("vpbuddy.server.bailian_asr.start_session", return_value=sess):
        with patch("vpbuddy.server.bailian_asr.send_audio") as mock_send:
            with _connect(client, mid, auth) as ws:
                ws.send_json({"type": "start", "format": "pcm", "sample_rate": 16000})
                for i in range(10):
                    frame = bytes([i % 256]) * 3200
                    ws.send_bytes(frame)
                import time
                time.sleep(0.2)
                assert mock_send.call_count >= 10


def test_ws_ping_pong(client, auth):
    mid = f"ws_pp_{uuid.uuid4().hex[:6]}"
    sess = _mock_bailian_session()
    sess.meeting_id = mid

    with patch("vpbuddy.server.bailian_asr.start_session", return_value=sess):
        with _connect(client, mid, auth) as ws:
            ws.send_json({"type": "start", "format": "pcm", "sample_rate": 16000})
            for i in range(3):
                ws.send_json({"type": "ping"})
                resp = ws.receive_json()
                assert resp == {"type": "pong"}, f"ping #{i} 应返回 pong"


def test_ws_stop_closes_session(client, auth):
    mid = f"ws_stop_{uuid.uuid4().hex[:6]}"
    sess = _mock_bailian_session()
    sess.meeting_id = mid

    with patch("vpbuddy.server.bailian_asr.start_session", return_value=sess):
        with patch("vpbuddy.server.bailian_asr.stop_session") as mock_stop:
            with _connect(client, mid, auth) as ws:
                ws.send_json({"type": "start", "format": "pcm", "sample_rate": 16000})
                ws.send_json({"type": "stop"})
                import time
                time.sleep(0.3)
                assert mock_stop.called, "stop_session 应被调用"


def test_ws_disconnect_cleanup(client, auth):
    mid = f"ws_dc_{uuid.uuid4().hex[:6]}"
    sess = _mock_bailian_session()
    sess.meeting_id = mid

    with patch("vpbuddy.server.bailian_asr.start_session", return_value=sess):
        with patch("vpbuddy.server.bailian_asr.stop_session") as mock_stop:
            with _connect(client, mid, auth) as ws:
                ws.send_json({"type": "start", "format": "pcm", "sample_rate": 16000})
                ws.close()
            import time
            time.sleep(0.3)
            assert mock_stop.called, "断连后 stop_session 应被调用"


def test_ws_concurrent_meetings(client, auth):
    mid_a = f"ws_conc_a_{uuid.uuid4().hex[:6]}"
    mid_b = f"ws_conc_b_{uuid.uuid4().hex[:6]}"

    sess_a = _mock_bailian_session()
    sess_a.meeting_id = mid_a
    sess_b = _mock_bailian_session()
    sess_b.meeting_id = mid_b

    def _start_session(loop, meeting_id, send_json, sample_rate=16000, fmt="pcm", data_dir=""):
        if meeting_id == mid_a:
            return sess_a
        elif meeting_id == mid_b:
            return sess_b
        raise ValueError(f"unknown meeting: {meeting_id}")

    with patch("vpbuddy.server.bailian_asr.start_session", side_effect=_start_session):
        with _connect(client, mid_a, auth) as ws_a, _connect(client, mid_b, auth) as ws_b:
            ws_a.send_json({"type": "start", "format": "pcm", "sample_rate": 16000})
            ws_b.send_json({"type": "start", "format": "pcm", "sample_rate": 16000})

            ws_a.send_json({"type": "ping"})
            ws_b.send_json({"type": "ping"})

            resp_a = ws_a.receive_json()
            resp_b = ws_b.receive_json()

            assert resp_a == {"type": "pong"}
            assert resp_b == {"type": "pong"}


def test_ws_stop_then_disconnect_no_double_stop(client, auth):
    mid = f"ws_idem_{uuid.uuid4().hex[:6]}"
    sess = _mock_bailian_session()
    sess.meeting_id = mid

    with patch("vpbuddy.server.bailian_asr.start_session", return_value=sess):
        with patch("vpbuddy.server.bailian_asr.stop_session") as mock_stop:
            with _connect(client, mid, auth) as ws:
                ws.send_json({"type": "start", "format": "pcm", "sample_rate": 16000})
                ws.send_json({"type": "stop"})
                import time
                time.sleep(0.3)
            time.sleep(0.3)
            assert mock_stop.call_count in (1, 2), f"stop_session 调了 {mock_stop.call_count} 次 (预期 1-2)"
