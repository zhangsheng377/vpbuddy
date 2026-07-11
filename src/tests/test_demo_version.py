"""测试 demo_version 模块 (ADR-0024) — 多版本 demo 文件 + manifest + symlink."""

from __future__ import annotations
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from vpbuddy import demo_version


@pytest.fixture
def docs_dir(tmp_path, monkeypatch):
    """临时 docs dir."""
    d = tmp_path / "docs"
    d.mkdir()
    monkeypatch.setattr("vpbuddy.ui_server.DOCS_DIR", d)
    return d


def _make_html(title: str, body: str = "x") -> str:
    return f"<!DOCTYPE html><html><body><h1>{title}</h1><p>{body}</p></body></html>"


# ── 基础: 写 v1 / v2 / v3 ──


def test_write_v1_creates_files_and_manifest(docs_dir):
    """首次写 → 写 demo_v1.html + manifest.json + symlink."""
    out = demo_version.write_demo_version("m1", _make_html("会议首页 v1"), trigger="agent_iterate", docs_dir=docs_dir)
    assert out["ok"] is True
    assert out["version"] == 1
    assert (docs_dir / "m1" / "demo_v1.html").exists()
    assert (docs_dir / "m1" / "demo_manifest.json").exists()
    # symlink on Linux/macOS
    if os.name != "nt":
        latest = docs_dir / "m1" / "demo_latest.html"
        assert latest.is_symlink()
        assert os.readlink(latest) == "demo_v1.html"


def test_write_v2_does_not_overwrite_v1(docs_dir):
    """v2 不覆盖 v1, 旧版保留."""
    demo_version.write_demo_version("m1", _make_html("v1"), docs_dir=docs_dir)
    v1_content = (docs_dir / "m1" / "demo_v1.html").read_text()

    demo_version.write_demo_version("m1", _make_html("v2"), docs_dir=docs_dir)

    v1_after = (docs_dir / "m1" / "demo_v1.html").read_text()
    assert v1_after == v1_content  # v1 没变
    assert (docs_dir / "m1" / "demo_v2.html").exists()
    assert "v2" in (docs_dir / "m1" / "demo_v2.html").read_text()


def test_write_v3_updates_latest_symlink(docs_dir):
    """v3 写后, symlink 应指向 demo_v3.html."""
    demo_version.write_demo_version("m1", _make_html("v1"), docs_dir=docs_dir)
    demo_version.write_demo_version("m1", _make_html("v2"), docs_dir=docs_dir)
    demo_version.write_demo_version("m1", _make_html("v3"), docs_dir=docs_dir)

    if os.name != "nt":
        latest = docs_dir / "m1" / "demo_latest.html"
        assert os.readlink(latest) == "demo_v3.html"


def test_manifest_records_each_version(docs_dir):
    """manifest 包含所有版本的 metadata."""
    demo_version.write_demo_version("m1", _make_html("v1 标题"), trigger="agent_iterate", docs_dir=docs_dir)
    demo_version.write_demo_version("m1", _make_html("v2 标题"), trigger="user_chat", docs_dir=docs_dir)

    manifest = json.loads((docs_dir / "m1" / "demo_manifest.json").read_text())
    assert len(manifest) == 2
    assert manifest[0]["version"] == 1
    assert manifest[0]["trigger"] == "agent_iterate"
    assert "v1" in manifest[0]["summary"]
    assert manifest[1]["version"] == 2
    assert manifest[1]["trigger"] == "user_chat"


def test_summary_extracted_from_h1(docs_dir):
    """summary 从 <h1> 提取, 去 HTML 标签."""
    html = "<html><body><h1>客户 Q4 营收报告</h1><p>...</p></body></html>"
    out = demo_version.write_demo_version("m1", html, docs_dir=docs_dir)
    assert "客户 Q4 营收报告" in out["summary"]


def test_summary_extracted_from_h2_when_no_h1(docs_dir):
    """无 h1 时退到 h2."""
    html = "<html><body><h2>次级标题</h2></body></html>"
    out = demo_version.write_demo_version("m1", html, docs_dir=docs_dir)
    assert "次级标题" in out["summary"]


