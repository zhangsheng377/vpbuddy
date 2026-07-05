"""experience — 经验蒸馏数据模型 (v0.9.0 #1 Phase 1)

设计:
- ExperienceItem: 从会议过程中提炼的可复用经验
- 会议结束时自动从 MeetingState 提取候选
- 后续会议生成前检索注入
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Literal


class ExperienceKind(str, Enum):
    DOMAIN_FACT = "domain_fact"           # 领域事实 (如"物理实验产品需考虑单位系统")
    PRODUCT_PATTERN = "product_pattern"   # 产品模式 (如"科研数据平台 = 采集+清洗+求解+可视化+溯源")
    DECISION_RULE = "decision_rule"       # 决策规则 (如"风险分析必须覆盖数据可重复性")
    TERMINOLOGY = "terminology"           # 术语 (如"某客户用'交付物'指代所有输出文件")
    FAILURE_LESSON = "failure_lesson"     # 失败教训 (如"跳过边界条件审核导致返工")
    USER_PREFERENCE = "user_preference"   # 用户偏好 (如"VP 喜欢风险先列高再列中低")


class ExperienceItem:
    """一条经验条目.

    从会议 MeetingState (requirements/goals/features/risks/questions) +
    KB 上传文件 + chat 历史中自动提取, 写入 data/experiences/ 目录.
    只有 approved=True 的条目才会被后续会议检索注入.
    """

    def __init__(
        self,
        kind: ExperienceKind | str,
        text: str,
        source_meeting_id: str,
        domain: str | None = None,
        product_type: str | None = None,
        evidence: list[str] | None = None,
        confidence: float = 0.5,
        approved: bool = False,
        item_id: str | None = None,
        created_at: str | None = None,
    ):
        self.id = item_id or f"exp-{uuid.uuid4().hex[:12]}"
        self.kind = ExperienceKind(kind) if isinstance(kind, str) else kind
        self.text = text
        self.domain = domain
        self.product_type = product_type
        self.evidence = evidence or []
        self.source_meeting_id = source_meeting_id
        self.confidence = min(max(confidence, 0.0), 1.0)
        self.approved = approved
        self.created_at = created_at or datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "text": self.text,
            "domain": self.domain,
            "product_type": self.product_type,
            "evidence": self.evidence,
            "source_meeting_id": self.source_meeting_id,
            "confidence": self.confidence,
            "approved": self.approved,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> ExperienceItem:
        return cls(
            kind=d["kind"],
            text=d["text"],
            source_meeting_id=d["source_meeting_id"],
            domain=d.get("domain"),
            product_type=d.get("product_type"),
            evidence=d.get("evidence", []),
            confidence=d.get("confidence", 0.5),
            approved=d.get("approved", False),
            item_id=d.get("id"),
            created_at=d.get("created_at"),
        )

    def __repr__(self) -> str:
        return f"<ExperienceItem {self.kind.value} confidence={self.confidence:.1f}>"


def guess_domain_from_meeting(
    meeting_title: str,
    state_facts: list[str],
) -> str | None:
    """从会议标题和 state 事实中猜测领域.

    简单的基于关键词的启发式方法.
    """
    text = f"{meeting_title} {' '.join(state_facts)}".lower()

    domains = {
        "physics": ["物理", "实验", "仿真", "仪器", "传感器", "单位", "误差"],
        "fintech": ["金融", "支付", "风控", "交易", "账", "资金", "合规"],
        "healthcare": ["医疗", "患者", "临床", "诊断", "药品", "器械"],
        "ecommerce": ["电商", "商品", "库存", "订单", "物流", "促销"],
        "education": ["教育", "课程", "教学", "学习", "培训", "考试"],
        "manufacturing": ["制造", "产线", "质检", "供应链", "工业"],
        "saas": ["SaaS", "订阅", "租户", "多租户", "工作台"],
    }

    for domain, keywords in domains.items():
        if any(kw in text for kw in keywords):
            return domain

    return None


def guess_product_type(
    meeting_title: str,
    state_facts: list[str],
) -> str | None:
    """从会议内容猜测产品类型."""
    text = f"{meeting_title} {' '.join(state_facts)}".lower()

    types = {
        "data_platform": ["数据平台", "数据仓库", "ETL", "BI", "报表"],
        "api_service": ["API", "接口", "微服务", "网关"],
        "mobile_app": ["手机", "APP", "iOS", "Android", "小程序"],
        "web_app": ["网站", "门户", "后台", "管理端"],
        "iot_platform": ["IoT", "设备", "物联", "传感器"],
    }

    for ptype, keywords in types.items():
        if any(kw in text for kw in keywords):
            return ptype

    return None
