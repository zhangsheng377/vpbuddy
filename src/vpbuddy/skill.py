"""VPBuddy skill — Hermes skill 协议入口(ADR-0009)

Hermes 通过 `[project.entry-points."hermes.skills"]` 发现本 skill,
运行时调到 `VPBuddySkill` 类。本类封装 VPBuddy 全部能力,
提供 Hermes-friendly API(简单方法,不带复杂状态管理)。

设计原则(ADR-0009 + Hermes skill_manage 规范):
- 单实例 — Hermes 进程内只注册一个 VPBuddy skill
- 委托 — 实际逻辑在 MeetingState / storage / engine / knowledge_base
- 不持长连接 — 每次调用开新 Pydantic 验证后立即返回
- 无 I/O 副作用(除了主动触发的派生) — 不偷偷写文件/发消息

用法(开发者直接 import):
    from vpbuddy.skill import VPBuddySkill
    skill = VPBuddySkill()
    meetings = skill.list_meetings()
    state = skill.get_state("PHASE2_TEST")
"""
from __future__ import annotations
from typing import Dict, List, Optional

from . import __version__
from .state import MeetingState
from .storage import MeetingStorage
from .sub_session_controller import (
    list_active_meetings,
    trigger_sub_session,
    DOC_KINDS,
)


class VPBuddySkill:
    """VPBuddy skill — Hermes 协议入口(ADR-0009)

    Hermes 调用约定(参考 hermes-cli skill_manage):
    - 类名 = 入口点配置(vpbuddy.skill:VPBuddySkill)
    - 实例化无参
    - 方法在 Hermes 进程内同步调用;长时间任务交给 sub_session_controller
    """

    NAME = "vpbuddy"
    VERSION = __version__

    def __init__(self) -> None:
        self._storage: Optional[MeetingStorage] = None

    @property
    def storage(self) -> MeetingStorage:
        """延迟初始化存储(YAGNI:不预加载 state)"""
        if self._storage is None:
            from pathlib import Path
            import os
            data_dir = Path(os.environ.get("VPBUDDY_DATA_DIR", "/home/zsd/vpbuddy/data/meetings"))
            self._storage = MeetingStorage(data_dir=str(data_dir))
        return self._storage

    # === Hermes 调用的方法(skill 协议) ===

    def status(self) -> Dict:
        """VPBuddy runtime 状态 — Hermes 启动时调用,确认 skill 可用"""
        return {
            "name": self.NAME,
            "version": self.VERSION,
            "available": True,
            "doc_kinds": DOC_KINDS,
            "meetings_count": len(list_active_meetings()),
        }

    def list_meetings(self) -> List[str]:
        """列出活跃会议 ID(Hermes 用于 cron 决策)"""
        return list_active_meetings()

    def get_state(self, meeting_id: str) -> Optional[Dict]:
        """读取会议状态(返回 dict 给 Hermes,不是 Pydantic)"""
        try:
            state = self.storage.load(meeting_id)
            return state.model_dump(mode="json")
        except FileNotFoundError:
            return None

    def trigger_doc(self, meeting_id: str, doc_kind: str, dry_run: bool = False) -> Dict:
        """触发单个 doc_kind 生成(controller 的一部分,Hermes 可以直接调)

        Args:
            meeting_id: 会议 ID
            doc_kind: req/arch/tasks/api/risk/demo
            dry_run: True = 只渲染 prompt 不触发 LLM
        """
        return trigger_sub_session(meeting_id, doc_kind, dry_run=dry_run)

    # === 元信息(给 Hermes skill discovery) ===

    def __repr__(self) -> str:
        return f"<VPBuddySkill v{self.VERSION}>"


__all__ = ["VPBuddySkill"]