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
from vpbuddy import sub_session_controller as ssc
from vpbuddy.sub_session_controller import _AGENT_CACHE


@pytest.fixture(autouse=True)
def _isolate_agent_cache():
    """每个 test 前清空 _AGENT_CACHE, 防测试间污染 (2026-06-24 +test_cleanup_inactive_agents 后必需)"""
    _AGENT_CACHE.clear()
    # 同时确保 DATA_DIR 指向 TEST_DATA (其它测试可能 monkeypatch 过)
    ssc.DATA_DIR = Path(TEST_DATA)
    ssc.DOCS_DIR = Path(TEST_DOCS)
    yield
    _AGENT_CACHE.clear()
    ssc.DATA_DIR = Path(TEST_DATA)
    ssc.DOCS_DIR = Path(TEST_DOCS)


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
            _AGENT_CACHE, _AGENT_AVAILABLE, _get_or_create_agent, _agent_session_id,
        )
        if not _AGENT_AVAILABLE:
            pytest.skip("AIAgent not available (no hermes-agent)")

        _AGENT_CACHE.clear()  # 测试隔离
        sid = _agent_session_id("CACHE_TEST_001", "req")

        a1 = _get_or_create_agent("CACHE_TEST_001", "req")
        a2 = _get_or_create_agent("CACHE_TEST_001", "req")
        assert a1 is a2, "Expected same AIAgent instance for same (mid, kind)"
        assert sid in _AGENT_CACHE

    def test_different_doc_kinds_different_agents(self):
        """不同 doc_kind → 不同 AIAgent 实例"""
        from vpbuddy.sub_session_controller import _AGENT_CACHE, _AGENT_AVAILABLE, _get_or_create_agent

        if not _AGENT_AVAILABLE:
            pytest.skip("AIAgent not available")

        _AGENT_CACHE.clear()
        a_req = _get_or_create_agent("CACHE_TEST_002", "req")
        a_arch = _get_or_create_agent("CACHE_TEST_002", "arch")
        assert a_req is not a_arch
        assert len(_AGENT_CACHE) == 2

    def test_session_id_format(self):
        """session_id 格式 = meeting:{mid}:{kind}(不依赖 AIAgent)"""
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


class TestOfflineDefaults:
    """2026-06-22 §19/§20 踩坑:KB 模型默认离线,避免 controller 进程卡 53min

    conftest.py 在 pytest 进程设了 HF_HUB_OFFLINE=1,但跑 `python -m vpbuddy.sub_session_controller`
    时 conftest 不加载。controller.py 顶部 setdefault 这俩 env var,KB add_document 才不联网。
    """

    def test_hf_hub_offline_set_in_controller_module(self):
        """sub_session_controller.py 顶部应设 HF_HUB_OFFLINE=1"""
        import subprocess
        import sys
        env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": "src"}
        result = subprocess.run(
            [sys.executable, "-c",
             "import os; from vpbuddy import sub_session_controller; "
             "print(os.environ.get('HF_HUB_OFFLINE', 'NOT_SET'))"],
            capture_output=True, text=True, env=env,
            cwd=os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        )
        assert result.stdout.strip() == "1", \
            f"Expected HF_HUB_OFFLINE=1 after import, got {result.stdout.strip()!r} (stderr: {result.stderr.strip()!r})"

    def test_transformers_offline_set_in_controller_module(self):
        """sub_session_controller.py 顶部应设 TRANSFORMERS_OFFLINE=1"""
        import subprocess
        import sys
        env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": "src"}
        result = subprocess.run(
            [sys.executable, "-c",
             "import os; from vpbuddy import sub_session_controller; "
             "print(os.environ.get('TRANSFORMERS_OFFLINE', 'NOT_SET'))"],
            capture_output=True, text=True, env=env,
            cwd=os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        )
        assert result.stdout.strip() == "1", \
            f"Expected TRANSFORMERS_OFFLINE=1 after import, got {result.stdout.strip()!r} (stderr: {result.stderr.strip()!r})"


