"""测试 experience — ExperienceItem / ExperienceKind / 领域猜测 (v0.9.0 #1 Phase 1)

覆盖:
- ExperienceItem 创建 + to_dict() / from_dict() 序列化/反序列化
- 6 种 ExperienceKind 枚举值
- guess_domain_from_meeting() 关键词匹配 (物理/金融/医疗/电商/教育/制造/SaaS)
- guess_product_type() 关键词匹配
- confidence 边界 (0-1 裁剪)
- item_id 自动生成
"""

from __future__ import annotations

import sys
import re
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from vpbuddy.experience import (
    ExperienceItem,
    ExperienceKind,
    guess_domain_from_meeting,
    guess_product_type,
)


class TestExperienceKind:
    """ExperienceKind 枚举."""

    def test_all_6_kinds(self):
        """枚举应有 6 种类型."""
        kinds = list(ExperienceKind)
        assert len(kinds) == 6
        names = {k.name for k in kinds}
        assert names == {
            "DOMAIN_FACT",
            "PRODUCT_PATTERN",
            "DECISION_RULE",
            "TERMINOLOGY",
            "FAILURE_LESSON",
            "USER_PREFERENCE",
        }

    def test_kind_values(self):
        assert ExperienceKind.DOMAIN_FACT.value == "domain_fact"
        assert ExperienceKind.PRODUCT_PATTERN.value == "product_pattern"
        assert ExperienceKind.DECISION_RULE.value == "decision_rule"
        assert ExperienceKind.TERMINOLOGY.value == "terminology"
        assert ExperienceKind.FAILURE_LESSON.value == "failure_lesson"
        assert ExperienceKind.USER_PREFERENCE.value == "user_preference"


class TestExperienceItem:
    """ExperienceItem 数据模型."""

    def test_create_minimal(self):
        """最少字段创建."""
        item = ExperienceItem(
            kind="domain_fact",
            text="物理实验需考虑单位系统",
            source_meeting_id="mtg_001",
        )
        assert item.kind == ExperienceKind.DOMAIN_FACT
        assert item.text == "物理实验需考虑单位系统"
        assert item.source_meeting_id == "mtg_001"
        assert item.domain is None
        assert item.product_type is None
        assert item.evidence == []
        assert item.confidence == 0.5
        assert item.approved is False
        assert item.id.startswith("exp-")

    def test_id_auto_generated(self):
        """item_id 不传应自动生成 exp- 前缀."""
        item1 = ExperienceItem(kind="domain_fact", text="a", source_meeting_id="m1")
        item2 = ExperienceItem(kind="domain_fact", text="b", source_meeting_id="m1")
        assert item1.id.startswith("exp-")
        assert item2.id.startswith("exp-")
        assert item1.id != item2.id

    def test_id_custom(self):
        """传入 item_id 应精确使用."""
        item = ExperienceItem(
            kind="domain_fact", text="a", source_meeting_id="m1",
            item_id="my-custom-id",
        )
        assert item.id == "my-custom-id"

    def test_confidence_clamped_low(self):
        """confidence 低于 0 应裁剪为 0."""
        item = ExperienceItem(
            kind="domain_fact", text="a", source_meeting_id="m1",
            confidence=-0.5,
        )
        assert item.confidence == 0.0

    def test_confidence_clamped_high(self):
        """confidence 高于 1 应裁剪为 1."""
        item = ExperienceItem(
            kind="domain_fact", text="a", source_meeting_id="m1",
            confidence=1.5,
        )
        assert item.confidence == 1.0

    def test_confidence_normal(self):
        """正常 confidence 保持不变."""
        item = ExperienceItem(
            kind="domain_fact", text="a", source_meeting_id="m1",
            confidence=0.7,
        )
        assert item.confidence == 0.7

    def test_to_dict(self):
        """to_dict() 应返回完整 dict."""
        item = ExperienceItem(
            kind="failure_lesson",
            text="跳过边界条件审核导致返工",
            source_meeting_id="mtg_002",
            domain="manufacturing",
            product_type="data_platform",
            evidence=["RISK: 边界条件未审核"],
            confidence=0.8,
            approved=True,
        )
        d = item.to_dict()
        assert d["id"] == item.id
        assert d["kind"] == "failure_lesson"
        assert d["text"] == "跳过边界条件审核导致返工"
        assert d["source_meeting_id"] == "mtg_002"
        assert d["domain"] == "manufacturing"
        assert d["product_type"] == "data_platform"
        assert d["evidence"] == ["RISK: 边界条件未审核"]
        assert d["confidence"] == 0.8
        assert d["approved"] is True
        assert "created_at" in d

    def test_from_dict(self):
        """from_dict() 应恢复完整对象."""
        original = ExperienceItem(
            kind="user_preference",
            text="VP 喜欢风险先列高再列中低",
            source_meeting_id="mtg_003",
            domain="saas",
            product_type="web_app",
            evidence=["偏好说明"],
            confidence=0.6,
            approved=True,
        )
        d = original.to_dict()
        restored = ExperienceItem.from_dict(d)
        assert restored.id == original.id
        assert restored.kind == original.kind
        assert restored.text == original.text
        assert restored.domain == original.domain
        assert restored.product_type == original.product_type
        assert restored.evidence == original.evidence
        assert restored.confidence == original.confidence
        assert restored.approved == original.approved
        assert restored.source_meeting_id == original.source_meeting_id
        assert restored.created_at == original.created_at

    def test_from_dict_minimal(self):
        """from_dict() 最少字段应填默认值."""
        d = {
            "kind": "terminology",
            "text": "客户用'交付物'指代所有输出文件",
            "source_meeting_id": "mtg_004",
        }
        item = ExperienceItem.from_dict(d)
        assert item.kind == ExperienceKind.TERMINOLOGY
        assert item.domain is None
        assert item.product_type is None
        assert item.evidence == []
        assert item.confidence == 0.5
        assert item.approved is False
        assert item.id is not None

    def test_str_kind_acceptance(self):
        """构造函数接受 str 而不是 ExperienceKind."""
        item = ExperienceItem(kind="product_pattern", text="p", source_meeting_id="m")
        assert item.kind == ExperienceKind.PRODUCT_PATTERN

    def test_repr(self):
        """__repr__ 应有可读性."""
        item = ExperienceItem(kind="domain_fact", text="t", source_meeting_id="m", confidence=0.9)
        r = repr(item)
        assert "domain_fact" in r
        assert "0.9" in r


