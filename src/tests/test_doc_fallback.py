"""测试 doc_fallback:代码生成 docs 不依赖 LLM

背景(2026-06-22):MiniMax-M3 工具调用弱,fallback 必须工作。
"""
import json
import pytest
from pathlib import Path
from vpbuddy.doc_fallback import (
    generate_doc,
    generate_and_write,
    _GENERATORS,
)


SAMPLE_STATE = {
    "meeting_id": "TEST_MEETING",
    "title": "测试会议:音频采集方案",
    "created_at": "2026-06-22T18:00:00",
    "platform": "local",
    "facts": {
        "REQ": ["loopback 音频采集", "支持三个会议平台", "本地 SQLite 存储"],
        "GOAL": ["完全本地运行"],
        "FEAT": ["Web UI 8765"],
        "RISK": ["冷启动 256MB", "检索质量"],
        "QUE": ["法务确认隐私政策"],
    },
}


class TestDocFallback:
    def test_all_six_kinds_have_generators(self):
        """6 种 doc_kind 都有生成器"""
        assert set(_GENERATORS.keys()) == {"req", "arch", "tasks", "api", "risk", "demo"}

    def test_generate_doc_unknown_kind_raises(self):
        """未知 doc_kind → ValueError"""
        with pytest.raises(ValueError, match="Unknown doc_kind"):
            generate_doc("TEST", "invalid_kind", SAMPLE_STATE)

    def test_req_doc_contains_all_reqs(self):
        """req 文档包含所有 REQ"""
        content = generate_doc("TEST", "req", SAMPLE_STATE)
        for r in SAMPLE_STATE["facts"]["REQ"]:
            assert r in content, f"missing REQ: {r}"
        assert "# 需求清单" in content
        assert "REQ-001" in content
        assert "REQ-002" in content

    def test_risk_doc_contains_all_risks(self):
        """risk 文档包含所有 RISK"""
        content = generate_doc("TEST", "risk", SAMPLE_STATE)
        for r in SAMPLE_STATE["facts"]["RISK"]:
            assert r in content, f"missing RISK: {r}"
        assert "R-001" in content
        assert "风险评估" in content

    def test_api_doc_has_openapi_structure(self):
        """api 文档有 OpenAPI 风格 endpoint"""
        content = generate_doc("TEST", "api", SAMPLE_STATE)
        assert "POST /v1/meetings" in content
        assert "GET /v1/kb/search" in content
        assert "错误码" in content

    def test_arch_doc_has_components(self):
        """arch 文档有总体架构 + 数据流 + 关键决策"""
        content = generate_doc("TEST", "arch", SAMPLE_STATE)
        assert "总体架构" in content
        assert "数据流" in content
        assert "关键决策" in content
        assert "模块划分" in content

    def test_tasks_doc_has_one_task_per_req(self):
        """tasks 文档 T-001..N 对应每个 REQ"""
        content = generate_doc("TEST", "tasks", SAMPLE_STATE)
        n_reqs = len(SAMPLE_STATE["facts"]["REQ"])
        assert f"T-{n_reqs:03d}" in content
        assert "T-001" in content
        for r in SAMPLE_STATE["facts"]["REQ"]:
            assert r in content, f"missing task for: {r}"

    def test_demo_doc_is_valid_html(self):
        """demo 文档是合法 HTML"""
        content = generate_doc("TEST", "demo", SAMPLE_STATE)
        assert content.startswith("<!DOCTYPE html>")
        assert "</html>" in content
        assert "VPBuddy" in content
        for r in SAMPLE_STATE["facts"]["REQ"]:
            assert r in content, f"missing REQ in demo: {r}"

    def test_generate_and_write_creates_file(self, tmp_path):
        """generate_and_write 写盘成功"""
        doc_path = tmp_path / "docs" / "TEST_MEETING" / "req.md"
        result = generate_and_write("TEST_MEETING", "req", SAMPLE_STATE, doc_path)
        assert result == doc_path
        assert doc_path.exists()
        content = doc_path.read_text(encoding="utf-8")
        assert "REQ-001" in content
        assert content == generate_doc("TEST_MEETING", "req", SAMPLE_STATE)

    def test_generate_and_write_creates_parent_dirs(self, tmp_path):
        """generate_and_write 自动建父目录"""
        doc_path = tmp_path / "deep" / "nested" / "path" / "req.md"
        assert not doc_path.parent.exists()
        generate_and_write("M", "req", SAMPLE_STATE, doc_path)
        assert doc_path.exists()

    def test_empty_facts_produces_valid_doc(self):
        """空 facts 也能生成合法文档(不崩)"""
        empty = {"meeting_id": "EMPTY", "title": "空会议", "created_at": "2026-06-22", "facts": {}}
        for kind in _GENERATORS:
            content = generate_doc("EMPTY", kind, empty)
            assert isinstance(content, str) and len(content) > 0, f"{kind} 返空字符串"
