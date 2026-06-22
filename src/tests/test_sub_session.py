"""VPBuddy sub-session controller tests

测试目标:
1. list_active_meetings 能找到 meetings
2. format_state_summary 正确格式化累积
3. render_prompt 正确合并模板
4. trigger_sub_session(dry_run=True) 不调 hermes
5. 完整流程(单元 + dry-run)

不测(需要真实 LLM):
- 实际 hermes 触发(超时+成本)
- LLM 实际写文件(质量)
"""
import os
import sys
import tempfile
from pathlib import Path

import pytest

# 设置测试用临时目录(避免污染真实数据)
TEST_DATA = tempfile.mkdtemp(prefix="vpbuddy_test_")
TEST_DOCS = tempfile.mkdtemp(prefix="vpbuddy_docs_test_")
os.environ["VPBUDDY_DATA_DIR"] = TEST_DATA
os.environ["VPBUDDY_DOCS_DIR"] = TEST_DOCS

# 让 vpbuddy 可 import
sys.path.insert(0, str(Path(__file__).parent.parent))

from vpbuddy.sub_session_controller import (
    DOC_KINDS,
    DOCS_DIR,
    DATA_DIR,
    PROMPTS_DIR,
    format_state_summary,
    get_doc_path,
    list_active_meetings,
    render_prompt,
    run_one_round,
    trigger_sub_session,
)
from vpbuddy.state import (
    MeetingState, Platform, Priority, ItemStatus,
    Requirement, Risk, Question,
)
from vpbuddy.storage import MeetingStorage


@pytest.fixture
def populated_meeting():
    """创建一个有累积的测试会议"""
    sid = "TEST12345ABCD"
    storage = MeetingStorage(data_dir=TEST_DATA)
    state = MeetingState(meeting_id=sid, platform=Platform.LOCAL)
    state.add_requirement("客户要 SSO 登录", Priority.HIGH)
    state.add_requirement("支持微信扫码", Priority.MEDIUM)
    state.add_risk("OAuth 服务可能限流")
    state.add_question("SSO 走哪个 IdP?")
    state.speaker_map["SPEAKER_00"] = "客户 张总"
    storage.save(state)
    yield sid
    # cleanup
    Path(TEST_DATA, f"{sid}.json").unlink(missing_ok=True)


class TestMeetingListing:
    def test_empty_no_meetings(self):
        """空目录返回空列表"""
        assert list_active_meetings() == []

    def test_finds_active_meetings(self, populated_meeting):
        """能找到创建过的会议"""
        meetings = list_active_meetings()
        assert populated_meeting in meetings
        assert len(meetings) == 1


class TestDocPath:
    def test_markdown_doc(self, populated_meeting):
        """req/arch/tasks/api/risk → .md"""
        for kind in ["req", "arch", "tasks", "api", "risk"]:
            p = get_doc_path(populated_meeting, kind)
            assert p.suffix == ".md"
            assert populated_meeting in str(p)

    def test_demo_doc(self, populated_meeting):
        """demo → demo.html 在子目录"""
        p = get_doc_path(populated_meeting, "demo")
        assert p.name == "demo.html"
        assert "demo" in str(p)


class TestFormatStateSummary:
    def test_includes_all_sections(self, populated_meeting):
        """摘要包含所有累积项"""
        state = MeetingStorage(data_dir=TEST_DATA).load(populated_meeting)
        summary = format_state_summary(state)
        assert "TEST12345ABCD" in summary
        assert "需求" in summary
        assert "SSO 登录" in summary
        assert "微信扫码" in summary
        assert "风险" in summary
        assert "OAuth 服务可能限流" in summary
        assert "开放问题" in summary
        assert "SSO 走哪个 IdP" in summary
        assert "speaker_map" in summary or "说话人" in summary
        assert "张总" in summary

    def test_no_truncation_yagni(self, populated_meeting):
        """YAGNI:不截断,全量 dump"""
        state = MeetingStorage(data_dir=TEST_DATA).load(populated_meeting)
        for i in range(50):
            state.add_requirement(f"需求 X{i}", Priority.LOW)
        summary = format_state_summary(state)
        # 50 条全在里面(只测前 3 + 最后 1)
        assert "需求 X0" in summary
        assert "需求 X49" in summary


