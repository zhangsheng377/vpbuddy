"""platforms 多平台适配器测试

覆盖:
- 4 平台都能 get_adapter
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
    FeishuAdapter,
    TencentAdapter,
    DingTalkAdapter,
    WeComAdapter,
    PlatformAdapter,
    get_adapter,
    list_supported,
)


@pytest.mark.parametrize("platform,expected_class", [
    ("feishu", FeishuAdapter),
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
        get_adapter("zoom")  # 之前 state.py 有,但 platforms 没
    assert "Unsupported platform" in str(exc.value)


def test_list_supported():
    """list_supported 应该返回 4 平台"""
    platforms = list_supported()
    assert set(platforms) == {"feishu", "tencent", "dingtalk", "wecom"}


def test_supported_platforms_constant():
    """SUPPORTED_PLATFORMS 应该 = list_supported()"""
    assert set(SUPPORTED_PLATFORMS) == set(list_supported())


def test_feishu_meta_complete():
    """飞书 meta 应该包含完整字段"""
    adapter = get_adapter("feishu")
    meta = adapter.meta
    assert meta.platform == "feishu"
    assert "飞书" in meta.display_name
    assert meta.api_base.startswith("https://")
    assert meta.docs_url.startswith("https://")
    assert meta.free_tier
    assert len(meta.capabilities) > 0


def test_all_adapters_have_capabilities():
    """4 平台都应该有 ≥ 1 能力"""
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


def test_feishu_capabilities_have_minutes():
    """飞书应该有 minutes 相关能力"""
    adapter = get_adapter("feishu")
    caps = adapter.list_capabilities()
    cap_names = [c.name for c in caps]
    assert "minutes.fetch" in cap_names


def test_each_platform_has_unique_api_base():
    """4 平台 API 端点应该不同"""
    bases = [get_adapter(p).meta.api_base for p in SUPPORTED_PLATFORMS]
    assert len(set(bases)) == 4, "API 端点不应该重复"