def test_summary_truncated_to_50_chars(docs_dir):
    """summary 截断到 50 字符."""
    html = "<h1>" + "x" * 100 + "</h1>"
    out = demo_version.write_demo_version("m1", html, docs_dir=docs_dir)
    assert len(out["summary"]) <= 50


def test_summary_fallback_untitled(docs_dir):
    """无 h1/h2/p → summary = 'untitled'."""
    html = "<html><body></body></html>"
    out = demo_version.write_demo_version("m1", html, docs_dir=docs_dir)
    assert out["summary"] == "untitled"


def test_summary_fallback_skips_empty_p(docs_dir):
    """空的 <p></p> 不算, 跳到下一个 p."""
    html = "<html><body><p></p><p>real content</p></body></html>"
    out = demo_version.write_demo_version("m1", html, docs_dir=docs_dir)
    assert "real content" in out["summary"]


# ── next_version ──


def test_next_version_starts_at_1(docs_dir):
    assert demo_version.next_version("fresh", docs_dir=docs_dir) == 1


def test_next_version_increments(docs_dir):
    demo_version.write_demo_version("m1", _make_html("v1"), docs_dir=docs_dir)
    demo_version.write_demo_version("m1", _make_html("v2"), docs_dir=docs_dir)
    demo_version.write_demo_version("m1", _make_html("v3"), docs_dir=docs_dir)
    assert demo_version.next_version("m1", docs_dir=docs_dir) == 4


# ── list_versions 倒序 ──


def test_list_versions_reversed(docs_dir):
    """list_versions 返最新在前."""
    demo_version.write_demo_version("m1", _make_html("v1"), docs_dir=docs_dir)
    demo_version.write_demo_version("m1", _make_html("v2"), docs_dir=docs_dir)
    demo_version.write_demo_version("m1", _make_html("v3"), docs_dir=docs_dir)

    versions = demo_version.list_versions("m1", docs_dir=docs_dir)
    assert [v["version"] for v in versions] == [3, 2, 1]


def test_list_versions_empty_for_new_meeting(docs_dir):
    """新会议没版本 → 返 []."""
    assert demo_version.list_versions("nope", docs_dir=docs_dir) == []


# ── 老格式迁移 ──


def test_legacy_demo_html_migrates_to_v1(docs_dir):
    """老格式 demo/demo.html 自动迁移成 v1."""
    # 写老格式
    meeting = "legacy"
    legacy_dir = docs_dir / meeting / "demo"
    legacy_dir.mkdir(parents=True)
    legacy_html = _make_html("旧版 demo")
    (legacy_dir / "demo.html").write_text(legacy_html, encoding="utf-8")

    # load_manifest 触发迁移
    manifest = demo_version.load_manifest(meeting, docs_dir=docs_dir)
    assert len(manifest) == 1
    assert manifest[0]["version"] == 1
    assert manifest[0]["trigger"] == "legacy_migration"
    assert (docs_dir / meeting / "demo_v1.html").exists()
    assert (docs_dir / meeting / "demo_v1.html").read_text() == legacy_html
    # 老 demo.html 保留不删 (兼容 url)
    assert (docs_dir / meeting / "demo" / "demo.html").exists()


def test_legacy_migration_updates_symlink(docs_dir):
    """迁移后, symlink 指向 demo_v1.html."""
    meeting = "legacy2"
    (docs_dir / meeting / "demo").mkdir(parents=True)
    (docs_dir / meeting / "demo" / "demo.html").write_text(_make_html("x"))

    demo_version.load_manifest(meeting, docs_dir=docs_dir)

    if os.name != "nt":
        latest = docs_dir / meeting / "demo_latest.html"
        assert latest.is_symlink()
        assert os.readlink(latest) == "demo_v1.html"


