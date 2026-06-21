"""测试:MeetingState + MeetingStorage

验证 Step 1 验收标准:
- 状态对象可读可写 ✓
- 跨调用持久化 ✓
- CRUD 完整(add/confirm/reject/list) ✓
"""
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest

# 让 import 找到 vpbuddy 包
sys.path.insert(0, str(Path(__file__).parent.parent))

from vpbuddy.state import (
    MeetingState, Platform, Priority, ItemStatus,
    Requirement, Goal, Feature, Risk, Question,
)
from vpbuddy.storage import MeetingStorage, StorageError


@pytest.fixture
def tmp_storage(tmp_path):
    """临时存储(每个测试独立)"""
    return MeetingStorage(data_dir=tmp_path / "meetings")


class TestMeetingStateCRUD:
    """状态对象 CRUD"""

    def test_create_state(self):
        state = MeetingState(platform=Platform.LOCAL,
                            project_name="XX公司-ESG需求沟通会")
        assert state.platform == Platform.LOCAL
        assert state.project_name == "XX公司-ESG需求沟通会"
        assert state.requirements == []
        assert state.goals == []
        assert state.risks == []
        assert state.open_questions == []
        assert state.meeting_id  # 自动生成
        print(f"  ✓ 创建会议:meeting_id={state.meeting_id}")

    def test_add_requirement(self):
        state = MeetingState()
        req = state.add_requirement("碳排放数据统一管理", priority=Priority.HIGH)
        assert req.id.startswith("REQ-")
        assert req.text == "碳排放数据统一管理"
        assert req.priority == Priority.HIGH
        assert req.status == ItemStatus.PENDING
        assert len(state.requirements) == 1
        print(f"  ✓ 添加需求:{req.id} (high)")

    def test_add_goal_feature_risk_question(self):
        state = MeetingState()
        g = state.add_goal("碳中和目标")
        f = state.add_feature("可视化看板")
        r = state.add_risk("排放因子来源不确定", severity=Priority.HIGH)
        q = state.add_question("是否支持 Scope 3?", is_urgent=True)

        assert g.id.startswith("GOAL-")
        assert f.id.startswith("FEAT-")
        assert r.id.startswith("RISK-")
        assert q.id.startswith("QUE-")
        assert q.is_urgent is True
        print(f"  ✓ 添加 4 类:GOAL/FEAT/RISK/QUE")

    def test_confirm_item(self):
        state = MeetingState()
        req = state.add_requirement("测试需求", priority=Priority.MEDIUM)
        confirmed = state.confirm_item("requirement", req.id, speaker_name="张总")
        assert confirmed.status == ItemStatus.CONFIRMED
        assert confirmed.speaker_name == "张总"
        print(f"  ✓ 确认需求:{confirmed.id} (speaker=张总)")

    def test_reject_item(self):
        state = MeetingState()
        req = state.add_requirement("测试需求")
        rejected = state.reject_item("requirement", req.id)
        assert rejected.status == ItemStatus.REJECTED
        print(f"  ✓ 拒绝需求:{rejected.id}")

    def test_list_pending_sort_by_priority(self):
        state = MeetingState()
        state.add_requirement("Low 需求", priority=Priority.LOW)
        state.add_requirement("High 需求", priority=Priority.HIGH)
        state.add_requirement("Medium 需求", priority=Priority.MEDIUM)
        state.add_goal("Low 目标", priority=Priority.LOW)
        state.confirm_item("requirement",
                          [r.id for r in state.requirements if r.text == "Medium 需求"][0])

        pending = state.list_pending()
        # 高优先级排前面
        assert pending[0].priority == Priority.HIGH
        # Medium 已被确认,不在 pending
        assert all(it.priority != Priority.MEDIUM for it in pending)
        print(f"  ✓ list_pending 按优先级排序(高->低),共 {len(pending)} 项")

    def test_speaker_map(self):
        state = MeetingState()
        state.register_speaker("u001", "张总")
        state.register_speaker("u002", "李经理")
        assert state.speaker_map["u001"] == "张总"
        assert state.speaker_map["u002"] == "李经理"
        print(f"  ✓ 说话人映射:{state.speaker_map}")

    def test_stats(self):
        state = MeetingState()
        state.add_requirement("R1")
        state.add_requirement("R2")
        state.add_goal("G1")
        state.add_risk("K1")
        state.add_question("Q1")
        state.add_question("Q2")
        stats = state.stats()
        assert stats["requirements"] == 2
        assert stats["goals"] == 1
        assert stats["risks"] == 1
        assert stats["open_questions"] == 2
        assert stats["total_pending"] == 6
        print(f"  ✓ 统计:{stats}")

    def test_find_item_not_found_raises(self):
        state = MeetingState()
        with pytest.raises(KeyError):
            state.confirm_item("requirement", "REQ-NOTEXIST")
        print(f"  ✓ 不存在的 ID 抛 KeyError")

    def test_find_item_wrong_type_raises(self):
        state = MeetingState()
        with pytest.raises(ValueError):
            state.confirm_item("unknown_type", "X-001")
        print(f"  ✓ 未知类型抛 ValueError")