class TestKbStatus:
    """2026-06-22:KB 状态可观测 + 自动 retry

    trigger_sub_session 触发 KB 后,_KB_STATUS 记录状态(queued/stored/failed/retrying)。
    get_kb_status() 给 UI / CLI / 监控用。
    """

    def test_kb_status_empty_initially(self):
        """_KB_STATUS 启动时为空"""
        from vpbuddy.sub_session_controller import _KB_STATUS
        _KB_STATUS.clear()
        assert _KB_STATUS == {}

    def test_kb_status_after_dry_run_no_entry(self, populated_meeting):
        """dry_run=True 不写 KB → _KB_STATUS 没该项"""
        from vpbuddy.sub_session_controller import _KB_STATUS, trigger_sub_session
        _KB_STATUS.clear()
        trigger_sub_session(populated_meeting, "req", dry_run=True)
        assert (populated_meeting, "req") not in _KB_STATUS

    def test_kb_status_after_actual_trigger(self, populated_meeting):
        """ADR-0020: KB 自动 ingest 已废弃, 验证不再写入 _KB_STATUS"""
        from vpbuddy.sub_session_controller import _KB_STATUS, trigger_sub_session, get_doc_path
        doc_path = get_doc_path(populated_meeting, "req")
        doc_path.parent.mkdir(parents=True, exist_ok=True)
        doc_path.write_text("# Test req doc\n", encoding="utf-8")
        try:
            r = trigger_sub_session(populated_meeting, "req", dry_run=False)
            if r["triggered"]:
                assert (populated_meeting, "req") not in _KB_STATUS, \
                    "KB auto-ingest should be disabled by ADR-0020"
        finally:
            doc_path.unlink(missing_ok=True)

    def test_get_kb_status_summary(self):
        """get_kb_status() 返回空 (ADR-0020 stub)"""
        from vpbuddy.sub_session_controller import get_kb_status
        data = get_kb_status()
        assert data["summary"]["total"] == 0
        assert data["summary"]["stored"] == 0

    def test_get_kb_status_filter_by_meeting(self):
        """get_kb_status(meeting_id=X) 返回空 (ADR-0020 stub)"""
        from vpbuddy.sub_session_controller import get_kb_status
        data = get_kb_status(meeting_id="MTG_A")
        assert data["summary"]["total"] == 0
        assert data["items"] == []


