"""platforms 多平台适配器测试 (2026-06-21 ADR-0008: 删 FeishuAdapter)

覆盖:
- 3 平台都能 get_adapter (tencent / dingtalk / wecom)
- meta 字段完整
- capabilities 列出
- 抽象方法默认 raise NotImplementedError
- 不支持的平台抛 ValueError
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from vpbuddy.platforms import (
    SUPPORTED_PLATFORMS,
    Capability,
    TencentAdapter,
    DingTalkAdapter,
    WeComAdapter,
    PlatformAdapter,
    get_adapter,
    list_supported,
)


@pytest.mark.parametrize("platform,expected_class", [
    ("tencent", TencentAdapter),
    ("dingtalk", DingTalkAdapter),
    ("wecom", WeComAdapter),
])
def test_get_adapter_returns_correct_type(platform, expected_class):
    """每个平台应该返回对应的 adapter"""
    adapter = get_adapter(platform)
    assert isinstance(adapter, expected_class)
    assert isinstance(adapter, PlatformAdapter)


def test_get_adapter_unsupported():
    """不支持的平台应该抛 ValueError"""
    with pytest.raises(ValueError) as exc:
        get_adapter("feishu")  # 2026-06-21 ADR-0008 删除
    assert "Unsupported platform" in str(exc.value)


def test_list_supported():
    """list_supported 应该返回 3 平台 (2026-06-21 ADR-0008 删飞书)"""
    platforms = list_supported()
    assert set(platforms) == {"tencent", "dingtalk", "wecom"}


def test_supported_platforms_constant():
    """SUPPORTED_PLATFORMS 应该 = list_supported()"""
    assert set(SUPPORTED_PLATFORMS) == set(list_supported())


def test_all_adapters_have_capabilities():
    """3 平台都应该有 ≥ 1 能力"""
    for platform in SUPPORTED_PLATFORMS:
        adapter = get_adapter(platform)
        assert len(adapter.list_capabilities()) >= 1, f"{platform} 无能力"


def test_capability_dataclass():
    """Capability 数据类应该工作"""
    cap = Capability(name="test", description="desc", implemented=True)
    assert cap.name == "test"
    assert cap.implemented is True


def test_fetch_transcript_raises_by_default():
    """默认 fetch_transcript 应该 raise (YAGNI)"""
    for platform in SUPPORTED_PLATFORMS:
        adapter = get_adapter(platform)
        with pytest.raises(NotImplementedError):
            adapter.fetch_transcript("test_meeting")


def test_list_recent_meetings_raises_by_default():
    """默认 list_recent_meetings 应该 raise (YAGNI)"""
    for platform in SUPPORTED_PLATFORMS:
        adapter = get_adapter(platform)
        with pytest.raises(NotImplementedError):
            adapter.list_recent_meetings()


def test_each_platform_has_unique_api_base():
    """3 平台 API 端点应该不同"""
    bases = [get_adapter(p).meta.api_base for p in SUPPORTED_PLATFORMS]
    assert len(set(bases)) == 3, "API 端点不应该重复"