class TestMeetingStorage:
    """持久化测试"""

    def test_save_and_load(self, tmp_storage):
        state = MeetingState(platform=Platform.TENCENT)
        state.add_requirement("测试需求 A", priority=Priority.HIGH)
        state.add_goal("测试目标 B")
        state.register_speaker("u1", "张总")

        tmp_storage.save(state)
        loaded = tmp_storage.load(state.meeting_id)
        assert loaded.meeting_id == state.meeting_id
        assert len(loaded.requirements) == 1
        assert loaded.requirements[0].text == "测试需求 A"
        assert loaded.requirements[0].priority == Priority.HIGH
        assert loaded.goals[0].text == "测试目标 B"
        assert loaded.speaker_map["u1"] == "张总"
        print(f"  ✓ 保存 + 加载:{state.meeting_id}")

    def test_load_nonexistent_raises(self, tmp_storage):
        with pytest.raises(StorageError):
            tmp_storage.load("NOTEXIST")
        print(f"  ✓ 加载不存在的会议抛 StorageError")

    def test_persistence_across_sessions(self, tmp_storage):
        """Step 1 关键验证:跨调用持久化"""
        # 会话 1:创建并保存
        meeting_id = "PERSIST-001"
        state = MeetingState(meeting_id=meeting_id)
        state.add_requirement("会话 1 添加的需求")
        state.add_goal("会话 1 添加的目标")
        tmp_storage.save(state)

        # 模拟"另一个进程/调用"——重新加载
        loaded = tmp_storage.load(meeting_id)
        loaded.add_risk("会话 2 添加的风险")
        loaded.confirm_item("requirement",
                            [r.id for r in loaded.requirements][0],
                            speaker_name="会话 2 的 VP")
        tmp_storage.save(loaded)

        # 再加载
        final = tmp_storage.load(meeting_id)
        assert len(final.requirements) == 1
        assert len(final.goals) == 1
        assert len(final.risks) == 1
        assert final.requirements[0].speaker_name == "会话 2 的 VP"
        print(f"  ✓ 跨调用持久化:{meeting_id}")

    def test_list_meetings(self, tmp_storage):
        for i in range(3):
            state = MeetingState(meeting_id=f"LIST-{i:03d}")
            state.add_requirement(f"需求 {i}")
            tmp_storage.save(state)
        meetings = tmp_storage.list_meetings()
        assert len(meetings) == 3
        assert all(m.startswith("LIST-") for m in meetings)
        print(f"  ✓ 列出 {len(meetings)} 个会议:{meetings}")

    def test_delete(self, tmp_storage):
        state = MeetingState(meeting_id="DEL-001")
        tmp_storage.save(state)
        assert tmp_storage.exists("DEL-001")
        assert tmp_storage.delete("DEL-001")
        assert not tmp_storage.exists("DEL-001")
        assert not tmp_storage.delete("DEL-001")
        print(f"  ✓ 删除会议:DEL-001")


class TestEndToEnd:
    """完整流程(模拟真实会议场景)"""

    def test_full_meeting_flow(self, tmp_storage):
        """模拟一场会议:开始 → 累积 → 确认 → 跨调用读"""
        # 1. VP 加入会议
        state = MeetingState(
            meeting_id="ESG-2026-001",
            platform=Platform.LOCAL,
            project_name="XX公司-ESG碳管理系统需求沟通会"
        )

        # 2. 客户发言 → AI 累积需求
        state.add_requirement(
            "碳排放数据统一管理",
            priority=Priority.HIGH,
            speaker_id="u_client",
            source_segment_id="seg-001"
        )
        state.add_requirement(
            "组织/区域/工厂多层统计",
            priority=Priority.HIGH,
            speaker_id="u_client",
            source_segment_id="seg-001"
        )
        state.add_goal("碳中和目标")

        # 3. VP 说话人映射
        state.register_speaker("u_client", "张总")
        state.register_speaker("u_vp", "李经理")

        # 4. VP 在窗口中确认某些需求
        req0 = state.requirements[0]
        state.confirm_item("requirement", req0.id, speaker_name="张总")

        # 5. AI 提出疑问
        state.add_question("排放因子来源是?", is_urgent=True)

        # 6. 保存
        tmp_storage.save(state)

        # 7. 会话结束 → VPBuddy 重新加载
        loaded = tmp_storage.load("ESG-2026-001")

        # 8. 验证
        assert loaded.platform == Platform.LOCAL
        assert loaded.project_name == "XX公司-ESG碳管理系统需求沟通会"
        assert len(loaded.requirements) == 2
        assert loaded.requirements[0].status == ItemStatus.CONFIRMED
        assert loaded.requirements[0].speaker_name == "张总"
        assert len(loaded.goals) == 1
        assert len(loaded.open_questions) == 1
        assert loaded.open_questions[0].is_urgent is True
        assert loaded.speaker_map["u_client"] == "张总"

        stats = loaded.stats()
        print(f"  ✓ 完整会议流程测试通过")
        print(f"    项目:{loaded.project_name}")
        print(f"    统计:{stats}")


if __name__ == "__main__":
    print("=" * 60)
    print("MVP Step 1:MeetingState 测试")
    print("=" * 60)
    sys.exit(pytest.main([__file__, "-v", "--tb=short"]))