class TestRenderPrompt:
    def test_uses_specific_template(self):
        """req/arch/tasks/api/risk/demo 有各自模板"""
        for kind in DOC_KINDS:
            p = render_prompt(kind, "MID", "## 累积", None)
            # 每种 doc_kind 模板都提到自己
            assert kind in p.lower() or kind in p
            # 都不指定具体工具名(用户的纠错)
            assert "read_file" not in p
            assert "write_file" not in p
            assert "patch" not in p or "use patch" not in p

    def test_includes_meeting_id(self):
        """prompt 包含 meeting_id(用于 session_id)"""
        p = render_prompt("demo", "MY-MEETING-123", "## 累积", None)
        assert "MY-MEETING-123" in p

    def test_includes_state_summary(self):
        """prompt 包含累积摘要"""
        p = render_prompt("req", "MID", "## 累积\n- 需求 1", None)
        assert "需求 1" in p

    def test_first_run_shows_no_previous(self):
        """首次运行,提示无历史"""
        p = render_prompt("demo", "MID", "## 累积", None)
        assert "无" in p or "首次" in p

    def test_subsequent_run_includes_previous(self):
        """非首次运行,包含上次输出"""
        p = render_prompt("req", "MID", "## 累积", "## 上次的 req 文档")
        assert "上次的 req 文档" in p


class TestTriggerDryRun:
    def test_dry_run_doesnt_invoke_hermes(self, populated_meeting):
        """dry_run=True 只渲染 prompt,不调 hermes"""
        r = trigger_sub_session(populated_meeting, "demo", dry_run=True)
        assert r["triggered"] is False
        assert r["dry_run"] is True
        assert r["error"] is None
        assert "prompt" in r
        assert len(r["prompt"]) > 100
        assert populated_meeting in r["prompt"]

    def test_dry_run_creates_no_files(self, populated_meeting):
        """dry_run 不应该创建任何文件"""
        trigger_sub_session(populated_meeting, "demo", dry_run=True)
        # 文档目录应该还是空的
        docs = list(Path(TEST_DOCS).rglob("*"))
        # 可能有 .gitkeep 之类的占位,没 doc 文件就算空
        doc_files = [f for f in docs if f.suffix in (".md", ".html")]
        assert doc_files == []


class TestRunOneRound:
    def test_runs_all_kinds_for_one_meeting(self, populated_meeting):
        """一个会议触发 6 种 doc_kind(都 dry_run)"""
        results = run_one_round(meeting_ids=[populated_meeting], dry_run=True)
        assert len(results) == 6
        kinds = {r["session_id"].split(":")[-1] for r in results}
        assert kinds == set(DOC_KINDS)

    def test_empty_no_meetings_no_results(self):
        """空目录无会议 → 0 results"""
        results = run_one_round(dry_run=True)
        assert results == []


class TestDocKinds:
    def test_exactly_six_kinds(self):
        """MVP 固定 6 种(架构 v1.16 决定)"""
        assert len(DOC_KINDS) == 6
        assert set(DOC_KINDS) == {"req", "arch", "tasks", "api", "risk", "demo"}


class TestPromptTemplates:
    def test_all_templates_exist(self):
        """6 个 prompt 模板都存在"""
        for kind in DOC_KINDS:
            p = PROMPTS_DIR / f"{kind}.md"
            assert p.exists(), f"Missing prompt: {p}"

    def test_templates_no_tool_specification(self):
        """模板不指定具体工具名(用户纠错)"""
        for kind in DOC_KINDS:
            content = (PROMPTS_DIR / f"{kind}.md").read_text(encoding="utf-8")
            # 不应该有"用 read_file" / "用 write_file" 这种工具指定
            bad_patterns = [
                "用 read_file",
                "用 write_file",
                "use write_file",
                "use read_file",
                "调用 read_file",
                "调用 write_file",
            ]
            for bad in bad_patterns:
                assert bad not in content, f"{kind}.md contains: '{bad}'"

    def test_templates_mention_yagni(self):
        """模板都有 YAGNI 提醒(避免过度生成)"""
        for kind in DOC_KINDS:
            content = (PROMPTS_DIR / f"{kind}.md").read_text(encoding="utf-8")
            assert "YAGNI" in content or "不主动" in content


