"""MeetingState — 会议结构化状态(单一可信源)

v1.14 修订:不再叫"状态机",就是一个普通 JSON 对象。
v1.16:5 类核心累积(requirements/goals/features/risks/open_questions)+ 6 项交付物锚点。

设计原则:
- 一个 JSON 对象,可读可写
- 跨调用持久化(存 NFS JSON 文件)
- Pydantic 验证类型
- 不在 system prompt 里(只给 VP 看)
- YAGNI:不引入状态机、不引入 workflow 框架
"""
from __future__ import annotations
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


class Platform(str, Enum):
    """第一平台(ADR-0001):飞书;其他可选"""
    FEISHU = "feishu"
    TENCENT = "tencent"
    DINGTALK = "dingtalk"
    ZOOM = "zoom"


class Priority(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ItemStatus(str, Enum):
    """统一 3 态(v1.16):pending / confirmed / rejected"""
    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    """生成人类可读 ID(REQ-001, GOAL-001, ...)"""
    return f"{prefix}-{uuid4().hex[:6].upper()}"


class TrackedItem(BaseModel):
    """累积项的基类"""
    id: str = Field(default_factory=lambda: _new_id("ITEM"))
    text: str
    priority: Priority = Priority.MEDIUM
    status: ItemStatus = ItemStatus.PENDING
    speaker_id: Optional[str] = None  # 谁说的(从 ASR 段带过来)
    speaker_name: Optional[str] = None
    source_segment_id: Optional[str] = None  # 来自哪个转写段
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)

    def confirm(self, speaker_name: Optional[str] = None) -> None:
        self.status = ItemStatus.CONFIRMED
        if speaker_name:
            self.speaker_name = speaker_name
        self.updated_at = _now()

    def reject(self) -> None:
        self.status = ItemStatus.REJECTED
        self.updated_at = _now()


class Requirement(TrackedItem):
    """客户需求(REQ-001)+ 优先级 + 状态"""
    prefix: str = "REQ"
    id: str = Field(default_factory=lambda: _new_id("REQ"))


class Goal(TrackedItem):
    """业务目标(GOAL-001)"""
    prefix: str = "GOAL"
    id: str = Field(default_factory=lambda: _new_id("GOAL"))


class Feature(TrackedItem):
    """功能点(FEAT-001)"""
    prefix: str = "FEAT"
    id: str = Field(default_factory=lambda: _new_id("FEAT"))


class Risk(TrackedItem):
    """风险点(RISK-001)"""
    prefix: str = "RISK"
    severity: Priority = Priority.MEDIUM  # 复用 priority 字段表示严重度
    id: str = Field(default_factory=lambda: _new_id("RISK"))


class Question(TrackedItem):
    """待确认问题(QUE-001)"""
    prefix: str = "QUE"
    is_urgent: bool = False
    id: str = Field(default_factory=lambda: _new_id("QUE"))


class MeetingState(BaseModel):
    """会议结构化状态(单一可信源)

    5 类累积项(requirements/goals/features/risks/open_questions)
    + 元数据(platform/speaker_map/last_updated)

    跨调用持久化:每条 add/update 操作都立即落盘(NFS JSON)
    """
    # === 基础信息 ===
    meeting_id: str = Field(default_factory=lambda: uuid4().hex[:12].upper())
    platform: Platform = Platform.FEISHU
    project_name: Optional[str] = None
    started_at: str = Field(default_factory=_now)

    # === 累积项(单一可信源的核心) ===
    requirements: List[Requirement] = Field(default_factory=list)
    goals: List[Goal] = Field(default_factory=list)
    features: List[Feature] = Field(default_factory=list)
    risks: List[Risk] = Field(default_factory=list)
    open_questions: List[Question] = Field(default_factory=list)

    # === 元数据 ===
    speaker_map: Dict[str, str] = Field(default_factory=dict)  # speaker_id -> speaker_name
    last_updated: str = Field(default_factory=_now)
    vpbuddy_version: str = "0.1.0"  # 记录生成此 state 的 VPBuddy 版本

    # === CRUD:添加 ===
    def add_requirement(self, text: str, priority: Priority = Priority.MEDIUM,
                       speaker_id: Optional[str] = None,
                       source_segment_id: Optional[str] = None) -> Requirement:
        req = Requirement(text=text, priority=priority,
                          speaker_id=speaker_id,
                          source_segment_id=source_segment_id)
        self.requirements.append(req)
        self._touch()
        return req

    def add_goal(self, text: str, **kwargs) -> Goal:
        goal = Goal(text=text, **kwargs)
        self.goals.append(goal)
        self._touch()
        return goal

    def add_feature(self, text: str, **kwargs) -> Feature:
        feat = Feature(text=text, **kwargs)
        self.features.append(feat)
        self._touch()
        return feat

    def add_risk(self, text: str, **kwargs) -> Risk:
        risk = Risk(text=text, **kwargs)
        self.risks.append(risk)
        self._touch()
        return risk

    def add_question(self, text: str, is_urgent: bool = False, **kwargs) -> Question:
        q = Question(text=text, is_urgent=is_urgent, **kwargs)
        self.open_questions.append(q)
        self._touch()
        return q

    # === CRUD:更新 ===
    def confirm_item(self, item_type: str, item_id: str, speaker_name: Optional[str] = None) -> TrackedItem:
        item = self._find_item(item_type, item_id)
        item.confirm(speaker_name)
        self._touch()
        return item

    def reject_item(self, item_type: str, item_id: str) -> TrackedItem:
        item = self._find_item(item_type, item_id)
        item.reject()
        self._touch()
        return item

    def _find_item(self, item_type: str, item_id: str) -> TrackedItem:
        """按类型 + ID 找累积项"""
        collection_map = {
            "requirement": self.requirements,
            "goal": self.goals,
            "feature": self.features,
            "risk": self.risks,
            "question": self.open_questions,
        }
        if item_type not in collection_map:
            raise ValueError(f"Unknown item_type: {item_type}. "
                             f"Must be one of {list(collection_map.keys())}")
        for item in collection_map[item_type]:
            if item.id == item_id:
                return item
        raise KeyError(f"{item_type} {item_id} not found")

    # === 查询 ===
    def list_pending(self) -> List[TrackedItem]:
        """所有 pending 项(按紧急度排序:高优先级优先)"""
        all_items: List[TrackedItem] = (
            self.requirements + self.goals + self.features +
            self.risks + self.open_questions
        )
        priority_order = {Priority.HIGH: 0, Priority.MEDIUM: 1, Priority.LOW: 2}
        pending = [it for it in all_items if it.status == ItemStatus.PENDING]
        pending.sort(key=lambda it: priority_order[it.priority])
        return pending

    def stats(self) -> Dict[str, int]:
        """统计各类型数量 + 状态分布"""
        return {
            "requirements": len(self.requirements),
            "goals": len(self.goals),
            "features": len(self.features),
            "risks": len(self.risks),
            "open_questions": len(self.open_questions),
            "total_pending": len(self.list_pending()),
        }

    # === 说话人映射 ===
    def register_speaker(self, speaker_id: str, speaker_name: str) -> None:
        self.speaker_map[speaker_id] = speaker_name
        self._touch()

    def _touch(self) -> None:
        """更新 last_updated 戳(每次修改都调用)"""
        self.last_updated = _now()
