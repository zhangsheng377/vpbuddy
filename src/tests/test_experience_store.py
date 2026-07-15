"""测试 experience_store — 经验持久化 + 检索 (v0.9.0 #1 Phase 1)

覆盖:
- save_experiences() + load_experiences() 文件读写 (tmp_path)
- _update_aggregate() + get_approved_experiences() 聚合去重
- search_experiences() 按 domain/product_type 过滤
- approve_experience() 确认状态
- extract_from_meeting_state() 从 Mock MeetingState 提取
- format_experiences_for_prompt() 输出格式
- 空输入/无文件时的边界
"""

from __future__ import annotations

import sys
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from vpbuddy.experience import ExperienceItem, ExperienceKind
from vpbuddy import experience_store as store


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _patch_experiences_dir(tmp_path, monkeypatch):
    """将所有 experience_store 文件操作重定向到 tmp_path."""
    test_dir = tmp_path / "experiences"
    monkeypatch.setattr(store, "EXPERIENCES_DIR", test_dir)
    monkeypatch.setattr(store, "_aggregate_path", test_dir / "_all.json")
    return test_dir


@pytest.fixture
def sample_items() -> list[ExperienceItem]:
    return [
        ExperienceItem(
            kind="domain_fact",
            text="物理实验需考虑单位系统",
            source_meeting_id="mtg_ph1",
            domain="physics",
            evidence=["REQ: 单位系统兼容"],
            confidence=0.4,
            approved=False,
        ),
        ExperienceItem(
            kind="failure_lesson",
            text="注意事项: 跳过边界条件审核导致返工",
            source_meeting_id="mtg_mf1",
            domain="manufacturing",
            evidence=["RISK: 边界条件未审核"],
            confidence=0.5,
            approved=True,
        ),
        ExperienceItem(
            kind="decision_rule",
            text="目标约束: 合规优先于效率",
            source_meeting_id="mtg_fn1",
            domain="fintech",
            product_type="api_service",
            evidence=["GOAL: 合规优先"],
            confidence=0.3,
            approved=True,
        ),
    ]


# ---------------------------------------------------------------------------
# save / load
# ---------------------------------------------------------------------------


class TestSaveLoad:
    """save_experiences + load_experiences."""

    def test_save_and_load(self, sample_items):
        """save 后 load 应返回相同条目."""
        store.save_experiences("test_mtg", sample_items)
        loaded = store.load_experiences("test_mtg")
        assert len(loaded) == len(sample_items)
        for orig, loaded_item in zip(sample_items, loaded):
            assert loaded_item.id == orig.id
            assert loaded_item.text == orig.text
            assert loaded_item.kind == orig.kind

    def test_load_nonexistent(self):
        """不存在的 meeting_id 应返回空列表."""
        loaded = store.load_experiences("nonexistent_mtg")
        assert loaded == []

    def test_load_corrupted_file(self, tmp_path):
        """损坏的 JSON 文件应返回空列表."""
        exp_dir = tmp_path / "experiences"
        exp_dir.mkdir(parents=True, exist_ok=True)
        (exp_dir / "corrupt.json").write_text("{invalid json", encoding="utf-8")
        loaded = store.load_experiences("corrupt")
        assert loaded == []

    def test_save_creates_file(self, sample_items):
        """save_experiences 应生成 .json 文件."""
        store.save_experiences("create_check", sample_items)
        path = store.EXPERIENCES_DIR / "create_check.json"
        assert path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["meeting_id"] == "create_check"
        assert len(data["items"]) == len(sample_items)

    def test_save_updates_aggregate(self, sample_items):
        """save_experiences 应同时更新聚合索引."""
        store.save_experiences("agg_check", sample_items)
        assert store._aggregate_path.exists()
        data = json.loads(store._aggregate_path.read_text(encoding="utf-8"))
        assert len(data["items"]) == len(sample_items)


# ---------------------------------------------------------------------------
# 聚合 / get_approved
# ---------------------------------------------------------------------------


class TestAggregate:
    """_update_aggregate + get_approved_experiences."""

    def test_get_approved_empty_when_no_aggregate(self):
        """无聚合文件时返回空列表."""
        approved = store.get_approved_experiences()
        assert approved == []

    def test_get_approved_filters(self, sample_items):
        """get_approved_experiences 只返回 approved=True 的条目."""
        store.save_experiences("approved_test", sample_items)
        approved = store.get_approved_experiences()
        # sample_items 中 2 个 approved=True, 1 个 approved=False
        assert len(approved) == 2
        for item in approved:
            assert item.approved is True

    def test_aggregate_dedup(self, sample_items):
        """相同 id 的条目不应重复写入聚合."""
        store.save_experiences("dedup_1", sample_items[:1])
        store.save_experiences("dedup_2", sample_items[:1])  # 同一个 item
        data = json.loads(store._aggregate_path.read_text(encoding="utf-8"))
        ids = {it["id"] for it in data["items"]}
        assert len(ids) == len(data["items"])  # 无重复 id

    def test_aggregate_combines_multiple_meetings(self, sample_items):
        """多个会议的 save 应合并到聚合索引."""
        store.save_experiences("multi_1", sample_items[:1])
        store.save_experiences("multi_2", sample_items[1:])
        data = json.loads(store._aggregate_path.read_text(encoding="utf-8"))
        assert len(data["items"]) == len(sample_items)


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