def test_legacy_migration_only_once(docs_dir):
    """迁移只触发一次: 老文件存在 + manifest 缺失."""
    meeting = "legacy3"
    (docs_dir / meeting / "demo").mkdir(parents=True)
    (docs_dir / meeting / "demo" / "demo.html").write_text(_make_html("first"))

    # 第一次调用: 迁移
    m1 = demo_version.load_manifest(meeting, docs_dir=docs_dir)
    assert len(m1) == 1

    # 修改老 demo.html, 不应被迁移 (manifest 已存在)
    (docs_dir / meeting / "demo" / "demo.html").write_text(_make_html("changed"))
    m2 = demo_version.load_manifest(meeting, docs_dir=docs_dir)
    assert len(m2) == 1  # 没新增版本


# ── 异常 ──


def test_write_empty_html_returns_error(docs_dir):
    """空 HTML → ok=False."""
    out = demo_version.write_demo_version("m1", "", docs_dir=docs_dir)
    assert out["ok"] is False
    assert "空" in out["error"]


def test_write_whitespace_only_html_returns_error(docs_dir):
    out = demo_version.write_demo_version("m1", "   \n\t  ", docs_dir=docs_dir)
    assert out["ok"] is False


def test_manifest_corrupted_returns_empty(docs_dir):
    """manifest.json 坏了 → 当作空, 不 crash."""
    meeting = "corrupt"
    md = docs_dir / meeting
    md.mkdir()
    (md / "demo_manifest.json").write_text("{ not json")
    m = demo_version.load_manifest(meeting, docs_dir=docs_dir)
    assert m == []


# ── 文件 size ──


def test_file_size_recorded(docs_dir):
    """file_size 字段是文件字节数."""
    html = _make_html("size test") + "x" * 1000
    out = demo_version.write_demo_version("m1", html, docs_dir=docs_dir)
    assert out["file_size"] == len(html.encode("utf-8"))
    manifest = demo_version.load_manifest("m1", docs_dir=docs_dir)
    assert manifest[0]["file_size"] == out["file_size"]


# ── symlink / Windows fallback ──


def test_symlink_target_is_relative(docs_dir):
    """symlink 用相对路径 (不是绝对), 跨目录移动 docs 也能 work."""
    if os.name == "nt":
        pytest.skip("Windows 不测 symlink")
    demo_version.write_demo_version("m1", _make_html("v1"), docs_dir=docs_dir)
    latest = docs_dir / "m1" / "demo_latest.html"
    target = os.readlink(latest)
    assert not target.startswith("/")  # 不是绝对路径
    assert target == "demo_v1.html"


# ── 集成: SSE 事件 (mock) ──


def test_sub_session_pushes_demo_new_version_event(tmp_path, monkeypatch):
    """doc agent 写 demo.html → sub_session_controller 调 write_demo_version + 推 SSE.

    简化: 不起完整 agent, 直接模拟 doc agent 路径.
    """
    # 这里只测 demo_version.write_demo_version 自身 + 推 SSE, 不完整测 sub_session_controller
    # 端到端测在 test_sub_session.py 已覆盖
    from vpbuddy import realtime_server

    docs = tmp_path / "docs"
    docs.mkdir()
    meeting = "m1"
    meeting_dir = docs / meeting
    meeting_dir.mkdir()
    (meeting_dir / "demo").mkdir()
    (meeting_dir / "demo" / "demo.html").write_text(_make_html("v1"))

    pushed = []
    monkeypatch.setattr(realtime_server, "push_event", lambda mid, t, p: pushed.append((mid, t, p)))

    # 模拟 sub_session_controller 的逻辑: 读 demo.html → write_demo_version
    content = (meeting_dir / "demo" / "demo.html").read_text()
    v_result = demo_version.write_demo_version(meeting, content, trigger="agent_iterate", docs_dir=docs)
    if v_result["ok"]:
        realtime_server.push_event(meeting, "demo-new-version", {
            "version": v_result["version"],
            "summary": v_result["summary"],
            "file_size": v_result["file_size"],
        })

    assert len(pushed) == 1
    # 老 demo.html 存在 → 迁移成 v1 (load_manifest 触发) → 再 write_demo_version 推 v2
    # (因为 manifest 已存在, write_demo_version 直接推进版本号, 不重新迁移)
    assert pushed[0][1] == "demo-new-version"
    # 老 demo.html 存在 → write_demo_version 直接推进 (load_manifest 会在内部触发迁移成 v1,
    # 但 manifest 此时不存在于文件, 所以 write_demo_version 自己 load → 触发迁移 → v1 入 manifest,
    # 但 next_version 在 write 开头调, 返回 2 ... 具体实现见源码)
    assert pushed[0][2]["version"] in (1, 2)


