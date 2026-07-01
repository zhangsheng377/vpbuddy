"""测试 ui_server._validate_meeting_id + check_id + close endpoints (ADR-0022)

起本地 HTTP server (threading), 用 urllib 发请求测 — 比 mock BaseHTTPRequestHandler 简单.
"""

from __future__ import annotations
import json
import sys
import socket
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from vpbuddy.ui_server import (
    _validate_meeting_id,
    Handler,
    DATA_DIR as REAL_DATA_DIR,
)


# ── _validate_meeting_id 纯函数测试 ──


def test_validate_accepts_alphanumeric():
    ok, err = _validate_meeting_id("mtg-2026-q4")
    assert ok is True
    assert err == ""


def test_validate_rejects_too_short():
    ok, err = _validate_meeting_id("ab")
    assert ok is False
    assert "3-32" in err


def test_validate_rejects_too_long():
    ok, err = _validate_meeting_id("a" * 33)
    assert ok is False
    assert "3-32" in err


def test_validate_rejects_spaces():
    ok, err = _validate_meeting_id("my meeting")
    assert ok is False


def test_validate_rejects_chinese():
    """会议 ID 不许中文 (ADR-0022 — 简化 ID, 显示名走 project_name)."""
    ok, err = _validate_meeting_id("我的会议")
    assert ok is False


def test_validate_accepts_underscore_and_dash():
    assert _validate_meeting_id("a_b-c")[0] is True
    assert _validate_meeting_id("123")[0] is True


def test_validate_rejects_special_chars():
    assert _validate_meeting_id("mtg@home")[0] is False
    assert _validate_meeting_id("mtg/2026")[0] is False
    assert _validate_meeting_id("mtg.2026")[0] is False


def test_validate_exact_boundary_lengths():
    """3 字符 OK, 32 字符 OK."""
    assert _validate_meeting_id("abc")[0] is True
    assert _validate_meeting_id("a" * 32)[0] is True
    assert _validate_meeting_id("ab")[0] is False
    assert _validate_meeting_id("a" * 33)[0] is False


# ── 起本地 server 测 HTTP 端点 ──


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def http_server(tmp_path, monkeypatch):
    """起本地 HTTP server, DATA_DIR 指向 tmp_path."""
    # 改 DATA_DIR 到 tmp_path
    monkeypatch.setattr("vpbuddy.ui_server.DATA_DIR", tmp_path)
    # demo_version 内部 import DOCS_DIR, 同样要改, 不然写到了真实 data/docs
    monkeypatch.setattr("vpbuddy.ui_server.DOCS_DIR", tmp_path / "docs")

    port = _free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    # 关 keep-alive (跟生产一致, 防 reqwest 死等)
    monkeypatch.setattr(Handler, "protocol_version", "HTTP/1.0")
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()
    server.server_close()


def _get(url: str) -> tuple[int, dict]:
    import urllib.request
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8") if e.fp else ""
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, {"raw": body}


def _post(url: str) -> tuple[int, dict]:
    import urllib.request
    req = urllib.request.Request(url, method="POST", data=b"")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8") if e.fp else ""
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, {"raw": body}


# ── GET /api/meetings/check_id ──


def test_check_id_returns_valid_for_new(http_server, tmp_path):
    code, body = _get(f"{http_server}/api/meetings/check_id?id=newmeeting")
    assert code == 200
    assert body["id"] == "newmeeting"
    assert body["valid"] is True
    assert body["exists"] is False


def test_check_id_returns_exists_true_for_existing(http_server, tmp_path):
    (tmp_path / "existingmtg.json").write_text("{}")
    code, body = _get(f"{http_server}/api/meetings/check_id?id=existingmtg")
    assert code == 200
    assert body["exists"] is True


def test_check_id_returns_400_for_invalid_format(http_server):
    code, body = _get(f"{http_server}/api/meetings/check_id?id=ab")  # 太短
    assert code == 400
    assert body["valid"] is False


def test_check_id_returns_400_for_missing_id(http_server):
    code, body = _get(f"{http_server}/api/meetings/check_id")
    assert code == 400


def test_check_id_rejects_chinese(http_server):
    from urllib.parse import quote
    code, body = _get(f"{http_server}/api/meetings/check_id?id={quote('我的会议')}")
    assert code == 400
    assert body["valid"] is False


# ── GET /api/meetings/{id}/demo/versions (ADR-0024) ──