class TestTriggerWritesFile:
    """验证 trigger_sub_session 真的把 doc 写盘了(2026-06-22 修:之前 trigger=True 假阳性)

    Bug 历史:PHASE3_TTS_TEST 测试发现 agent.chat() 返回文字响应但没调 write_file 工具,
    trigger API 仍返 triggered=True,KB 也没进。
    Fix:trigger_sub_session 调 agent 后强制验证 doc_path.exists(),不存在则改 triggered=False。
    """

    def test_trigger_false_when_agent_did_not_write(self, populated_meeting, monkeypatch):
        """agent 返文字但没写文件 + VPBUDDY_FALLBACK=0 → trigger 返 False

        默认 (VPBUDDY_FALLBACK=1) 会自动调 fallback 写盘,所以这里设 FALLBACK=0 测严格路径。
        """
        from vpbuddy import sub_session_controller as ctrl
        # 1. 删可能存在的旧文件
        doc_path = get_doc_path(populated_meeting, "req")
        if doc_path.exists():
            doc_path.unlink()
        # 2. Mock _trigger_via_aiagent 让它返 triggered=True 但不写文件
        def fake_aiagent(prompt, meeting_id, doc_kind):
            return {
                "triggered": True,
                "session_id": f"meeting:{meeting_id}:{doc_kind}",
                "agent_response": "I wrote a great doc but did not call write_file",
                "agent_path": "in-process",
                "error": None,
            }
        monkeypatch.setattr(ctrl, "_trigger_via_aiagent", fake_aiagent)
        monkeypatch.setattr(ctrl, "_AGENT_AVAILABLE", True)
        monkeypatch.setenv("VPBUDDY_FALLBACK", "0")
        # 3. 调 trigger
        r = trigger_sub_session(populated_meeting, "req", dry_run=False)
        # 4. 验证:triggered=False + error 信息
        assert r["triggered"] is False, f"应返 False 因为 FALLBACK=0 且文件没写,实得: {r}"
        assert "did not write" in r["error"]
        assert str(doc_path) in r["error"]
        assert not doc_path.exists(), "验证文件确实没写盘"

    def test_trigger_uses_fallback_when_agent_did_not_write(self, populated_meeting, monkeypatch):
        """VPBUDDY_FALLBACK=1 (默认) → agent 不写时自动 fallback 写盘"""
        from vpbuddy import sub_session_controller as ctrl
        doc_path = get_doc_path(populated_meeting, "tasks")
        if doc_path.exists():
            doc_path.unlink()
        def fake_aiagent(prompt, meeting_id, doc_kind):
            return {
                "triggered": True,
                "session_id": f"meeting:{meeting_id}:{doc_kind}",
                "agent_response": "I forgot to call write_file",
                "agent_path": "in-process",
                "error": None,
            }
        monkeypatch.setattr(ctrl, "_trigger_via_aiagent", fake_aiagent)
        monkeypatch.setattr(ctrl, "_AGENT_AVAILABLE", True)
        # FALLBACK=1 是默认,显式确认
        monkeypatch.setenv("VPBUDDY_FALLBACK", "1")
        r = trigger_sub_session(populated_meeting, "tasks", dry_run=False)
        assert r["triggered"] is True
        assert r.get("fallback_used") is True
        assert doc_path.exists(), "fallback 必须把文件写盘"
        assert r.get("doc_size", 0) > 0
        # 清理
        doc_path.unlink(missing_ok=True)

    def test_trigger_true_when_agent_wrote_file(self, populated_meeting, monkeypatch):
        """agent 返文字 + 真写了文件 → trigger 返 True + doc_size"""
        from vpbuddy import sub_session_controller as ctrl
        doc_path = get_doc_path(populated_meeting, "api")
        # 1. 预先创建文件(模拟 AIAgent 写过了)
        doc_path.parent.mkdir(parents=True, exist_ok=True)
        doc_path.write_text("# API\n\n## POST /v1/test\n", encoding="utf-8")
        # 2. Mock agent 返 triggered=True
        def fake_aiagent(prompt, meeting_id, doc_kind):
            return {
                "triggered": True,
                "session_id": f"meeting:{meeting_id}:{doc_kind}",
                "agent_response": "I called write_file",
                "agent_path": "in-process",
                "error": None,
            }
        monkeypatch.setattr(ctrl, "_trigger_via_aiagent", fake_aiagent)
        monkeypatch.setattr(ctrl, "_AGENT_AVAILABLE", True)
        try:
            r = trigger_sub_session(populated_meeting, "api", dry_run=False)
            # 3. 验证:triggered=True + doc_size 字段
            assert r["triggered"] is True
            assert r.get("doc_size") == doc_path.stat().st_size
        finally:
            doc_path.unlink(missing_ok=True)

    def test_dry_run_skips_write_check(self, populated_meeting):
        """dry_run 不走写盘验证(只渲染 prompt)"""
        # dry_run 路径会 return 早,不调 _trigger_via_aiagent
        r = trigger_sub_session(populated_meeting, "demo", dry_run=True)
        assert r.get("dry_run") is True
        assert r["triggered"] is False  # dry_run 永远 False

    def test_vpbuddy_direct_skips_write_check(self, populated_meeting, monkeypatch):
        """VPBUDDY_DIRECT=1 模式不走写盘验证(主 session 写文件)"""
        monkeypatch.setenv("VPBUDDY_DIRECT", "1")
        r = trigger_sub_session(populated_meeting, "risk", dry_run=False)
        assert r["triggered"] is True
        assert r["agent_path"] == "direct"
        assert "doc_path" in r

