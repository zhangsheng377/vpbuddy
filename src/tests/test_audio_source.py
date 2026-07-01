"""测试 v0.6 Phase 4 (ADR-0021) 服务端: audio_source 字段 + stream_start API 接受 + 持久化."""

from __future__ import annotations
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from vpbuddy.state import MeetingState, Platform, AudioSourceKind


# ── AudioSourceKind enum ──


def test_audio_source_kind_values():
    """三个值: microphone / loopback / both."""
    assert AudioSourceKind.MICROPHONE.value == "microphone"
    assert AudioSourceKind.LOOPBACK.value == "loopback"
    assert AudioSourceKind.BOTH.value == "both"


def test_audio_source_kind_from_string():
    """字符串可转 enum (来自 query / form)."""
    assert AudioSourceKind("microphone") is AudioSourceKind.MICROPHONE
    assert AudioSourceKind("loopback") is AudioSourceKind.LOOPBACK
    assert AudioSourceKind("both") is AudioSourceKind.BOTH


def test_audio_source_kind_invalid_raises():
    """非法字符串 ValueError (调方负责 fallback)."""
    with pytest.raises(ValueError):
        AudioSourceKind("invalid")
    with pytest.raises(ValueError):
        AudioSourceKind("")


# ── MeetingState.audio_source 字段 ──


def test_meeting_state_default_audio_source():
    """新建 MeetingState 默认 microphone (兼容老客户端)."""
    s = MeetingState(meeting_id="m1")
    assert s.audio_source == AudioSourceKind.MICROPHONE


def test_meeting_state_audio_source_persists_through_serialization():
    """audio_source 字段能 JSON 序列化 + 反序列化."""
    s = MeetingState(meeting_id="m1", audio_source=AudioSourceKind.BOTH)
    j = s.model_dump()
    assert j["audio_source"] == "both"
    s2 = MeetingState.model_validate(j)
    assert s2.audio_source == AudioSourceKind.BOTH


def test_meeting_state_audio_source_loopback():
    s = MeetingState(meeting_id="m1", audio_source=AudioSourceKind.LOOPBACK)
    assert s.audio_source == AudioSourceKind.LOOPBACK


def test_meeting_state_audio_source_default_for_legacy_json():
    """老 JSON (没 audio_source 字段) 加载时默认 microphone — 向后兼容."""
    legacy = {
        "meeting_id": "old",
        "platform": "local",
        "project_name": None,
        "started_at": "2026-06-01T00:00:00+00:00",
        "requirements": [], "goals": [], "features": [], "risks": [], "open_questions": [],
        "speaker_map": {}, "last_updated": "2026-06-01T00:00:00+00:00",
        "vpbuddy_version": "0.5.0",
    }
    s = MeetingState.model_validate(legacy)
    assert s.audio_source == AudioSourceKind.MICROPHONE


# ── storage round-trip ──


def test_audio_source_round_trip_through_storage(tmp_path):
    """audio_source 字段经 storage.save → load 完整往返."""
    from vpbuddy.storage import MeetingStorage
    storage = MeetingStorage(tmp_path)
    s = MeetingState(meeting_id="rt", audio_source=AudioSourceKind.LOOPBACK)
    storage.save(s)
    loaded = storage.load("rt")
    assert loaded.audio_source == AudioSourceKind.LOOPBACK


# ── HTTP 端点: POST /api/meetings/stream_start?audio_source=loopback ──


def _free_port():
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def http_server(tmp_path, monkeypatch):
    """起本地 HTTP server, DATA_DIR → tmp_path."""
    monkeypatch.setattr("vpbuddy.ui_server.DATA_DIR", tmp_path)
    monkeypatch.setattr("vpbuddy.ui_server.DOCS_DIR", tmp_path / "docs")
    from http.server import ThreadingHTTPServer
    from vpbuddy.ui_server import Handler
    monkeypatch.setattr(Handler, "protocol_version", "HTTP/1.0")

    port = _free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    import threading
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield port
    server.shutdown()
    server.server_close()


def _post(url, data=b""):
    import urllib.request
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/octet-stream")
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


def test_stream_start_default_microphone(http_server):
    """不传 audio_source → 默认 microphone."""
    code, body = _post(f"http://127.0.0.1:{http_server}/api/meetings/stream_start")
    assert code == 200
    assert body["audio_source"] == "microphone"


def test_stream_start_loopback(http_server, tmp_path):
    """传 audio_source=loopback → 存到 state."""
    code, body = _post(f"http://127.0.0.1:{http_server}/api/meetings/stream_start?audio_source=loopback")
    assert code == 200
    assert body["audio_source"] == "loopback"

    # 验证 state.json 持久化
    # 验证 state.json 持久化 (排除 .stream.json 和 .chat.json)
    state_files = [
        f for f in tmp_path.glob("*.json")
        if f.stem.startswith("STREAM_") and not f.name.endswith(".stream.json") and not f.name.endswith(".chat.json")
    ]
    assert len(state_files) == 1
    data = json.loads(state_files[0].read_text())
    assert data["audio_source"] == "loopback"


def test_stream_start_both(http_server):
    """传 audio_source=both → 存到 state."""
    code, body = _post(f"http://127.0.0.1:{http_server}/api/meetings/stream_start?audio_source=both")
    assert code == 200
    assert body["audio_source"] == "both"


def test_stream_start_invalid_fallback(http_server, capsys):
    """传非法 audio_source → fallback microphone + warning log."""
    code, body = _post(f"http://127.0.0.1:{http_server}/api/meetings/stream_start?audio_source=invalid_value")
    assert code == 200
    assert body["audio_source"] == "microphone"  # fallback
    # warning print
    captured = capsys.readouterr()
    assert "invalid_value" in captured.out


def test_stream_start_case_insensitive(http_server):
    """audio_source 大小写不敏感 (宽容)."""
    code, body = _post(f"http://127.0.0.1:{http_server}/api/meetings/stream_start?audio_source=LOOPBACK")
    assert code == 200
    assert body["audio_source"] == "loopback"


def test_stream_start_strips_whitespace(http_server):
    code, body = _post(f"http://127.0.0.1:{http_server}/api/meetings/stream_start?audio_source=%20loopback%20")
    assert code == 200
    assert body["audio_source"] == "loopback"


# ── /api/meetings/{id}/state 显示 audio_source ──


def test_state_endpoint_returns_audio_source(http_server, tmp_path):
    """state 端点返 audio_source 字段."""
    # 1. 创建一个 stream 会议
    code, body = _post(f"http://127.0.0.1:{http_server}/api/meetings/stream_start?audio_source=both")
    assert code == 200
    meeting_id = body["meeting_id"]

    # 2. 查 state
    import urllib.request
    code, state_body = _get(f"http://127.0.0.1:{http_server}/api/meetings/{meeting_id}/state")
    assert code == 200
    assert state_body["state"]["audio_source"] == "both"


def _get(url):
    import urllib.request
    with urllib.request.urlopen(url, timeout=5) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


# ── /api/meetings 列表显示 audio_source ──


def test_meetings_list_includes_audio_source(http_server):
    """meetings 列表返的每个会议包含 audio_source."""
    _post(f"http://127.0.0.1:{http_server}/api/meetings/stream_start?audio_source=loopback")
    _post(f"http://127.0.0.1:{http_server}/api/meetings/stream_start?audio_source=microphone")
    code, body = _get(f"http://127.0.0.1:{http_server}/api/meetings")
    assert code == 200
    assert body["count"] == 2
    sources = sorted(m["audio_source"] for m in body["meetings"])
    assert sources == ["loopback", "microphone"]