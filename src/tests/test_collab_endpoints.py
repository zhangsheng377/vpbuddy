"""测试 ui_server 的 3 个 collab 端点 (ADR-0028 Commit 2).

覆盖:
- GET /api/meetings/{id}/collab (返 collab.md + pending/answered/stats)
- POST /api/meetings/{id}/ask_question (含节流 + SSE 推)
- POST /api/meetings/{id}/answer_question (含 SSE 推 + 错误码)

测试约定: bytes literal 不含非 ASCII (tokenize 限制).
"""

from __future__ import annotations

import json
import socket
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from vpbuddy.ui_server import Handler
from vpbuddy import collab as collab_mod


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def http_server(tmp_path, monkeypatch):
    """起本地 HTTP server, DATA_DIR + DOCS_DIR 都指向 tmp_path."""
    monkeypatch.setattr("vpbuddy.ui_server.DATA_DIR", tmp_path / "data")
    monkeypatch.setattr("vpbuddy.ui_server.DOCS_DIR", tmp_path / "docs")
    monkeypatch.setattr("vpbuddy.collab._default_docs_dir", lambda: tmp_path / "docs")

    port = _free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    monkeypatch.setattr(Handler, "protocol_version", "HTTP/1.0")
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()
    server.server_close()


def _get(url: str) -> tuple[int, dict]:
    import urllib.error
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


