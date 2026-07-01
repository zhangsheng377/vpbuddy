"""测试 batch_docs 合并 + 6→2 kinds 调度 (ADR-0029 Commit 3).

覆盖:
- render_batch_prompt: 5 文档路径 + 上次内容 + state_summary 注入
- get_batch_doc_paths: 5 个文件路径
- trigger_batch_docs dry_run: 不调 LLM, 返 prompt
- trigger_batch_docs 直接模式: VPBUDDY_DIRECT=1 不调 LLM
- _dispatch_kind 路由: batch_docs / demo / deprecated
- run_one_round 调度: 每个会议 2 kinds (batch_docs + demo)
- prompt 渲染: 不破 brace (CSS / 模板字符串)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from vpbuddy.sub_sessions.batch_docs import (
    BATCH_DOC_KINDS,
    get_batch_doc_paths,
    render_batch_prompt,
    trigger_batch_docs,
)
from vpbuddy.sub_session_controller import (
    BATCH_DOCS_KIND,
    DEMO_KIND,
    SCHEDULED_KINDS,
    DOC_KINDS,
    _dispatch_kind,
    run_one_round,
    format_state_summary,
)
from vpbuddy.state import MeetingState


# ── get_batch_doc_paths ──


def test_get_batch_doc_paths_returns_5(tmp_path, monkeypatch):
    """返 5 个文档路径."""
    monkeypatch.setattr("vpbuddy.sub_sessions.batch_docs.DOCS_DIR", tmp_path)
    paths = get_batch_doc_paths("mtg01")
    assert set(paths.keys()) == {"req", "arch", "tasks", "api", "risk"}
    for kind, p in paths.items():
        assert p == tmp_path / "mtg01" / f"{kind}.md"


def test_batch_doc_kinds_constant():
    """BATCH_DOC_KINDS 包含 5 个文档 (不含 demo)."""
    assert set(BATCH_DOC_KINDS) == {"req", "arch", "tasks", "api", "risk"}
    assert "demo" not in BATCH_DOC_KINDS
    assert len(BATCH_DOC_KINDS) == 5


# ── render_batch_prompt ──


def test_render_batch_prompt_injects_paths(tmp_path):
    """5 文档路径注入到 prompt."""
    state = MeetingState(meeting_id="rp_mtg")
    state_summary = format_state_summary(state)
    last_docs: dict = {kind: None for kind in BATCH_DOC_KINDS}

    prompt = render_batch_prompt("rp_mtg", state_summary, last_docs, docs_dir=tmp_path)

    paths = get_batch_doc_paths("rp_mtg", tmp_path)
    for kind, p in paths.items():
        assert str(p) in prompt, f"path {p} not in prompt"
        assert f"doc_path_{kind}" not in prompt  # 占位符已替换


def test_render_batch_prompt_includes_last_docs(tmp_path):
    """上次文档内容注入到 prompt."""
    state_summary = format_state_summary(MeetingState(meeting_id="ld_mtg"))
    last_docs = {
        "req": "# 需求清单\n- REQ-001 ...",
        "arch": "# 架构\n## 模块",
        "tasks": None,  # 首次创建
        "api": "# API",
        "risk": "# 风险",
    }

    prompt = render_batch_prompt("ld_mtg", state_summary, last_docs)

    # req.md 上次输出应注入
    assert "REQ-001" in prompt
    # 首次创建标记
    assert "(首次创建 — 空)" in prompt
    # arch 内容
    assert "## 模块" in prompt


def test_render_batch_prompt_includes_state_summary(tmp_path):
    """state_summary 注入."""
    state = MeetingState(meeting_id="ss_mtg")
    summary = format_state_summary(state)
    last_docs: dict = {k: None for k in BATCH_DOC_KINDS}
    prompt = render_batch_prompt("ss_mtg", summary, last_docs, docs_dir=tmp_path)
    assert "会议 ss_mtg 累积摘要" in prompt


def test_render_batch_prompt_preserves_braces_in_content(tmp_path):
    """prompt 含 CSS { } 不破 .format()."""
    # 在 last_docs 里塞 CSS 代码块 (含 { })
    last_docs = {
        "req": "```css\nbody { font-family: sans-serif; }\n```",
        "arch": "模板字符串 `hello {name}` 不破",
        "tasks": None,
        "api": None,
        "risk": None,
    }
    summary = format_state_summary(MeetingState(meeting_id="br_mtg"))
    # 不抛 KeyError 即 OK
    prompt = render_batch_prompt("br_mtg", summary, last_docs)
    assert "br_mtg" in prompt


def test_render_batch_prompt_contains_collab_protocol(tmp_path):
    """prompt 含 collab.md 协作协议段 (ADR-0028)."""
    summary = format_state_summary(MeetingState(meeting_id="cp_mtg"))
    last_docs: dict = {k: None for k in BATCH_DOC_KINDS}
    prompt = render_batch_prompt("cp_mtg", summary, last_docs, docs_dir=tmp_path)
    assert "collab.md" in prompt
    assert "ask_question" in prompt
    assert "list_pending" in prompt


# ── trigger_batch_docs dry_run ──


def test_trigger_batch_docs_dry_run(tmp_path, monkeypatch):
    """dry_run=True 不调 LLM, 返 prompt."""
    monkeypatch.setattr("vpbuddy.sub_sessions.batch_docs.DATA_DIR", tmp_path)
    monkeypatch.setattr("vpbuddy.sub_sessions.batch_docs.DOCS_DIR", tmp_path)
    # state.json 必须存在 (MeetingStorage 加载)
    state_path = tmp_path / "dryrun_mtg.json"
    state_path.write_text(json.dumps({"meeting_id": "dryrun_mtg", "platform": "local"}))

    result = trigger_batch_docs("dryrun_mtg", dry_run=True)
    assert result["dry_run"] is True
    assert "prompt" in result
    assert "dryrun_mtg" in result["prompt"]
    assert result["triggered"] is False


# ── trigger_batch_docs DIRECT mode ──


def test_trigger_batch_docs_direct_mode(tmp_path, monkeypatch):
    """VPBUDDY_DIRECT=1 不调 LLM, 返 triggered=True, agent_path=direct."""
    monkeypatch.setattr("vpbuddy.sub_sessions.batch_docs.DATA_DIR", tmp_path)
    monkeypatch.setattr("vpbuddy.sub_sessions.batch_docs.DOCS_DIR", tmp_path)
    state_path = tmp_path / "direct_mtg.json"
    state_path.write_text(json.dumps({"meeting_id": "direct_mtg", "platform": "local"}))
    monkeypatch.setenv("VPBUDDY_DIRECT", "1")

    result = trigger_batch_docs("direct_mtg")
    assert result["triggered"] is True
    assert result["agent_path"] == "direct"
    assert all(not f["written"] for f in result["files"].values())  # 不真写


# ── trigger_batch_docs 无 AIAgent ──


def test_trigger_batch_docs_no_agent(tmp_path, monkeypatch):
    """AIAgent 不可用 → 返 error."""
    monkeypatch.setattr("vpbuddy.sub_sessions.batch_docs.DATA_DIR", tmp_path)
    monkeypatch.setattr("vpbuddy.sub_sessions.batch_docs.DOCS_DIR", tmp_path)
    monkeypatch.setattr("vpbuddy.sub_sessions.batch_docs._AGENT_AVAILABLE", False)
    state_path = tmp_path / "noagent_mtg.json"
    state_path.write_text(json.dumps({"meeting_id": "noagent_mtg", "platform": "local"}))
    monkeypatch.delenv("VPBUDDY_DIRECT", raising=False)

    result = trigger_batch_docs("noagent_mtg")
    assert result["triggered"] is False
    assert "AIAgent not available" in result["error"]


# ── _dispatch_kind 路由 ──


def test_dispatch_kind_batch_docs(tmp_path, monkeypatch):
    """kind=batch_docs 路由到 trigger_batch_docs."""
    monkeypatch.setattr("vpbuddy.sub_sessions.batch_docs.DATA_DIR", tmp_path)
    monkeypatch.setattr("vpbuddy.sub_sessions.batch_docs.DOCS_DIR", tmp_path)
    state_path = tmp_path / "dispatch_batch.json"
    state_path.write_text(json.dumps({"meeting_id": "dispatch_batch", "platform": "local"}))
    monkeypatch.setattr("vpbuddy.sub_session_controller._AGENT_AVAILABLE", False)
    monkeypatch.delenv("VPBUDDY_DIRECT", raising=False)

    result = _dispatch_kind("dispatch_batch", BATCH_DOCS_KIND)
    assert result["agent_path"] == "in-process" or "AIAgent not available" in str(result.get("error", ""))


def test_dispatch_kind_deprecated(tmp_path, monkeypatch):
    """kind=req 返 deprecated 警告."""
    for kind in ["req", "arch", "tasks", "api", "risk"]:
        result = _dispatch_kind("any_mtg", kind)
        assert result.get("deprecated") is True
        assert "batch_docs" in result["error"]
        assert result["triggered"] is False


def test_dispatch_kind_demo_route_exists(tmp_path, monkeypatch):
    """kind=demo 路由到 trigger_sub_session (老路径)."""
    monkeypatch.setattr("vpbuddy.sub_sessions.batch_docs.DATA_DIR", tmp_path)
    monkeypatch.setattr("vpbuddy.sub_sessions.batch_docs.DOCS_DIR", tmp_path)
    # demo 走老 trigger_sub_session, 需要 demo 子 session 可用
    # 简单测: 至少不抛 + session_id 包含 demo
    state_path = tmp_path / "demo_route.json"
    state_path.write_text(json.dumps({"meeting_id": "demo_route", "platform": "local"}))
    # dry_run=True 让 demo 也只渲染 prompt 不调 LLM
    monkeypatch.setattr("vpbuddy.sub_session_controller._AGENT_AVAILABLE", False)
    result = _dispatch_kind("demo_route", DEMO_KIND, dry_run=True)
    assert "demo" in result["session_id"]


# ── run_one_round 调度 (2 kinds) ──


def test_scheduled_kinds_count():
    """SCHEDULED_KINDS = 2 (batch_docs + demo)."""
    assert len(SCHEDULED_KINDS) == 2
    assert BATCH_DOCS_KIND in SCHEDULED_KINDS
    assert DEMO_KIND in SCHEDULED_KINDS


def test_run_one_round_uses_2_kinds_per_meeting(tmp_path, monkeypatch, capsys):
    """run_one_round 每个会议 2 kinds (1 batch_docs + 1 demo)."""
    monkeypatch.setattr("vpbuddy.sub_session_controller.DATA_DIR", tmp_path)
    monkeypatch.setattr("vpbuddy.sub_sessions.batch_docs.DATA_DIR", tmp_path)
    monkeypatch.setattr("vpbuddy.sub_sessions.batch_docs.DOCS_DIR", tmp_path)
    monkeypatch.setattr("vpbuddy.sub_session_controller._AGENT_AVAILABLE", False)
    monkeypatch.delenv("VPBUDDY_DIRECT", raising=False)

    # 3 个 active meeting
    for i in range(3):
        (tmp_path / f"meet{i}.json").write_text(json.dumps({
            "meeting_id": f"meet{i}", "platform": "local"
        }))

    results = run_one_round(meeting_ids=["meet0", "meet1", "meet2"], dry_run=True, parallel=False)
    # 3 meetings × 2 kinds = 6 tasks
    assert len(results) == 6

    # 日志应说明 2 kinds × 3 meetings
    captured = capsys.readouterr()
    assert "2 kinds" in captured.out
    assert "3 meetings" in captured.out


def test_run_one_round_parallel_default(tmp_path, monkeypatch):
    """默认 parallel=True (ThreadPoolExecutor)."""
    monkeypatch.setattr("vpbuddy.sub_session_controller.DATA_DIR", tmp_path)
    monkeypatch.setattr("vpbuddy.sub_sessions.batch_docs.DATA_DIR", tmp_path)
    monkeypatch.setattr("vpbuddy.sub_sessions.batch_docs.DOCS_DIR", tmp_path)
    monkeypatch.setattr("vpbuddy.sub_session_controller._AGENT_AVAILABLE", False)
    monkeypatch.delenv("VPBUDDY_DIRECT", raising=False)
    (tmp_path / "single_mtg.json").write_text(json.dumps({"meeting_id": "single_mtg", "platform": "local"}))

    results = run_one_round(meeting_ids=["single_mtg"], dry_run=True)
    assert len(results) == 2  # batch_docs + demo


# ── 老 API 兼容 ──


def test_old_doc_kinds_constant_still_has_6():
    """老 DOC_KINDS 6 个保留 (向后兼容老 import)."""
    assert len(DOC_KINDS) == 6
    assert set(DOC_KINDS) == {"req", "arch", "tasks", "api", "risk", "demo"}


# ── prompt 模板完整性 ──


def test_prompt_template_exists():
    """prompts/batch_docs.md 存在且含必要段."""
    from vpbuddy.sub_session_controller import PROMPTS_DIR
    template = (PROMPTS_DIR / "batch_docs.md").read_text(encoding="utf-8")
    # 必要段
    assert "协作提问协议" in template
    assert "ADR-0028" in template
    assert "ask_question" in template
    assert "list_pending" in template
    assert "collab.md" in template
    # 5 文档路径占位符
    for kind in BATCH_DOC_KINDS:
        assert f"{{doc_path_{kind}}}" in template
    # 数据隔离铁律
    assert "数据隔离" in template
    assert "VPBuddy" in template and "不知道" in template


def test_demo_prompt_has_collab_protocol():
    """prompts/demo.md 加了 ADR-0028 协议段."""
    from vpbuddy.sub_session_controller import PROMPTS_DIR
    template = (PROMPTS_DIR / "demo.md").read_text(encoding="utf-8")
    assert "协作提问协议" in template
    assert "ADR-0028" in template
    assert "ask_question" in template
    assert "section=" in template  # demo section