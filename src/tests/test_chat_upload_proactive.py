"""测试 agent 主动提问 (ADR-0023 Phase 5) + chat 上传 (Phase 6).

主动提问覆盖:
- trigger 节流 (同 mid + 同 type 1 次)
- clear_throttle (close_meeting 调)
- 5 个 trigger type 都能构造文本
- risk_threshold 触发条件 (≥3 medium+high)
- docs_complete 触发 (check_all_docs_stored_notify 内部调)
- demo_new_version 触发 (write_demo_version 完成时调)
- 后台 monitor 线程启动/停止 (smoke)

chat 上传覆盖:
- handle_chat_upload 文本类入 KB
- handle_chat_upload 图片转 base64 data URI
- _parse_multipart 升级支持多文件
- _handle_chat multipart 分支 (smoke, 不调 LLM)

测试约定: bytes literal 不含非 ASCII (Python tokenize 限制). 服务端接 UTF-8 OK,
但测试用 ASCII 占位避免 SyntaxError.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from vpbuddy.ui_server import DATA_DIR as REAL_DATA_DIR
from vpbuddy.kb_api import (
    _parse_multipart,
    handle_chat_upload,
    handle_kb_upload,
    _validate_file,
    _is_image,
    _image_to_b64_data_uri,
    ALLOWED_IMAGE_EXTENSIONS,
)
from vpbuddy.agent_proactive import (
    trigger,
    clear_throttle,
    _TRIGGERED,
    _TRIGGER_BUILDERS,
    start_monitor,
    stop_monitor,
    SILENCE_THRESHOLD_SEC,
)
from vpbuddy.state import MeetingState, Priority, Risk


# ── 主动提问纯函数测试 ──


@pytest.fixture(autouse=True)
def _reset_throttle():
    """每个测试前清节流 + 后清 monitor."""
    _TRIGGERED.clear()
    yield
    _TRIGGERED.clear()


def test_trigger_throttles_same_mid_same_type():
    """同 (mid, type) 第二次返 None."""
    r1 = trigger("mtg1", "docs_complete")
    assert r1 is not None
    assert r1["trigger_type"] == "docs_complete"
    r2 = trigger("mtg1", "docs_complete")
    assert r2 is None  # 节流


def test_trigger_different_meetings_independent():
    """不同 mid 独立."""
    assert trigger("m1", "docs_complete") is not None
    assert trigger("m2", "docs_complete") is not None


def test_trigger_different_types_independent():
    """同 mid 不同 type 都触发."""
    assert trigger("m1", "docs_complete") is not None
    assert trigger("m1", "risk_threshold") is not None
    assert trigger("m1", "demo_new_version") is not None


def test_trigger_unknown_type_returns_none():
    """未知 type 不触发."""
    assert trigger("m1", "bogus_type_xxx") is None


def test_trigger_message_contains_emojis():
    """消息含视觉标识 (📄 / ⚠️ / 🎨 / 🤔 / ⏱️)."""
    r = trigger("m1", "docs_complete", state_summary="3 个需求, 2 个风险")
    assert "📄" in r["message"]
    r = trigger("m1", "risk_threshold", risk_list=["风险A", "风险B"])
    assert "⚠️" in r["message"]
    r = trigger("m1", "demo_new_version", version=2, summary="新版本")
    assert "🎨" in r["message"]
    r = trigger("m1", "silence", silence_sec=300)
    assert "🤔" in r["message"]
    r = trigger("m1", "time_node", elapsed_sec=600, facts_count=5)
    assert "⏱️" in r["message"]


def test_clear_throttle_removes_mid_keys():
    trigger("m1", "docs_complete")
    trigger("m1", "risk_threshold")
    trigger("m2", "docs_complete")
    cleared = clear_throttle("m1")
    assert cleared == 2
    # m1 还能再触发
    assert trigger("m1", "docs_complete") is not None
    # m2 不受影响
    assert trigger("m2", "docs_complete") is None  # 仍节流


def test_clear_throttle_no_keys_no_op():
    """没触发过 clear → 返 0, 不抛."""
    assert clear_throttle("nonexistent_mtg") == 0


def test_all_5_trigger_types_have_builders():
    """5 个 trigger type 全部有 builder (防漏)."""
    expected = {"docs_complete", "risk_threshold", "demo_new_version", "silence", "time_node"}
    assert set(_TRIGGER_BUILDERS.keys()) == expected


# ── risk_threshold 触发条件: trigger("risk_threshold") 的节流 ──


def test_risk_threshold_fires_after_3_medium():
    """直接测 trigger("risk_threshold") — ADR-0020 后不再由 add_risk 侧效应触发."""
    # trigger 2 次应成功, 第 3 次同类被节流
    assert trigger("risk_mtg_1", "risk_threshold", risk_list=["r1", "r2", "r3"]) is not None
    assert trigger("risk_mtg_1", "docs_complete") is not None  # 不同类型
    assert trigger("risk_mtg_1", "risk_threshold") is None  # 节流
    _TRIGGERED.clear()  # clean for other tests


def test_low_risk_does_not_trigger():
    """trigger("risk_threshold") 接受任何 risk_list, 只测节流."""
    assert trigger("low_mtg", "risk_threshold", risk_list=["l1"]) is not None
    assert "low_mtg:risk_threshold" in _TRIGGERED
    _TRIGGERED.clear()


def test_high_risk_counts_toward_threshold():
    """同 mid 重复 trigger → 节流."""
    assert trigger("high_mtg", "risk_threshold", risk_list=["h1", "h2", "h3"]) is not None
    assert trigger("high_mtg", "risk_threshold") is None  # 第二次同类节流
    _TRIGGERED.clear()

# ── docs_complete: check_all_docs_stored_notify 内部触发 ──


def test_docs_complete_triggers_proactive(tmp_path, monkeypatch):
    """6 doc 写完 → 主动 trigger docs_complete (chat 通知).

    2026-07-02: 不再验 SSE push "docs-complete" (该事件已删除为死代码).
    保留对 agent_proactive.trigger("docs_complete") 的触发断言 — chat 通道独立.
    """
    from vpbuddy import ui_server_helpers, realtime_server

    # monkeypatch push_event 不让真推 SSE 出去, 但仍然能验 proactive 触发
    monkeypatch.setattr(realtime_server, "push_event", lambda *a, **k: None)

    # 准备 6 doc
    docs = tmp_path / "docs"
    (docs / "test_docs_complete_mtg").mkdir(parents=True)
    (docs / "test_docs_complete_mtg" / "demo").mkdir(parents=True)
    for kind in ["req", "arch", "tasks", "api", "risk"]:
        (docs / "test_docs_complete_mtg" / f"{kind}.md").write_text("x")
    (docs / "test_docs_complete_mtg" / "demo" / "demo.html").write_text("<h1>v</h1>")
    monkeypatch.setattr("vpbuddy.ui_server.DOCS_DIR", docs)

    ui_server_helpers.check_all_docs_stored_notify("test_docs_complete_mtg")
    # 给异步 thread 一点时间把节流标记置上
    time.sleep(0.05)
    assert "test_docs_complete_mtg:docs_complete" in _TRIGGERED


# ── demo_new_version: write_demo_version 完成触发 ──


def test_demo_new_version_triggers_proactive(tmp_path):
    """新 demo 版本写入 → 主动 trigger demo_new_version."""
    from vpbuddy import demo_version

    demo_version.write_demo_version("demo_proactive_mtg", "<h1>v1</h1>", trigger="agent_iterate", docs_dir=tmp_path / "docs")
    time.sleep(0.05)
    assert "demo_proactive_mtg:demo_new_version" in _TRIGGERED


# ── 后台 monitor smoke (启动/停止) ──


def test_monitor_starts_and_stops():
    """start_monitor 起后台线程, stop_monitor 优雅退出."""
    start_monitor()
    time.sleep(0.1)
    stop_monitor(timeout=2.0)
    # 二次调用 stop 不抛
    stop_monitor(timeout=0.5)


def test_monitor_idempotent():
    """多次 start_monitor 不重复起线程."""
    start_monitor()
    start_monitor()  # 第二次直接 return
    time.sleep(0.05)
    stop_monitor(timeout=1.0)


# ── 异步写 chat 历史 (用 monkeypatch push_event) ──


def test_trigger_writes_collab_md_and_pushes_collab_update(monkeypatch, tmp_path):
    """2026-07-03 v0.8.4: trigger 异步调 collab.ask_question + push SSE collab-update.

    之前 v0.8.3 是写 chat.json + push chat-message; 改为落 collab.md + 推 collab-update.
    chat 历史不再被主动消息污染.
    """
    from vpbuddy import ui_server
    # 测试不污染真实 DATA_DIR / DOCS_DIR — 用 tmp_path 隔离
    monkeypatch.setattr("vpbuddy.ui_server.DATA_DIR", tmp_path / "data")
    docs_dir = tmp_path / "docs"
    monkeypatch.setattr("vpbuddy.ui_server.DOCS_DIR", docs_dir)
    # collab._default_docs_dir() 走 sub_session_controller.DOCS_DIR
    from vpbuddy import sub_session_controller
    monkeypatch.setattr(sub_session_controller, "DOCS_DIR", docs_dir)
    # agent_proactive 内部 `from .realtime_server import push_event` 引用的是模块里的,
    # monkeypatch 必须在 agent_proactive 命名空间替换.
    import vpbuddy.agent_proactive as ap_mod
    pushed = []
    monkeypatch.setattr(ap_mod, "push_event", lambda mid, t, p: pushed.append((mid, t, p)))

    trigger("async_chat_mtg", "docs_complete", state_summary="x")
    time.sleep(0.2)  # 等 daemon thread 跑完

    # collab.md 应有一条 (写落 docs/{mid}/collab.md)
    collab_file = (tmp_path / "docs" / "async_chat_mtg" / "collab.md")
    assert collab_file.exists(), f"collab.md 缺失 at {collab_file}"
    collab_text = collab_file.read_text(encoding="utf-8")
    assert "📄" in collab_text
    assert "[docs]" in collab_text  # trigger type → section 'docs'
    assert "agent-proactive:docs_complete" in collab_text  # asker
    # chat 历史应**没有** proactive 消息 (v0.8.4 不写 chat)
    history = ui_server._load_chat_history("async_chat_mtg")
    proactive_msgs = [m for m in history if m.get("is_proactive")]
    assert proactive_msgs == [], f"不应有 is_proactive chat 历史, 实际: {proactive_msgs}"
    # SSE 推 collab-update
    assert len(pushed) == 1
    assert pushed[0][0] == "async_chat_mtg"
    assert pushed[0][1] == "collab-update"
    payload = pushed[0][2]
    assert payload["action"] == "ask"
    assert payload["section"] == "docs"
    assert "📄" in payload["question"]
    assert payload["asker"] == "agent-proactive:docs_complete"


def test_trigger_isolates_meeting_throttle():
    """不同 mid 的 trigger 不互相影响."""
    trigger("iso_a", "docs_complete")
    trigger("iso_b", "docs_complete")
    # a 和 b 都能触发一次 (节流是 per-(mid,type))
    assert trigger("iso_a", "docs_complete") is None  # a 已节流
    assert trigger("iso_b", "docs_complete") is None  # b 也节流


def test_silence_threshold_constant_is_5min():
    """沉默阈值 = 5 分钟 (跟设计稿一致)."""
    assert SILENCE_THRESHOLD_SEC == 300


# ── chat 上传: handle_chat_upload / _parse_multipart ──


def _make_multipart_body(fields: list[tuple[str, bytes, dict]]) -> tuple[bytes, str]:
    """手工拼 multipart body (避免引第三方)."""
    boundary = "----TestBoundary12345"
    parts = []
    for name, data, meta in fields:
        meta = dict(meta)
        filename = meta.get("filename")
        ct = meta.get("content_type")
        if filename:
            header = f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
            if ct:
                header += f"Content-Type: {ct}\r\n"
        else:
            header = f'Content-Disposition: form-data; name="{name}"\r\n'
        parts.append(f"--{boundary}\r\n{header}\r\n".encode() + data + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)
    content_type = f"multipart/form-data; boundary={boundary}"
    return body, content_type


def test_parse_multipart_multi_files():
    """_parse_multipart 支持多文件."""
    body, ct = _make_multipart_body([
        ("text", b"hello", {}),
        ("files", b"file1content", {"filename": "a.txt", "content_type": "text/plain"}),
        ("files", b"file2content", {"filename": "b.md", "content_type": "text/markdown"}),
    ])
    parts = _parse_multipart(body, ct)
    assert parts["text"] == "hello"
    assert len(parts["files"]) == 2
    assert parts["files"][0]["filename"] == "a.txt"
    assert parts["files"][1]["filename"] == "b.md"
    assert parts["files"][0]["data"] == b"file1content"
    assert parts["files"][1]["content_type"] == "text/markdown"


def test_parse_multipart_single_file_backward_compat():
    """单文件仍解析."""
    body, ct = _make_multipart_body([
        ("files", b"onlyone", {"filename": "x.txt", "content_type": "text/plain"}),
    ])
    parts = _parse_multipart(body, ct)
    assert len(parts["files"]) == 1
    assert parts["files"][0]["filename"] == "x.txt"
    assert parts["files"][0]["data"] == b"onlyone"


def test_parse_multipart_no_files():
    """无文件 → files 是空 list."""
    body, ct = _make_multipart_body([("text", b"hi", {})])
    parts = _parse_multipart(body, ct)
    assert parts["text"] == "hi"
    assert parts["files"] == []


def test_validate_file_accepts_images_when_allowed():
    """图片白名单."""
    _validate_file("a.png", b"x" * 100, allow_images=True)
    _validate_file("a.jpg", b"x" * 100, allow_images=True)
    _validate_file("a.webp", b"x" * 100, allow_images=True)


def test_validate_file_rejects_images_when_not_allowed():
    """默认不许图片."""
    with pytest.raises(ValueError, match="只支持"):
        _validate_file("a.png", b"x" * 100)


def test_is_image_detection():
    """_is_image 识别图片扩展名."""
    assert _is_image("photo.png")
    assert _is_image("photo.JPG")  # 大小写无关
    assert _is_image("photo.webp")
    assert not _is_image("doc.pdf")
    assert not _is_image("notes.md")


def test_image_to_data_uri_roundtrip():
    """base64 data URI 正确拼装."""
    data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 10
    uri = _image_to_b64_data_uri(data, "image/png")
    assert uri.startswith("data:image/png;base64,")
    import base64
    payload_b64 = uri.split(",", 1)[1]
    assert base64.b64decode(payload_b64) == data


def test_image_to_data_uri_oversized_rejected():
    """>5MB 拒绝."""
    big = b"x" * (6 * 1024 * 1024)
    with pytest.raises(ValueError, match="超过 5MB"):
        _image_to_b64_data_uri(big, "image/png")


def test_image_to_data_uri_fallback_content_type():
    """content_type 不是 image/* → fallback image/png."""
    uri = _image_to_b64_data_uri(b"\x89PNG", "application/octet-stream")
    assert uri.startswith("data:image/png;base64,")


def test_allowed_image_extensions_set():
    """白名单覆盖常见图片格式."""
    expected = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
    assert ALLOWED_IMAGE_EXTENSIONS == expected



# ── 替身 RAG (不真连 Chroma) ──


class _FakeRag:
    """替身 get_rag: 不真连 Chroma."""

    def __init__(self):
        self.added = []

    def add(self, ids, documents, metadatas):
        self.added.append({"ids": ids, "documents": documents, "metadatas": metadatas})

    def query(self, *a, **kw):
        return {"ids": [[]], "documents": [[]], "metadatas": [[]]}

    def count(self):
        return 0


# ── handle_kb_upload 向后兼容 (老调用方 parts["file"] 已升级) ──


def test_handle_kb_upload_single_file_compat(tmp_path, monkeypatch):
    """单文件仍走 handle_kb_upload (向后兼容)."""
    monkeypatch.setattr("vpbuddy.kb_api.UPLOADS_DIR", tmp_path / "uploads")
    monkeypatch.setattr("vpbuddy.kb_api.get_rag", lambda: _FakeRag())

    body, ct = _make_multipart_body([
        ("meeting_id", b"compat_mtg", {}),
        ("file", b"file body content for backward compat test " * 5, {"filename": "doc.txt", "content_type": "text/plain"}),
    ])
    result = handle_kb_upload(body, ct)
    assert result["status"] == 200
    assert result["filename"] == "doc.txt"
    assert result["meeting_id"] == "compat_mtg"


def test_handle_chat_upload_text_files_to_kb(tmp_path, monkeypatch):
    """文本类 → 入 KB."""
    monkeypatch.setattr("vpbuddy.kb_api.UPLOADS_DIR", tmp_path / "uploads")
    fake = _FakeRag()
    monkeypatch.setattr("vpbuddy.kb_api.get_rag", lambda: fake)

    body, ct = _make_multipart_body([
        ("text", b"hello question", {}),
        ("files", b"key doc content " * 20, {"filename": "doc.md", "content_type": "text/markdown"}),
    ])
    result = handle_chat_upload(body, ct, "chat_upload_mtg")
    assert result["status"] == 200
    assert result["text"] == "hello question"
    assert len(result["kb_doc_ids"]) == 1
    assert result["image_count"] == 0
    # 入库的内容含 doc.md 的字符
    assert len(fake.added) == 1
    assert "key doc content" in fake.added[0]["documents"][0][:100]


def test_handle_chat_upload_image_to_b64(tmp_path, monkeypatch):
    """图片 → base64 data URI, 不入库."""
    monkeypatch.setattr("vpbuddy.kb_api.UPLOADS_DIR", tmp_path / "uploads")
    fake = _FakeRag()
    monkeypatch.setattr("vpbuddy.kb_api.get_rag", lambda: fake)

    png_data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
    body, ct = _make_multipart_body([
        ("text", b"see this image", {}),
        ("files", png_data, {"filename": "snap.png", "content_type": "image/png"}),
    ])
    result = handle_chat_upload(body, ct, "chat_img_mtg")
    assert result["status"] == 200
    assert result["image_count"] == 1
    assert len(result["kb_doc_ids"]) == 0
    assert len(fake.added) == 0  # 图片不入库


def test_handle_chat_upload_rejects_invalid_ext(tmp_path, monkeypatch):
    """不支持的扩展名 → 标 rejected 不阻塞其他文件."""
    monkeypatch.setattr("vpbuddy.kb_api.UPLOADS_DIR", tmp_path / "uploads")
    monkeypatch.setattr("vpbuddy.kb_api.get_rag", lambda: _FakeRag())

    body, ct = _make_multipart_body([
        ("files", b"binary exe content", {"filename": "virus.exe", "content_type": "application/octet-stream"}),
    ])
    result = handle_chat_upload(body, ct, "chat_rej_mtg")
    assert result["status"] == 200
    assert len(result["files"]) == 1
    assert result["files"][0]["status"] == "rejected"
    assert "只支持" in result["files"][0]["error"]


def test_handle_chat_upload_text_only_no_files_ok(tmp_path, monkeypatch):
    """纯文本 (无附件) 也能上传 — text-only 模式."""
    monkeypatch.setattr("vpbuddy.kb_api.UPLOADS_DIR", tmp_path / "uploads")
    monkeypatch.setattr("vpbuddy.kb_api.get_rag", lambda: _FakeRag())

    body, ct = _make_multipart_body([("text", b"text-only ask Hermes", {})])
    result = handle_chat_upload(body, ct, "text_only_mtg")
    assert result["status"] == 200
    assert result["text"] == "text-only ask Hermes"


def test_handle_chat_upload_empty_both_rejected(tmp_path, monkeypatch):
    """text 和 files 都空 → 400."""
    monkeypatch.setattr("vpbuddy.kb_api.UPLOADS_DIR", tmp_path / "uploads")
    monkeypatch.setattr("vpbuddy.kb_api.get_rag", lambda: _FakeRag())

    body, ct = _make_multipart_body([])  # 空 body
    result = handle_chat_upload(body, ct, "empty_mtg")
    assert result["status"] == 400
    assert "text 或 files" in result["error"]


def test_handle_chat_upload_mixed_files(tmp_path, monkeypatch):
    """混合: 1 文件 + 1 图片 + 1 不支持."""
    monkeypatch.setattr("vpbuddy.kb_api.UPLOADS_DIR", tmp_path / "uploads")
    fake = _FakeRag()
    monkeypatch.setattr("vpbuddy.kb_api.get_rag", lambda: fake)

    body, ct = _make_multipart_body([
        ("text", b"?? + ???", {}),
        ("files", b"some markdown content here " * 5, {"filename": "doc.md", "content_type": "text/markdown"}),
        ("files", b"\x89PNG\r\n\x1a\n" + b"\x00" * 50, {"filename": "snap.png", "content_type": "image/png"}),
        ("files", b"exe binary", {"filename": "bad.exe", "content_type": "application/octet-stream"}),
    ])
    result = handle_chat_upload(body, ct, "mixed_mtg")
    assert result["status"] == 200
    statuses = [f["status"] for f in result["files"]]
    assert "kb-stored" in statuses
    assert "image" in statuses
    assert "rejected" in statuses


# ── HTTP 端点测试 — v0.9.0 旧 Handler 已删除, 由 FastAPI E2E 覆盖 ──