class TestSearch:
    """search_experiences."""

    def test_search_by_domain(self, sample_items):
        """按 domain 过滤 (仅搜索已 approved 条目)."""
        store.save_experiences("search_domain", sample_items)
        # 只有 approved=True 的条目会被搜索; sample_items 中 approved=True 的 domain 是 manufacturing 和 fintech
        results = store.search_experiences(domain="manufacturing")
        assert len(results) == 1
        assert results[0].domain == "manufacturing"

    def test_search_by_product_type(self, sample_items):
        """按 product_type 过滤."""
        store.save_experiences("search_ptype", sample_items)
        results = store.search_experiences(product_type="api_service")
        assert len(results) == 1
        assert results[0].product_type == "api_service"

    def test_search_no_match(self, sample_items):
        """无匹配返回空列表."""
        store.save_experiences("search_none", sample_items)
        results = store.search_experiences(domain="education")
        assert results == []

    def test_search_all(self, sample_items):
        """不传过滤条件返回全部 approved."""
        store.save_experiences("search_all", sample_items)
        results = store.search_experiences()
        assert len(results) == 2  # 只包含 approved=True


# ---------------------------------------------------------------------------
# approve
# ---------------------------------------------------------------------------


class TestApprove:
    """approve_experience."""

    def test_approve_success(self, sample_items):
        """确认一条经验."""
        store.save_experiences("app_test", sample_items)
        target_id = sample_items[0].id
        assert sample_items[0].approved is False

        result = store.approve_experience(target_id, "app_test")
        assert result is True

        # 重新加载应看到 approved=True
        reloaded = store.load_experiences("app_test")
        for item in reloaded:
            if item.id == target_id:
                assert item.approved is True
                break
        else:
            pytest.fail("未找到目标条目")

    def test_approve_not_found(self, sample_items):
        """不存在的 item_id 返回 False."""
        store.save_experiences("app_nf", sample_items)
        result = store.approve_experience("nonexistent_id", "app_nf")
        assert result is False

    def test_approve_updates_meeting_file(self, sample_items):
        """approve 后会议文件中的 approved 字段应更新."""
        item = sample_items[0]
        assert item.approved is False
        store.save_experiences("app_file", [item])

        # approve
        result = store.approve_experience(item.id, "app_file")
        assert result is True

        # 重新加载会议文件应看到 approved=True
        reloaded = store.load_experiences("app_file")
        for it in reloaded:
            if it.id == item.id:
                assert it.approved is True
                break
        else:
            pytest.fail("未找到目标条目")


# ---------------------------------------------------------------------------
# extract_from_meeting_state
# ---------------------------------------------------------------------------