class TestAgentCache:
    """2026-06-22 ADR-0009 落地:in-process AIAgent 跨轮询复用"""

    def test_get_or_create_agent_reuses_instance(self):
        """同 (meeting_id, doc_kind) 两次调用 → 同一 AIAgent 实例"""
        from vpbuddy.sub_session_controller import (
            _AGENT_CACHE, _get_or_create_agent, _agent_session_id,
        )
        # 清掉之前的 cache(测试隔离)
        _AGENT_CACHE.clear()

        if not _get_or_create_agent.__module__:
            pytest.skip("AIAgent not available")

        sid = _agent_session_id("CACHE_TEST_001", "req")
        # 假装有 AIAgent(否则 import 会失败)
        try:
            a1 = _get_or_create_agent("CACHE_TEST_001", "req")
            a2 = _get_or_create_agent("CACHE_TEST_001", "req")
        except RuntimeError:
            pytest.skip("AIAgent not available (no hermes-agent)")

        assert a1 is a2, "Expected same AIAgent instance for same (mid, kind)"
        assert sid in _AGENT_CACHE

    def test_different_doc_kinds_different_agents(self):
        """不同 doc_kind → 不同 AIAgent 实例"""
        from vpbuddy.sub_session_controller import _AGENT_CACHE, _get_or_create_agent

        _AGENT_CACHE.clear()

        try:
            a_req = _get_or_create_agent("CACHE_TEST_002", "req")
            a_arch = _get_or_create_agent("CACHE_TEST_002", "arch")
        except RuntimeError:
            pytest.skip("AIAgent not available")

        assert a_req is not a_arch
        assert len(_AGENT_CACHE) == 2

    def test_session_id_format(self):
        """session_id 格式 = meeting:{mid}:{kind}"""
        from vpbuddy.sub_session_controller import _agent_session_id
        assert _agent_session_id("MTG123", "req") == "meeting:MTG123:req"
        assert _agent_session_id("PHASE2_TEST", "demo") == "meeting:PHASE2_TEST:demo"


class TestVpbuddyDirectMode:
    """2026-06-22 ADR-0009:VPBUDDY_DIRECT=1 模式保留(主 session 写文件)"""

    def test_direct_mode_skips_llm(self, populated_meeting, monkeypatch):
        """VPBUDDY_DIRECT=1 时,trigger 不调 LLM,只返 prompt + doc_path"""
        monkeypatch.setenv("VPBUDDY_DIRECT", "1")
        r = trigger_sub_session(populated_meeting, "req", dry_run=False)
        assert r["triggered"] is True
        assert r.get("agent_path") == "direct"
        assert "doc_path" in r
        assert "prompt" in r
        # prompt 包含会议 ID
        assert populated_meeting in r["prompt"]


class TestParallelRun:
    """2026-06-22 ADR-0009 落地:ThreadPoolExecutor 真并行触发 6 doc_kind"""

    def test_run_one_round_parallel(self, populated_meeting):
        """并行触发 → 6 个结果,每个有 session_id"""
        results = run_one_round(
            meeting_ids=[populated_meeting],
            dry_run=True,
            parallel=True,
        )
        assert len(results) == 6
        for r in results:
            assert "session_id" in r
            assert populated_meeting in r["session_id"]

    def test_run_one_round_serial(self, populated_meeting):
        """serial=True 也跑 6 个"""
        results = run_one_round(
            meeting_ids=[populated_meeting],
            dry_run=True,
            parallel=False,
        )
        assert len(results) == 6