def test_demo_versions_endpoint_empty(http_server):
    """新会议没 demo → 返空 versions."""
    code, body = _get(f"{http_server}/api/meetings/newmtg/demo/versions")
    assert code == 200
    assert body["count"] == 0
    assert body["versions"] == []


def test_demo_versions_endpoint_returns_manifest(http_server, tmp_path):
    """有 2 版本 → 返回 manifest 列表."""
    from vpbuddy import demo_version
    demo_version.write_demo_version("m1", "<h1>v1</h1>", trigger="agent_iterate")
    demo_version.write_demo_version("m1", "<h1>v2</h1>", trigger="user_chat")

    code, body = _get(f"{http_server}/api/meetings/m1/demo/versions")
    assert code == 200
    versions = body["versions"]
    assert len(versions) == 2
    # 倒序: 最新在前
    assert versions[0]["version"] == 2
    assert versions[1]["version"] == 1
    assert "summary" in versions[0]
    assert "file_size" in versions[0]
    assert "trigger" in versions[0]


# ── POST /api/meetings/{id}/close ──


def test_close_pushes_meeting_complete_and_closes(http_server, monkeypatch):
    """用户主动 close → 推 meeting-complete + close_meeting."""
    from vpbuddy import realtime_server
    pushed = []
    closed = []

    monkeypatch.setattr(realtime_server, "push_event", lambda mid, t, p: pushed.append((mid, t, p)))
    monkeypatch.setattr(realtime_server, "close_meeting", lambda mid: closed.append(mid) or 2)

    code, body = _post(f"{http_server}/api/meetings/mtg123/close")
    assert code == 200
    assert body["status"] == "closed"
    assert len(pushed) == 1
    assert pushed[0][1] == "meeting-complete"
    assert pushed[0][2]["status"] == "user_closed"
    assert closed == ["mtg123"]


def test_close_handles_exception(http_server, monkeypatch):
    """close_meeting 抛 → 500, 不 raise."""
    from vpbuddy import realtime_server

    def boom(mid):
        raise RuntimeError("kill failed")

    monkeypatch.setattr(realtime_server, "push_event", lambda *a: None)
    monkeypatch.setattr(realtime_server, "close_meeting", boom)

    code, body = _post(f"{http_server}/api/meetings/mtg/close")
    assert code == 500
    assert "error" in body


# ── 区分: docs-complete (notify) vs meeting-complete (close) ──


def test_docs_complete_and_meeting_complete_are_distinct(http_server, tmp_path, monkeypatch):
    """核心区别: docs-complete 是 6 doc 写完触发, meeting-complete 是用户主动 close 触发."""
    from vpbuddy import realtime_server
    from vpbuddy import ui_server_helpers

    events = []
    monkeypatch.setattr(realtime_server, "push_event", lambda mid, t, p: events.append(t))
    monkeypatch.setattr(realtime_server, "close_meeting", lambda mid: 0)

    # 准备 DOCS_DIR
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "m").mkdir()
    for kind in ["req", "arch", "tasks", "api", "risk"]:
        (docs / "m" / f"{kind}.md").write_text("x")
    (docs / "m" / "demo" / "demo.html").parent.mkdir(parents=True, exist_ok=True)
    (docs / "m" / "demo" / "demo.html").write_text("x")
    monkeypatch.setattr("vpbuddy.ui_server.DOCS_DIR", docs)

    # 1. 6 doc 写完 → notify
    ui_server_helpers.check_all_docs_stored_notify("m")
    # 2. 用户手动 close
    _post(f"{http_server}/api/meetings/m/close")

    assert "docs-complete" in events
    assert "meeting-complete" in events
    assert events.index("docs-complete") < events.index("meeting-complete")


# ── 流式: stream_stop 仍可用 (旧 API 兼容) ──


def test_stream_stop_still_works_but_only_closes_subscribers(http_server, monkeypatch):
    """老调用方 stop_capture → stream_stop 仍 OK, 但不推 meeting-complete (跟 close 区分)."""
    from vpbuddy import realtime_server
    pushed = []

    monkeypatch.setattr(realtime_server, "push_event", lambda mid, t, p: pushed.append(t))
    monkeypatch.setattr(realtime_server, "close_meeting", lambda mid: 1)

    code, body = _post(f"{http_server}/api/meetings/mtg/stream_stop")
    assert code == 200
    assert body["closed_subscribers"] == 1
    assert "meeting-complete" not in pushed  # 老 API 不推 complete