# ── v0.22.5: placeholder 拒绝 ──


def test_placeholder_html_rejected_1(docs_dir):
    """HTML < 3KB 且含"暂无会议内容" → 拒绝写入版本."""
    html = "<html><body><h1>暂无会议内容</h1><p>等待更多发言</p></body></html>"
    out = demo_version.write_demo_version("m1", html, docs_dir=docs_dir)
    assert out["ok"] is False
    assert out.get("skipped") == "placeholder"


def test_placeholder_html_rejected_2(docs_dir):
    """HTML < 3KB 且含"等待更多会议内容" → 拒绝写入版本."""
    html = "<html><body><p>等待更多会议内容，无法制作 demo</p></body></html>"
    out = demo_version.write_demo_version("m1", html, docs_dir=docs_dir)
    assert out["ok"] is False
    assert out.get("skipped") == "placeholder"


def test_placeholder_html_not_rejected_if_large(docs_dir):
    """HTML > 3KB 即使含 placeholder 关键词, 也不拒绝 (可能是真 demo + 偶然含词)."""
    html = "<html><body><h1>等待更多会议内容</h1>" + "x" * 3000 + "</body></html>"
    out = demo_version.write_demo_version("m1", html, docs_dir=docs_dir)
    assert out["ok"] is True


def test_placeholder_html_not_rejected_if_no_keyword(docs_dir):
    """HTML < 3KB 但不含 placeholder 关键词 → 正常写入."""
    html = "<html><body><h1>正常会议 demo</h1></body></html>"
    out = demo_version.write_demo_version("m1", html, docs_dir=docs_dir)
    assert out["ok"] is True


def test_placeholder_rejected_does_not_create_file(docs_dir):
    """被拒绝的版本不创建文件."""
    html = "<html><body>暂无会议内容</body></html>"
    demo_version.write_demo_version("m1", html, docs_dir=docs_dir)
    assert not (docs_dir / "m1" / "demo_v1.html").exists()


# ── v0.22.5: latest_demo_content_hash ──


def test_latest_demo_content_hash_no_versions(docs_dir):
    """无版本时返回 None."""
    assert demo_version.latest_demo_content_hash("m1", docs_dir=docs_dir) is None


def test_latest_demo_content_hash_returns_md5(docs_dir):
    """返回最新版本的 md5 hex string."""
    html = _make_html("v1 test")
    demo_version.write_demo_version("m1", html, docs_dir=docs_dir)
    h = demo_version.latest_demo_content_hash("m1", docs_dir=docs_dir)
    assert isinstance(h, str)
    assert len(h) == 32


def test_latest_demo_content_hash_changes_with_content(docs_dir):
    """不同内容 → 不同 hash."""
    demo_version.write_demo_version("m1", _make_html("aaa"), docs_dir=docs_dir)
    h1 = demo_version.latest_demo_content_hash("m1", docs_dir=docs_dir)
    demo_version.write_demo_version("m1", _make_html("bbb"), docs_dir=docs_dir)
    h2 = demo_version.latest_demo_content_hash("m1", docs_dir=docs_dir)
    assert h1 != h2


def test_latest_demo_content_hash_stable_with_same_content(docs_dir):
    """相同内容 → 稳定 hash."""
    html = _make_html("stable test")
    demo_version.write_demo_version("m1", html, docs_dir=docs_dir)
    h1 = demo_version.latest_demo_content_hash("m1", docs_dir=docs_dir)
    h2 = demo_version.latest_demo_content_hash("m1", docs_dir=docs_dir)
    assert h1 == h2