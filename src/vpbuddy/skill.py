"""skill module"""
from __future__ import annotations


# Auto-computed project root. P1#1 (2026-07-04)
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent




from . import __version__
from .storage import MeetingStorage
from .sub_session_controller import (
    list_active_meetings,
    BATCH_DOCS_KIND,
    DEMO_KIND,
)
from .sub_session_controller import _dispatch_kind


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
        self._storage: MeetingStorage | None = None

    @property
    def storage(self) -> MeetingStorage:
        """延迟初始化存储(YAGNI:不预加载 state)"""
        if self._storage is None:
            import os
            from pathlib import Path
            self._storage = MeetingStorage(data_dir=str(data_dir))
        return self._storage

    # === Hermes 调用的方法(skill 协议) ===

    def status(self) -> dict:
        """VPBuddy runtime 状态 — Hermes 启动时调用,确认 skill 可用"""
        return {
            "name": self.NAME,
            "version": self.VERSION,
            "available": True,
            "scheduled_kinds": [BATCH_DOCS_KIND, DEMO_KIND],
            "meetings_count": len(list_active_meetings()),
        }

    def list_meetings(self) -> list[str]:
        """列出活跃会议 ID(Hermes 用于 cron 决策)"""
        return list_active_meetings()

    def get_state(self, meeting_id: str) -> dict | None:
        """读取会议状态(返回 dict 给 Hermes,不是 Pydantic)"""
        try:
            state = self.storage.load(meeting_id)
            return state.model_dump(mode="json")
        except FileNotFoundError:
            return None

    def trigger_doc(self, meeting_id: str, doc_kind: str, dry_run: bool = False) -> dict:
        """触发 doc_kind 生成 (ADR-0029: batch_docs + demo)

        Args:
            meeting_id: 会议 ID
            doc_kind: batch_docs / demo (旧 req/arch/tasks/api/risk → 返回 deprecated 警告)
            dry_run: True = 只渲染 prompt 不触发 LLM
        """
        return _dispatch_kind(meeting_id, doc_kind, dry_run=dry_run)

    # === 元信息(给 Hermes skill discovery) ===

    def __repr__(self) -> str:
        return f"<VPBuddySkill v{self.VERSION}>"


__all__ = ["VPBuddySkill"]
