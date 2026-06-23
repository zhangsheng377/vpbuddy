"""Tests for cleanup_inactive_agents (2026-06-24, ADR-0016 落地 + 内存泄漏修复)

覆盖场景:
- 活跃会议(state.json 刚更新)→ 不清理
- 不活跃会议(state.json mtime 旧)→ 清理 6 个 doc_kind AIAgent
- state.json 不存在(被人工删)→ 清理
- 多个会议混合
- dry_run=True → 不真清
- 自动 cleanup_inactive_agents 不抛异常(主循环依赖)
"""
import json
import time
from pathlib import Path

import pytest

from vpbuddy import sub_session_controller as ssc
from vpbuddy.sub_session_controller import (
    DOC_KINDS,
    _AGENT_CACHE,
    _agent_session_id,
    cleanup_inactive_agents,
)


@pytest.fixture(autouse=True)
def clear_cache():
    """每个测试清空 cache, 防相互污染"""
    _AGENT_CACHE.clear()
    yield
    _AGENT_CACHE.clear()


@pytest.fixture
def tmp_data_dir(tmp_path, monkeypatch):
    """替换 DATA_DIR 路径到 tmp_path (需要同时改 module 全局)"""
    monkeypatch.setattr(ssc, "DATA_DIR", tmp_path)
    return tmp_path


def _fake_aiagent(mid: str, kind: str):
    """假 AIAgent 对象 — 不调真的 run_agent.AIAgent, 用一个 sentinel"""
    return {"session_id": f"meeting:{mid}:{kind}", "fake": True}


def test_cleanup_no_inactive_meetings(tmp_data_dir):
    """空 cache → 没清"""
    result = cleanup_inactive_agents(inactive_minutes=30, dry_run=False)
    assert result["cleaned"] == []
    assert result["kept_active"] == []
    assert result["cache_size_before"] == 0
    assert result["cache_size_after"] == 0


def test_cleanup_keeps_active_meetings(tmp_data_dir):
    """刚更新的会议 → 保留"""
    for kind in DOC_KINDS:
        sid = _agent_session_id("ACTIVE_001", kind)
        _AGENT_CACHE[sid] = _fake_aiagent("ACTIVE_001", kind)

    state_file = tmp_data_dir / "ACTIVE_001.json"
    state_file.write_text(json.dumps({"meeting_id": "ACTIVE_001"}))

    result = cleanup_inactive_agents(inactive_minutes=30, dry_run=False)
    assert result["cleaned"] == []
    assert "ACTIVE_001" in result["kept_active"]
    assert result["cache_size_after"] == 6


def test_cleanup_removes_inactive_meetings(tmp_data_dir):
    """30 分钟前更新的会议 → 清理 6 个 doc_kind"""
    for kind in DOC_KINDS:
        sid = _agent_session_id("OLD_MEETING", kind)
        _AGENT_CACHE[sid] = _fake_aiagent("OLD_MEETING", kind)

    state_file = tmp_data_dir / "OLD_MEETING.json"
    state_file.write_text(json.dumps({"meeting_id": "OLD_MEETING"}))

    old_time = time.time() - (31 * 60)
    import os
    os.utime(state_file, (old_time, old_time))

    result = cleanup_inactive_agents(inactive_minutes=30, dry_run=False)
    assert "OLD_MEETING" in result["cleaned"]
    assert result["cache_size_after"] == 0


def test_cleanup_removes_when_state_missing(tmp_data_dir):
    """state.json 被人工删 → 该会议 cache 也清"""
    for kind in DOC_KINDS:
        sid = _agent_session_id("GONE_MID", kind)
        _AGENT_CACHE[sid] = _fake_aiagent("GONE_MID", kind)

    result = cleanup_inactive_agents(inactive_minutes=30, dry_run=False)
    assert "GONE_MID" in result["cleaned"]
    assert result["cache_size_after"] == 0


def test_cleanup_dry_run(tmp_data_dir):
    """dry_run=True → 统计但不清"""
    for kind in DOC_KINDS:
        sid = _agent_session_id("DRY_MID", kind)
        _AGENT_CACHE[sid] = _fake_aiagent("DRY_MID", kind)
    state_file = tmp_data_dir / "DRY_MID.json"
    state_file.write_text(json.dumps({}))
    old_time = time.time() - 999 * 60
    import os
    os.utime(state_file, (old_time, old_time))

    result = cleanup_inactive_agents(inactive_minutes=30, dry_run=True)
    assert "DRY_MID" in result["cleaned"]  # 会报告为 cleaned
    assert result["cache_size_after"] == 6  # 但实际没清


def test_cleanup_mixed(tmp_data_dir):
    """混合场景: 1 活跃 + 1 不活跃"""
    for kind in DOC_KINDS:
        _AGENT_CACHE[_agent_session_id("ACTIVE", kind)] = _fake_aiagent("ACTIVE", kind)
        _AGENT_CACHE[_agent_session_id("OLD", kind)] = _fake_aiagent("OLD", kind)

    (tmp_data_dir / "ACTIVE.json").write_text(json.dumps({}))  # mtime = now
    (tmp_data_dir / "OLD.json").write_text(json.dumps({}))
    old_time = time.time() - 60 * 60  # 1 小时前
    import os
    os.utime(tmp_data_dir / "OLD.json", (old_time, old_time))

    result = cleanup_inactive_agents(inactive_minutes=30, dry_run=False)
    assert "ACTIVE" in result["kept_active"]
    assert "OLD" in result["cleaned"]
    assert result["cache_size_after"] == 6  # ACTIVE 的 6 个保留


def test_cleanup_does_not_throw_on_weird_cache_keys(tmp_path, monkeypatch):
    """清理时如果 _AGENT_CACHE 有奇形怪状的 key (非 meeting: 开头), 不应抛"""
    _AGENT_CACHE["weird_key"] = object()
    _AGENT_CACHE["meeting:NORMAL:req"] = _fake_aiagent("NORMAL", "req")

    monkeypatch.setattr(ssc, "DATA_DIR", tmp_path)

    try:
        result = cleanup_inactive_agents(inactive_minutes=30, dry_run=False)
        # weird_key 因为不是 meeting: 开头, 不会被处理
        assert "weird_key" in _AGENT_CACHE
        # NORMAL 因为 state 不存在会被清
        assert result["cache_size_after"] == 1  # 只剩 weird_key
    except Exception as e:
        pytest.fail(f"cleanup 抛异常: {e}")


def test_agent_session_id_format():
    """session_id 格式 = meeting:{mid}:{kind}"""
    assert _agent_session_id("ABC", "req") == "meeting:ABC:req"
    assert _agent_session_id("XYZ_123", "demo") == "meeting:XYZ_123:demo"