class TestExtractFromMeetingState:
    """extract_from_meeting_state."""

    def _make_mock_state(self):
        """构造一个 Mock MeetingState 对象."""
        state = MagicMock()
        state.requirements = [
            MagicMock(text="需要支持多单位系统（公制/英制）实时转换"),
            MagicMock(text="数据采集频率不低于 100Hz 且支持多种传感器类型"),
        ]
        state.goals = [
            MagicMock(text="实现跨平台数据同步以确保多团队协作效率"),
            MagicMock(text="降低系统延迟到 50ms 以内以提升用户体验"),
        ]
        state.features = [
            MagicMock(text="实时数据看板"),
        ]
        state.risks = [
            MagicMock(text="传感器精度受温度影响可能导致数据偏差且需要额外校准流程"),
        ]
        state.open_questions = [
            MagicMock(text="是否需要支持离线模式？"),
        ]
        return state

    def test_extract_basic(self):
        """正常提取应返回经验列表."""
        state = self._make_mock_state()
        items = store.extract_from_meeting_state("extract_mtg", state, "物理实验数据平台")
        assert len(items) > 0

        # 检查 kind 分布
        kinds = {it.kind for it in items}
        assert ExperienceKind.DOMAIN_FACT in kinds
        assert ExperienceKind.FAILURE_LESSON in kinds
        assert ExperienceKind.DECISION_RULE in kinds

        # 所有条目的 source_meeting_id 正确
        for item in items:
            assert item.source_meeting_id == "extract_mtg"

        # 所有条目未确认
        for item in items:
            assert item.approved is False

    def test_extract_domain_guessed(self):
        """提取时应猜测 domain."""
        state = self._make_mock_state()
        items = store.extract_from_meeting_state("extract_dom", state, "物理实验需求评审")
        for item in items:
            assert item.domain == "physics"

    def test_extract_empty_state(self):
        """空 state 应返回空列表."""
        state = MagicMock()
        state.requirements = []
        state.goals = []
        state.features = []
        state.risks = []
        state.open_questions = []
        items = store.extract_from_meeting_state("empty_mtg", state)
        assert items == []

    def test_extract_short_text_filtered(self):
        """过短的文本应被过滤."""
        state = MagicMock()
        state.requirements = [MagicMock(text="OK")]
        state.goals = [MagicMock(text="")]  # short — filtered
        state.features = [MagicMock(text="")]  # will be empty
        state.risks = [MagicMock(text="")]
        state.open_questions = [MagicMock(text="No")]
        items = store.extract_from_meeting_state("short_mtg", state, "test")
        # REQ 是 "OK" (2 chars) — filtered because len <= 15 (len("OK") = 2)
        # Actually requirement text "OK" length = 2, rule requires > 15 for domain_fact, > 20 for others
        # So all items will be filtered out
        # Actually "OK" has len 2 which is < 15, so no domain_fact items
        # GOAL is "" length 0, < 20, no decision_rule
        # RISK is "" length 0, < 20, no failure_lesson
        # => items should be empty
        assert items == []

    def test_extract_max_items(self, sample_items):
        """提取最多 20 条."""
        # 构造有很多条目的 state
        state = MagicMock()
        state.requirements = [MagicMock(text=f"需求 {i}: 这是一个足够长的需求描述文本") for i in range(30)]
        state.goals = []
        state.features = []
        state.risks = []
        state.open_questions = []
        items = store.extract_from_meeting_state("max_mtg", state)
        assert len(items) <= 20

    def test_extract_state_with_none_fields(self):
        """state 字段为 None 不应抛异常."""
        state = MagicMock()
        state.requirements = None
        state.goals = None
        state.features = None
        state.risks = None
        state.open_questions = None
        items = store.extract_from_meeting_state("none_mtg", state)
        assert items == []


# ---------------------------------------------------------------------------
# format_experiences_for_prompt
# ---------------------------------------------------------------------------


class TestFormatExperiencesForPrompt:
    """format_experiences_for_prompt."""

    def test_format_with_items(self, sample_items):
        """正常列表应格式化."""
        approved = [it for it in sample_items if it.approved]
        text = store.format_experiences_for_prompt(approved)
        assert "历史经验参考" in text
        for item in approved:
            assert item.text in text

    def test_format_empty(self):
        """空列表返回空字符串."""
        text = store.format_experiences_for_prompt([])
        assert text == ""

    def test_format_max_items(self, sample_items):
        """max_items 限制."""
        many = sample_items * 5  # 15 items
        text = store.format_experiences_for_prompt(many, max_items=3)
        # 只应包含 3 条 + header/footer
        lines = [l for l in text.split("\n") if l.startswith("- **")]
        assert len(lines) == 3

    def test_format_includes_domain(self):
        """有 domain 的条目应显示领域."""
        item = ExperienceItem(
            kind="domain_fact", text="测试文本",
            source_meeting_id="m", domain="physics", approved=True,
        )
        text = store.format_experiences_for_prompt([item])
        assert "领域: physics" in text

    def test_format_kind_label(self):
        """类别标签应正确映射."""
        item = ExperienceItem(
            kind="failure_lesson", text="教训",
            source_meeting_id="m", approved=True,
        )
        text = store.format_experiences_for_prompt([item])
        assert "教训" in text


class TestPiiDetection:
    """v0.23.0: _might_contain_pii 检测 — 拒绝含人名/邮箱/电话的候选."""

    def test_clean_domain_fact_ok(self):
        assert not store._might_contain_pii("需求信息不足时，应先澄清再生成具体接口")

    def test_name_with_surname_rejected(self):
        assert store._might_contain_pii("张三要求采购系统支持短信登录")

    def test_name_in_context_rejected(self):
        assert store._might_contain_pii("叫王芳来办公室开会")

    def test_email_rejected(self):
        assert store._might_contain_pii("请联系 admin@example.com 获取权限")

    def test_phone_rejected(self):
        assert store._might_contain_pii("手机号 13812345678 请联系")

    def test_short_text_rejected(self):
        assert store._might_contain_pii("OK")

    def test_long_text_rejected(self):
        assert store._might_contain_pii("这是一个非常长的需求描述" + "测试文本" * 50)

    def test_clean_short_desc_ok(self):
        assert not store._might_contain_pii("当用户上传无效文件时，显示友好错误信息")

    def test_clean_generic_rule_ok(self):
        assert not store._might_contain_pii("API 返回 500 时应记录错误并通知用户")