def _post(url: str, body: bytes = b"", content_type: str = "application/octet-stream") -> tuple[int, dict]:
    import urllib.error
    import urllib.request
    req = urllib.request.Request(url, data=body, headers={"Content-Type": content_type}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body_text: bytes = e.read() if e.fp else b""
        try:
            return e.code, json.loads(body_text.decode("utf-8"))
        except Exception:
            return e.code, {"raw": body_text.decode("utf-8", errors="replace")}


# ── GET /api/meetings/{id}/collab ──


def test_collab_get_empty(http_server):
    """新会议没 collab.md → 返空 collab + 空 pending/answered + stats.exists=False."""
    code, body = _get(f"{http_server}/api/meetings/empty_mtg/collab")
    assert code == 200
    assert body["meeting_id"] == "empty_mtg"
    assert body["collab"] == ""
    assert body["pending"] == []
    assert body["answered"] == []
    assert body["stats"]["exists"] is False
    assert body["stats"]["total"] == 0


def test_collab_get_after_ask(http_server, tmp_path):
    """先 ask → GET collab 应含 pending."""
    # 直接通过 collab API 制造数据 (绕开 HTTP 测纯服务端逻辑)
    collab_mod.ask_question("after_ask_mtg", "req", "客户预算?", docs_dir=tmp_path / "docs")

    code, body = _get(f"{http_server}/api/meetings/after_ask_mtg/collab")
    assert code == 200
    assert len(body["pending"]) == 1
    assert body["pending"][0]["section"] == "req"
    assert body["pending"][0]["question"] == "客户预算?"
    assert body["stats"]["exists"] is True
    assert body["stats"]["pending"] == 1


def test_collab_get_after_answer(http_server, tmp_path):
    """先 ask + answer → GET collab 应 pending=0, answered=1."""
    docs = tmp_path / "docs"
    r1 = collab_mod.ask_question("ans_get_mtg", "req", "Q?", docs_dir=docs)
    collab_mod.answer_question("ans_get_mtg", r1["qid"], "A", docs_dir=docs)

    code, body = _get(f"{http_server}/api/meetings/ans_get_mtg/collab")
    assert code == 200
    assert body["pending"] == []
    assert len(body["answered"]) == 1
    assert body["answered"][0]["answer"] == "A"
    assert body["stats"]["answered"] == 1


# ── POST /api/meetings/{id}/ask_question ──


def test_collab_ask_basic(http_server):
    """正常 ask → 200, 返 qid, status=added."""
    from urllib.parse import quote
    code, body = _post(
        f"{http_server}/api/meetings/ask_basic_mtg/ask_question"
        f"?section=req&question={quote('client budget?')}&asker=chat"
    )
    assert code == 200
    assert body["ok"] is True
    assert body["status"] == "added"
    assert body["qid"].startswith("q-")


def test_collab_ask_throttled(http_server):
    """同 section + 相似问题 → 第二次 throttled."""
    from urllib.parse import quote
    q1 = quote("client budget for Q4?")
    _post(f"{http_server}/api/meetings/throttle_mtg/ask_question?section=req&question={q1}")
    code, body = _post(
        f"{http_server}/api/meetings/throttle_mtg/ask_question"
        f"?section=req&question={quote('client budget for Q4?')}"
    )
    assert code == 200
    assert body["status"] == "duplicate_exact"


def test_collab_ask_different_section_not_throttled(http_server):
    """不同 section → 不节流."""
    from urllib.parse import quote
    _post(f"{http_server}/api/meetings/sec_mtg/ask_question?section=req&question={quote('budget?')}")
    code, body = _post(
        f"{http_server}/api/meetings/sec_mtg/ask_question"
        f"?section=demo&question={quote('budget?')}"
    )
    assert code == 200
    assert body["status"] == "added"


def test_collab_ask_missing_params(http_server):
    """缺 section / question → 400."""
    code, body = _post(f"{http_server}/api/meetings/miss_mtg/ask_question")
    assert code == 400
    assert "section 和 question" in body["error"]

    from urllib.parse import quote
    code, body = _post(f"{http_server}/api/meetings/miss_mtg/ask_question?section=req")
    assert code == 400


def test_collab_ask_pushes_sse(http_server, monkeypatch):
    """ask 成功推 SSE collab-update."""
    import vpbuddy.realtime_server as rt
    pushed = []
    monkeypatch.setattr(rt, "push_event", lambda mid, t, p: pushed.append((mid, t, p)))

    from urllib.parse import quote
    _post(f"{http_server}/api/meetings/sse_mtg/ask_question?section=req&question={quote('test q?')}")

    # 至少 1 条 collab-update (action=ask)
    assert any(t == "collab-update" and p.get("action") == "ask" for (mid, t, p) in pushed)


# ── POST /api/meetings/{id}/answer_question ──


def test_collab_answer_basic(http_server):
    """ask → answer → 200 answered."""
    from urllib.parse import quote
    # ask
    code1, body1 = _post(
        f"{http_server}/api/meetings/ans_basic_mtg/ask_question"
        f"?section=req&question={quote('Q1?')}"
    )
    qid = body1["qid"]

    # answer
    code2, body2 = _post(
        f"{http_server}/api/meetings/ans_basic_mtg/answer_question"
        f"?qid={qid}&answer={quote('A1')}"
    )
    assert code2 == 200
    assert body2["ok"] is True
    assert body2["status"] == "answered"


def test_collab_answer_not_found(http_server):
    """qid 不存在 → 404 + error 描述."""
    from urllib.parse import quote
    code, body = _post(
        f"{http_server}/api/meetings/nf_mtg/answer_question"
        f"?qid=q-nonexistent&answer={quote('A')}"
    )
    assert code == 404
    # error 文本可能是 "collab.md not exist" 或 "qid ... not found"
    assert "error" in body
    assert "not" in body["error"].lower()


def test_collab_answer_missing_params(http_server):
    """缺 qid / answer → 400."""
    code, body = _post(f"{http_server}/api/meetings/miss_mtg/answer_question")
    assert code == 400

    from urllib.parse import quote
    code, body = _post(f"{http_server}/api/meetings/miss_mtg/answer_question?qid=q-x")
    assert code == 400


def test_collab_answer_pushes_sse(http_server, monkeypatch):
    """answer 推 SSE collab-update."""
    import vpbuddy.realtime_server as rt
    pushed = []
    monkeypatch.setattr(rt, "push_event", lambda mid, t, p: pushed.append((mid, t, p)))

    from urllib.parse import quote
    _post(f"{http_server}/api/meetings/sse_ans_mtg/ask_question?section=req&question={quote('Q?')}")
    pushed.clear()
    # 重新 ask (因为前一次没 qid 抓)
    # 实际: 抓 qid from response
    code, body = _post(
        f"{http_server}/api/meetings/sse_ans_mtg/ask_question?section=req&question={quote('Q unique')}"
    )
    qid = body["qid"]

    pushed.clear()
    _post(f"{http_server}/api/meetings/sse_ans_mtg/answer_question?qid={qid}&answer=A")
    assert any(t == "collab-update" and p.get("action") == "answer" for (mid, t, p) in pushed)


# ── 端到端: ask → answer → GET 应看到 answered ──


def test_collab_e2e_ask_answer_get(http_server):
    """完整流程: ask → answer → GET collab 应看到 pending 移走."""
    from urllib.parse import quote
    mid = "e2e_mtg"

    # ask
    _post(f"{http_server}/api/meetings/{mid}/ask_question?section=req&question={quote('E2E question?')}")
    # get — 1 pending
    code, body = _get(f"{http_server}/api/meetings/{mid}/collab")
    assert len(body["pending"]) == 1
    qid = body["pending"][0]["qid"]

    # answer
    _post(f"{http_server}/api/meetings/{mid}/answer_question?qid={qid}&answer={quote('E2E answer')}")

    # get — 0 pending, 1 answered
    code, body = _get(f"{http_server}/api/meetings/{mid}/collab")
    assert body["pending"] == []
    assert len(body["answered"]) == 1
    assert body["answered"][0]["qid"] == qid
    assert body["answered"][0]["answer"] == "E2E answer"