"""测试 ui_server_helpers.check_all_docs_stored_notify — ADR-0022 关键改动:

6 docs 全 stored 后:
- **不** 调 close_meeting (会议继续)
- **不** 推 SSE 事件 (2026-07-02 删 docs-complete 死代码, 客户端靠 doc-status 实时显示)
"""

from __future__ import annotations
import sys
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from vpbuddy import ui_server_helpers, realtime_server


@pytest.fixture
def fake_docs_dir(tmp_path, monkeypatch):
    """临时 DOCS_DIR, 写满 6 个 doc 文件."""
    from vpbuddy import ui_server
    docs = tmp_path / "docs"
    docs.mkdir()
    meeting = "testmtg"
    (docs / meeting).mkdir()
    for kind in ["req", "arch", "tasks", "api", "risk"]:
        (docs / meeting / f"{kind}.md").write_text(f"# {kind}\nbody")
    (docs / meeting / "demo" / "demo.html").parent.mkdir(parents=True, exist_ok=True)
    (docs / meeting / "demo" / "demo.html").write_text("<html></html>")

    monkeypatch.setattr(ui_server, "DOCS_DIR", docs)
    return docs / meeting


def test_notify_does_NOT_push_any_event(fake_docs_dir, monkeypatch):
    """2026-07-02 简化: 不推 docs-complete SSE (死代码, 客户端不消费)."""
    pushed = []
    monkeypatch.setattr(realtime_server, "push_event", lambda *a: pushed.append(a))

    out = ui_server_helpers.check_all_docs_stored_notify("testmtg")
    assert out is True
    assert pushed == [], f"应不推任何事件, 实际推: {pushed}"


def test_notify_does_NOT_close_meeting(fake_docs_dir, monkeypatch):
    """核心断言: 6 docs 完成**不**调 close_meeting (ADR-0022)."""
    closed_called = []

    def fake_push(mid, t, p):
        pass

    def fake_close(mid):
        closed_called.append(mid)
        return 1

    monkeypatch.setattr(realtime_server, "push_event", fake_push)
    monkeypatch.setattr(realtime_server, "close_meeting", fake_close)

    ui_server_helpers.check_all_docs_stored_notify("testmtg")
    assert closed_called == []  # 关键: 没 close


def test_notify_returns_false_if_any_doc_missing(tmp_path, monkeypatch):
    """少一个 doc → 不推 docs-complete, 不 close."""
    from vpbuddy import ui_server
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "partial").mkdir()
    (docs / "partial" / "req.md").write_text("x")
    # 其他 5 个没写

    monkeypatch.setattr(ui_server, "DOCS_DIR", docs)

    pushed = []
    closed = []
    monkeypatch.setattr(realtime_server, "push_event", lambda *a: pushed.append(a))
    monkeypatch.setattr(realtime_server, "close_meeting", lambda mid: closed.append(mid))

    out = ui_server_helpers.check_all_docs_stored_notify("partial")
    assert out is False
    assert pushed == []
    assert closed == []


def test_notify_returns_false_if_doc_empty(tmp_path, monkeypatch):
    """doc 文件存在但为空 → 不推."""
    from vpbuddy import ui_server
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "m").mkdir()
    for kind in ["req", "arch", "tasks", "api", "risk"]:
        (docs / "m" / f"{kind}.md").write_text("")
    (docs / "m" / "demo" / "demo.html").parent.mkdir(parents=True, exist_ok=True)
    (docs / "m" / "demo" / "demo.html").write_text("")  # 空
    monkeypatch.setattr(ui_server, "DOCS_DIR", docs)

    pushed = []
    monkeypatch.setattr(realtime_server, "push_event", lambda *a: pushed.append(a))

    out = ui_server_helpers.check_all_docs_stored_notify("m")
    assert out is False
    assert pushed == []


def test_old_alias_does_not_close_either(fake_docs_dir, monkeypatch):
    """旧名 check_all_docs_stored_and_close 现在是 DEPRECATED alias — 也只 notify 不 close."""
    closed = []
    monkeypatch.setattr(realtime_server, "push_event", lambda *a: None)
    monkeypatch.setattr(realtime_server, "close_meeting", lambda mid: closed.append(mid))

    ui_server_helpers.check_all_docs_stored_and_close("testmtg")
    assert closed == []  # 老名字也不关


def test_notify_returns_true_even_without_push_event(fake_docs_dir, monkeypatch):
    """2026-07-02: 函数不再调 push_event, 即使 realtime_server.push_event 不存在/抛异常, 函数仍正常返 True."""
    # push_event 不存在也不影响 — 函数根本没引用它
    out = ui_server_helpers.check_all_docs_stored_notify("testmtg")
    assert out is True  # 6 docs 全 stored 事实成立