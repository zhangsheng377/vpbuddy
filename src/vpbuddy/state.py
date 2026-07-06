"""MeetingState — 会议结构化状态(单一可信源)

v1.14 修订:不再叫"状态机",就是一个普通 JSON 对象。
v1.16:5 类核心累积(requirements/goals/features/risks/open_questions)+ 6 项交付物锚点。
v0.12.0:CRUD 方法已删除(ingest.py 移除),字段保留向后兼容。

设计原则:
- 一个 JSON 对象,可读可写
- 跨调用持久化(存 NFS JSON 文件)
- Pydantic 验证类型
- 不在 system prompt 里(只给 VP 看)
- YAGNI:不引入状态机、不引入 workflow 框架
"""
from __future__ import annotations
import logging

from datetime import datetime, timezone

UTC = timezone.utc
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, Field


class Platform(str, Enum):
    """数据源平台(2026-06-21 ADR-0008: 默认 = VP 桌面客户端麦克风/系统音频 loopback,不是会议平台)

    真实架构见 ADR-0004 (VP 设备 loopback → PCM → Whisper + pyannote 自接)。
    其他会议平台保留作 YAGNI 历史(未来真有客户再用,目前默认 LOCAL)。
    """
    LOCAL = "local"          # 默认: VP 桌面客户端麦克风/系统音频 loopback (ADR-0004)
    TENCENT = "tencent"      # YAGNI 历史
    DINGTALK = "dingtalk"    # YAGNI 历史
    WECOM = "wecom"          # YAGNI 历史 (原 ZOOM 改名)
    # FEISHU 在 2026-06-21 ADR-0008 中删除 — 不再是真实路径


class AudioSourceKind(str, Enum):
    """2026-07-01 ADR-0021: 客户端音频源类型 (麦克风 / 内录 / 双轨).

    默认 MICROPHONE (兼容老客户端, 老 stream_start 不传 audio_source 时).
    """
    MICROPHONE = "microphone"   # 仅麦克风 (默认, 一期主流)
    LOOPBACK = "loopback"       # 仅系统内录 (需平台支持: Linux .monitor / macOS BlackHole / Windows WASAPI loopback)
    BOTH = "both"               # 双轨混合 (一期简化: 等权平均 mic+loopback)


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
    return datetime.now(UTC).isoformat()


def _new_id(prefix: str) -> str:
    """生成人类可读 ID(REQ-001, GOAL-001, ...)"""
    return f"{prefix}-{uuid4().hex[:6].upper()}"


class TrackedItem(BaseModel):
    """累积项的基类"""
    id: str = Field(default_factory=lambda: _new_id("ITEM"))
    text: str
    priority: Priority = Priority.MEDIUM
    status: ItemStatus = ItemStatus.PENDING
    speaker_id: str | None = None  # 谁说的(从 ASR 段带过来)
    speaker_name: str | None = None
    source_segment_id: str | None = None  # 来自哪个转写段
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)

    def confirm(self, speaker_name: str | None = None) -> None:
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

    5 类累积项(requirements/goals/features/risks/open_questions, 字段保留向后兼容)
    + 元数据(platform/speaker_map/last_updated)

    跨调用持久化:每次修改都立即落盘(NFS JSON)
    """
    # === 基础信息 ===
    meeting_id: str = Field(default_factory=lambda: uuid4().hex[:12].upper())
    platform: Platform = Platform.LOCAL  # 默认 LOCAL (VP 桌面客户端麦克风/系统音频, ADR-0004)
    audio_source: AudioSourceKind = AudioSourceKind.MICROPHONE  # 2026-07-01 ADR-0021: 麦克风/内录/双轨
    project_name: str | None = None
    started_at: str = Field(default_factory=_now)

    # === 累积项(单一可信源的核心) ===
    requirements: list[Requirement] = Field(default_factory=list)
    goals: list[Goal] = Field(default_factory=list)
    features: list[Feature] = Field(default_factory=list)
    risks: list[Risk] = Field(default_factory=list)
    open_questions: list[Question] = Field(default_factory=list)

    # === 清理后的完整会议转写文本 (v0.10.0: 替代旧 5 类 facts 作为 LLM 上下文) ===
    # append-only: 每次 ASR pass 追加, ingest.py 通过 asr_clean 写入
    cleaned_text: str = ""

    # === 元数据 ===
    speaker_map: dict[str, str] = Field(default_factory=dict)  # speaker_id -> speaker_name
    last_updated: str = Field(default_factory=_now)
    vpbuddy_version: str = ""  # 记录生成此 state 的 VPBuddy 版本 (动态获取)

    # Pydantic v2 模型初始化后钩子: 动态填充 vpbuddy_version
    def model_post_init(self, __context) -> None:
        if not self.vpbuddy_version:
            try:
                from vpbuddy import __version__
                object.__setattr__(self, "vpbuddy_version", __version__ or "0.1.0")
            except Exception:
                object.__setattr__(self, "vpbuddy_version", "0.1.0")

    # === 查询 ===
    def list_pending(self) -> list[TrackedItem]:
        """所有 pending 项(按紧急度排序:高优先级优先)"""
        all_items: list[TrackedItem] = (
            self.requirements + self.goals + self.features +
            self.risks + self.open_questions
        )
        priority_order = {Priority.HIGH: 0, Priority.MEDIUM: 1, Priority.LOW: 2}
        pending = [it for it in all_items if it.status == ItemStatus.PENDING]
        pending.sort(key=lambda it: priority_order[it.priority])
        return pending

    def stats(self) -> dict[str, int]:
        """基础统计"""
        return {
            "cleaned_text_length": len(self.cleaned_text),
        }

    # === 说话人映射 ===
    def register_speaker(self, speaker_id: str, speaker_name: str) -> None:
        self.speaker_map[speaker_id] = speaker_name
        self._touch()

    def _touch(self) -> None:
        """更新 last_updated 戳(每次修改都调用)"""
        self.last_updated = _now()
