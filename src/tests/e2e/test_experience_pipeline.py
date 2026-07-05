"""E2E: 经验蒸馏 (Experience Distillation) 端到端流程测试

测试场景:
- 创建一个会议 → 添加 state facts → close_meeting → 验证经验候选被写入
- 从 experience_store 加载并验证 ExperienceItem
- 验证聚合索引包含新条目
- 测试 approve + search 功能
- 清理测试数据
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import uuid
from datetime import datetime
from functools import wraps
from pathlib import Path
from typing import Callable, Generator

import pytest

pytestmark = pytest.mark.e2e

_E2E_SKIP = os.environ.get("RUN_E2E") != "1"


# =============================================================================
# Helper: 临时覆盖 EXPERIENCES_DIR (装饰器/上下文管理器)
# =============================================================================


def _with_exp_dir(test_fn):
    """装饰器: 为测试函数提供独立的临时 experiences 目录.

    被装饰的测试函数第一个额外参数是 exp_dir (Path),
    第二个额外参数是 store_mod (module reference).
    测试函数内所有 experience_store 操作自动使用该临时目录.
    """

    @wraps(test_fn)
    def wrapper(*args, **kwargs):
        import vpbuddy.experience_store as store_mod

        with tempfile.TemporaryDirectory(prefix="vp_e2e_exp_") as td:
            tmp_dir = Path(td)
            exp_dir = tmp_dir / "experiences"

            # 保存原始值并覆盖
            orig_dir = store_mod.EXPERIENCES_DIR
            orig_agg = store_mod._aggregate_path
            try:
                store_mod.EXPERIENCES_DIR = exp_dir
                store_mod._aggregate_path = exp_dir / "_all.json"
                store_mod.ensure_dir()

                # 将 exp_dir 和 store_mod 作为额外参数传递给测试函数
                return test_fn(*args, exp_dir=exp_dir, store_mod=store_mod, **kwargs)
            finally:
                store_mod.EXPERIENCES_DIR = orig_dir
                store_mod._aggregate_path = orig_agg

    return wrapper


@pytest.fixture(scope="module")
def sample_meeting_id() -> str:
    """生成唯一测试会议 ID."""
    return f"test_e2e_exp_{int(time.time())}_{uuid.uuid4().hex[:6]}"


@pytest.fixture(scope="module")
def test_state(sample_meeting_id: str):
    """创建包含丰富 state facts 的测试会议.

    Notes:
        goals 文本长度 > 20 字符 (提取逻辑有 len > 20 过滤)。
    """
    from vpbuddy.state import MeetingState, Platform, Priority

    state = MeetingState(
        meeting_id=sample_meeting_id,
        project_name="E2E 测试: 物理实验数据平台",
        platform=Platform.LOCAL,
    )

    # 添加 requirements (每个 > 15 字符, 可被提取为 domain_fact)
    state.add_requirement("系统需支持物理实验数据的采集和存储", priority=Priority.HIGH)
    state.add_requirement("数据采集频率需达到 100Hz 以上", priority=Priority.HIGH)
    state.add_requirement("用户权限管理需支持角色分级", priority=Priority.MEDIUM)
    state.add_requirement("多实验室数据汇总需支持标准化格式转换", priority=Priority.HIGH)
    state.add_requirement("数据存储需满足至少五年的历史数据留存", priority=Priority.MEDIUM)

    # 添加 goals (文本 > 20 字符以满足 extraction 过滤)
    state.add_goal("打造一套完整的科研数据管理平台和实验协作工具链")
    state.add_goal("确保实验数据的可追溯性和可重复性满足行业标准规范")

    # 添加 features
    state.add_feature("支持多种数据导入格式 (CSV, Excel, JSON)")
    state.add_feature("提供数据可视化看板功能")

    # 添加 risks (文本 > 20 字符)
    state.add_risk("高频采集可能导致系统性能瓶颈需要优化缓存策略", priority=Priority.HIGH)
    state.add_risk("不同实验室的数据格式不统一可能增加集成成本", priority=Priority.MEDIUM)

    # 添加 open questions
    state.add_question("是否支持实时数据流处理?", is_urgent=True)
    state.add_question("数据保留策略如何定义?", is_urgent=False)

    return state


# =============================================================================
# Tests
# =============================================================================


@pytest.mark.skipif(_E2E_SKIP, reason="RUN_E2E != 1")
class TestExperiencePipeline:

    def test_extract_experiences(self, sample_meeting_id: str, test_state):
        """从 MeetingState 提取经验候选."""
        from vpbuddy.experience_store import extract_from_meeting_state

        items = extract_from_meeting_state(
            sample_meeting_id, test_state,
            meeting_title="E2E 测试: 物理实验数据平台",
        )
        assert len(items) > 0, "应该至少提取到一条经验"
        assert all(it.source_meeting_id == sample_meeting_id for it in items)

        # 应有 domain_fact (来自 requirements)
        domain_facts = [it for it in items if it.kind.value == "domain_fact"]
        assert len(domain_facts) > 0, "应该从 requirements 提取领域事实"
        print(f"提取到 {len(items)} 条经验候选 ({len(domain_facts)} 条领域事实)")

        # 应有 failure_lesson (来自 risks)
        failure_lessons = [it for it in items if it.kind.value == "failure_lesson"]
        assert len(failure_lessons) > 0, "应该从 risks 提取失败教训"

        # 应有 decision_rule (来自 goals, 文本 > 20 字符)
        decision_rules = [it for it in items if it.kind.value == "decision_rule"]
        assert len(decision_rules) > 0, "应该从 goals 提取决策规则"

        # 所有经验初始应为未确认
        assert all(it.approved is False for it in items)

        # 验证 domain 猜测
        domains = {it.domain for it in items if it.domain}
        assert "physics" in domains, f"应猜测领域为 physics, 实际: {domains}"

    @_with_exp_dir
    def test_save_and_load_experiences(self, sample_meeting_id: str, test_state,
                                       exp_dir: Path = None, store_mod=None):
        """保存并重新加载经验."""
        from vpbuddy.experience_store import extract_from_meeting_state, save_experiences, load_experiences

        items = extract_from_meeting_state(
            sample_meeting_id, test_state,
            meeting_title="E2E 测试: 物理实验数据平台",
        )
        assert len(items) > 0

        saved_path = save_experiences(sample_meeting_id, items)
        assert Path(saved_path).exists(), f"保存文件不存在: {saved_path}"

        # 验证 JSON 格式
        with open(saved_path, encoding="utf-8") as f:
            data = json.load(f)
        assert data["meeting_id"] == sample_meeting_id
        assert len(data["items"]) == len(items)

        # 加载回来
        loaded = load_experiences(sample_meeting_id)
        assert len(loaded) == len(items)
        assert loaded[0].kind == items[0].kind
        assert loaded[0].text == items[0].text

    @_with_exp_dir
    def test_aggregate_index_updated(self, sample_meeting_id: str, test_state,
                                     exp_dir: Path = None, store_mod=None):
        """验证聚合索引包含新条目."""
        from vpbuddy.experience_store import extract_from_meeting_state, save_experiences

        items = extract_from_meeting_state(
            sample_meeting_id, test_state,
            meeting_title="E2E 测试: 物理实验数据平台",
        )
        save_experiences(sample_meeting_id, items)

        # 验证 _all.json
        agg_path = store_mod._aggregate_path
        assert agg_path.exists(), f"聚合索引不存在: {agg_path}"
        with open(agg_path, encoding="utf-8") as f:
            agg_data = json.load(f)
        assert "items" in agg_data
        assert len(agg_data["items"]) == len(items), (
            f"聚合索引条目数 {len(agg_data['items'])} 应等于 {len(items)}"
        )
        # 所有条目初始 approved=False
        approved = [it for it in agg_data["items"] if it.get("approved")]
        assert len(approved) == 0, "新条目不应自动 approved"

    @_with_exp_dir
    def test_approve_experience(self, sample_meeting_id: str, test_state,
                                exp_dir: Path = None, store_mod=None):
        """测试 approve 功能."""
        from vpbuddy.experience_store import (
            extract_from_meeting_state,
            save_experiences,
            approve_experience,
            load_experiences,
        )

        items = extract_from_meeting_state(
            sample_meeting_id, test_state,
            meeting_title="E2E 测试: 物理实验数据平台",
        )
        save_experiences(sample_meeting_id, items)

        # approve 第一条
        first_id = items[0].id
        result = approve_experience(first_id, sample_meeting_id)
        assert result, "approve 应返回 True"

        # 验证加载后已 approved
        loaded = load_experiences(sample_meeting_id)
        approved_items = [it for it in loaded if it.approved]
        assert len(approved_items) >= 1
        assert approved_items[0].id == first_id

    @_with_exp_dir
    def test_search_experiences(self, sample_meeting_id: str, test_state,
                                exp_dir: Path = None, store_mod=None):
        """测试 search_experiences 按领域检索."""
        from vpbuddy.experience_store import (
            extract_from_meeting_state,
            save_experiences,
            approve_experience,
            search_experiences,
        )

        items = extract_from_meeting_state(
            sample_meeting_id, test_state,
            meeting_title="E2E 测试: 物理实验数据平台",
        )
        save_experiences(sample_meeting_id, items)

        # approve 所有条目
        for it in items:
            approve_experience(it.id, sample_meeting_id)

        # 按 domain 搜索
        results = search_experiences(domain="physics")
        assert len(results) >= 1, "应检索到至少 1 条 physics 领域经验"
        print(f"search_experiences(domain='physics') 返回 {len(results)} 条")

        # 按不存在的 domain 搜索
        no_results = search_experiences(domain="fintech")
        assert len(no_results) == 0, "不存在的 domain 应返回空列表"

    def test_format_for_prompt(self, sample_meeting_id: str, test_state):
        """测试 format_experiences_for_prompt."""
        from vpbuddy.experience_store import format_experiences_for_prompt
        from vpbuddy.experience import ExperienceItem

        items = [
            ExperienceItem(
                kind="domain_fact",
                text="物理实验产品需考虑单位系统",
                source_meeting_id=sample_meeting_id,
                domain="physics",
                approved=True,
            ),
            ExperienceItem(
                kind="failure_lesson",
                text="注意事项: 跳过边界条件审核导致返工",
                source_meeting_id=sample_meeting_id,
                domain="physics",
                approved=True,
            ),
        ]

        formatted = format_experiences_for_prompt(items, max_items=5)
        assert "历史经验参考" in formatted
        assert "物理实验产品需考虑单位系统" in formatted
        assert "跳过边界条件审核导致返工" in formatted

        # 空列表
        empty = format_experiences_for_prompt([])
        assert empty == ""

    def test_extract_from_empty_state(self, sample_meeting_id: str):
        """从空 state 提取应返回空列表."""
        from vpbuddy.state import MeetingState, Platform
        from vpbuddy.experience_store import extract_from_meeting_state

        empty_state = MeetingState(
            meeting_id=sample_meeting_id,
            project_name="空会议",
            platform=Platform.LOCAL,
        )
        items = extract_from_meeting_state(sample_meeting_id, empty_state)
        assert len(items) == 0, "空 state 应提取到 0 条经验"

    def test_load_nonexistent_experiences(self):
        """加载不存在的会议经验应返回空列表."""
        from vpbuddy.experience_store import load_experiences

        items = load_experiences("nonexistent_meeting_id_999")
        assert items == [], "不存在的会议应返回空列表"

    def test_experience_dir_creation(self):
        """验证 ensure_dir 创建目录."""
        from vpbuddy.experience_store import ensure_dir

        with tempfile.TemporaryDirectory(prefix="vp_e2e_exp_") as td:
            import vpbuddy.experience_store as store_mod

            exp_dir = Path(td) / "experiences"
            orig_dir = store_mod.EXPERIENCES_DIR
            orig_agg = store_mod._aggregate_path
            try:
                store_mod.EXPERIENCES_DIR = exp_dir
                store_mod._aggregate_path = exp_dir / "_all.json"
                ensure_dir()
                assert exp_dir.exists()
            finally:
                store_mod.EXPERIENCES_DIR = orig_dir
                store_mod._aggregate_path = orig_agg