class TestGuessDomain:
    """guess_domain_from_meeting."""

    def test_physics_match(self):
        """物理关键词触发 physics 领域."""
        domain = guess_domain_from_meeting("物理实验需求沟通", [])
        assert domain == "physics"

    def test_physics_from_facts(self):
        """从 facts 中触发 physics."""
        domain = guess_domain_from_meeting("产品方案", ["需要高精度传感器", "误差分析"])
        assert domain == "physics"

    def test_fintech_match(self):
        domain = guess_domain_from_meeting("支付系统迁移", ["合规要求"])
        assert domain == "fintech"

    def test_healthcare_match(self):
        domain = guess_domain_from_meeting("患者管理平台", [])
        assert domain == "healthcare"

    def test_ecommerce_match(self):
        domain = guess_domain_from_meeting("商品库存优化", ["促销活动设计"])
        assert domain == "ecommerce"

    def test_education_match(self):
        domain = guess_domain_from_meeting("在线考试系统", [])
        assert domain == "education"

    def test_manufacturing_match(self):
        domain = guess_domain_from_meeting("产线质检流程", [])
        assert domain == "manufacturing"

    def test_saas_match(self):
        domain = guess_domain_from_meeting("SaaS 多租户工作台", [])
        assert domain == "saas"

    def test_no_match(self):
        """无匹配关键词返回 None."""
        domain = guess_domain_from_meeting("随便聊聊", ["无关内容"])
        assert domain is None

    def test_empty_input(self):
        """空输入返回 None."""
        domain = guess_domain_from_meeting("", [])
        assert domain is None

    def test_healthcare_match(self):
        """医疗关键词触发 healthcare 领域."""
        domain = guess_domain_from_meeting("患者管理系统需求评审", [])
        assert domain == "healthcare"


class TestGuessProductType:
    """guess_product_type."""

    def test_data_platform_match(self):
        ptype = guess_product_type("数据仓库建设", [])
        assert ptype == "data_platform"

    def test_api_service_match(self):
        ptype = guess_product_type("API 网关设计", [])
        assert ptype == "api_service"

    def test_mobile_app_match(self):
        ptype = guess_product_type("手机APP开发项目", [])
        assert ptype == "mobile_app"

    def test_web_app_match(self):
        ptype = guess_product_type("后台管理端", [])
        assert ptype == "web_app"

    def test_iot_platform_match(self):
        ptype = guess_product_type("IoT 设备管理", [])
        assert ptype == "iot_platform"

    def test_no_match(self):
        ptype = guess_product_type("日常讨论", [])
        assert ptype is None

    def test_empty_input(self):
        ptype = guess_product_type("", [])
        assert ptype is None

    def test_match_from_facts(self):
        """从 facts 匹配产品类型."""
        ptype = guess_product_type("项目方案", ["ETL 流程设计", "BI 报表看板"])
        assert ptype == "data_platform"
