"""多平台适配器 — Step 6

设计原则(ADR-0007 — YAGNI):
- 4 平台共享同一接口 PlatformAdapter
- 飞书/腾讯/钉钉/企微 各一个 adapter
- 第一版只实现 metadata(平台能力 + 配置),transcript 留 TODO
- 主 session 调用 adapter.list_capabilities() 知道该平台支持什么

典型用法:
    from vpbuddy.platforms import get_adapter
    adapter = get_adapter("feishu")
    caps = adapter.list_capabilities()
    for cap in caps:
        print(f"{cap.name}: {cap.description}")
"""
from __future__ import annotations
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional

# 4 平台标识
SUPPORTED_PLATFORMS = ["tencent", "dingtalk", "wecom"]


@dataclass
class Capability:
    """平台能力描述"""
    name: str  # e.g. "transcript.fetch"
    description: str
    implemented: bool = False  # MVP 是否实现


@dataclass
class PlatformMeta:
    """平台元信息"""
    platform: str
    display_name: str
    vendor: str
    api_base: str
    auth_method: str  # oauth / app_credentials / webhook
    docs_url: str
    free_tier: str  # "300min/user/month" 等
    capabilities: List[Capability] = field(default_factory=list)


class PlatformAdapter(ABC):
    """平台适配器基类"""

    @property
    @abstractmethod
    def meta(self) -> PlatformMeta:
        ...

    def list_capabilities(self) -> List[Capability]:
        return self.meta.capabilities

    # === 抽象方法(MVP 阶段只列接口,不实现) ===

    def fetch_transcript(self, meeting_id: str) -> List[dict]:
        """拉取会议转写

        Returns: [{"start": 0.0, "end": 5.0, "speaker": "SPK_00", "text": "..."}]
        YAGNI: 默认 raise NotImplementedError
        """
        raise NotImplementedError(
            f"{self.meta.platform} fetch_transcript 暂未实现, "
            f"需先在飞书/腾讯会议/钉钉/企微 后台开通转写 API"
        )

    def list_recent_meetings(self, limit: int = 10) -> List[dict]:
        """列出最近会议(YAGNI: 默认未实现)"""
        raise NotImplementedError(f"{self.meta.platform} list_recent_meetings 暂未实现")


# === 4 平台具体实现 ===

    def meta(self) -> PlatformMeta:
        return PlatformMeta(
            platform="feishu",
            display_name="飞书 (Lark)",
            vendor="字节跳动 (ByteDance)",
            api_base="https://open.feishu.cn/open-apis",
            auth_method="app_credentials",
            docs_url="https://open.feishu.cn/document/server-docs/minutes-v1/minute/get",
            free_tier="妙记 300 分钟/用户/月 + API 1 万次/租户/月",
            capabilities=[
                Capability("minutes.fetch", "拉取妙记 metadata (会议标题/时间/参与人)", implemented=True),
                Capability("minutes.transcript", "拉取妙记完整逐字稿(段级时间戳+说话人)", implemented=False),
                Capability("minutes.summary", "拉取妙记 AI 总结(章节纪要+待办)", implemented=False),
                # 注意: 不实现 chat.send — VPBuddy 输出是文件,不发消息
            ],
        )


class TencentAdapter(PlatformAdapter):
    """腾讯会议适配器

    - 妙记 = 腾讯会议录制云端转写
    - API: meeting.tencent.com (开放平台)
    - Auth: 应用凭证 (sdk_app_id + secret_key) → AccessToken
    - 文档: https://cloud.tencent.com/document/product/1095
    """
    @property
    def meta(self) -> PlatformMeta:
        return PlatformMeta(
            platform="tencent",
            display_name="腾讯会议",
            vendor="腾讯 (Tencent)",
            api_base="https://api.meeting.qq.com",
            auth_method="app_credentials",
            docs_url="https://cloud.tencent.com/document/product/1095",
            free_tier="基础版 1000 分钟/月, 商业版无限",
            capabilities=[
                Capability("meeting.list", "列出企业会议", implemented=False),
                Capability("meeting.detail", "查询会议详情", implemented=False),
                Capability("minutes.fetch", "拉取会议云录制转写", implemented=False),
                Capability("minutes.transcript", "拉取逐字稿(段级)", implemented=False),
                Capability("minutes.summary", "拉取 AI 智能总结", implemented=False),
            ],
        )


class DingTalkAdapter(PlatformAdapter):
    """钉钉适配器

    - 智能会议 = 钉钉云端会议转写
    - API: oapi.dingtalk.com (开放平台)
    - Auth: 应用凭证 (AppKey + AppSecret) → access_token
    - 文档: https://open.dingtalk.com/document/orgapp
    """
    @property
    def meta(self) -> PlatformMeta:
        return PlatformMeta(
            platform="dingtalk",
            display_name="钉钉",
            vendor="阿里 (Alibaba)",
            api_base="https://oapi.dingtalk.com",
            auth_method="app_credentials",
            docs_url="https://open.dingtalk.com/document/orgapp",
            free_tier="基础版免费, AI 能力按调用计费",
            capabilities=[
                Capability("meeting.list", "列出企业会议", implemented=False),
                Capability("minutes.fetch", "拉取钉钉智能会议转写", implemented=False),
                Capability("minutes.transcript", "拉取逐字稿", implemented=False),
                # 注意: 不实现 chat.send — VPBuddy 输出是文件
            ],
        )


class WeComAdapter(PlatformAdapter):
    """企业微信适配器

    - 会议转写 = 企业微信会议录制
    - API: qyapi.weixin.qq.com (企业微信 API)
    - Auth: 应用凭证 (CorpID + CorpSecret) → access_token
    - 文档: https://developer.work.weixin.qq.com/document/path/91039
    """
    @property
    def meta(self) -> PlatformMeta:
        return PlatformMeta(
            platform="wecom",
            display_name="企业微信 (WeCom)",
            vendor="腾讯 (Tencent)",
            api_base="https://qyapi.weixin.qq.com/cgi-bin",
            auth_method="app_credentials",
            docs_url="https://developer.work.weixin.qq.com/document/path/91039",
            free_tier="基础 API 免费, 接口许可 5 元/账号/年(互通 50 元)",
            capabilities=[
                Capability("meeting.list", "列出企业会议", implemented=False),
                Capability("minutes.fetch", "拉取会议录制转写", implemented=False),
                Capability("minutes.transcript", "拉取逐字稿", implemented=False),
                # 注意: 不实现 chat.send — VPBuddy 输出是文件
            ],
        )


# === 工厂 ===
_ADAPTERS = {
    "tencent": TencentAdapter,
    "dingtalk": DingTalkAdapter,
    "wecom": WeComAdapter,
}


def get_adapter(platform: str) -> PlatformAdapter:
    """获取平台适配器

    Raises:
        ValueError: 不支持的平台
    """
    if platform not in _ADAPTERS:
        raise ValueError(
            f"Unsupported platform: {platform}. "
            f"Supported: {', '.join(SUPPORTED_PLATFORMS)}"
        )
    return _ADAPTERS[platform]()


def list_supported() -> List[str]:
    return list(SUPPORTED_PLATFORMS)


# === CLI 工具 ===
def _print_table(platforms: List[PlatformAdapter]) -> None:
    print(f"\n{'='*80}")
    print(f"📡 VPBuddy 多平台能力 ({len(platforms)} 平台)")
    print(f"{'='*80}\n")
    for adapter in platforms:
        meta = adapter.meta
        print(f"🔹 {meta.display_name}  ({meta.platform})")
        print(f"   厂商:      {meta.vendor}")
        print(f"   API:       {meta.api_base}")
        print(f"   认证:      {meta.auth_method}")
        print(f"   免费额度:  {meta.free_tier}")
        print(f"   文档:      {meta.docs_url}")
        print(f"   能力 ({len(meta.capabilities)}):")
        for cap in meta.capabilities:
            status = "✅" if cap.implemented else "🚧"
            print(f"     {status} {cap.name:30s} {cap.description}")
        print()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="VPBuddy 多平台能力查看")
    parser.add_argument("--platform", help="只查某平台 (tencent/dingtalk/wecom)")
    parser.add_argument("--list", action="store_true", help="列出所有支持平台")
    args = parser.parse_args()

    if args.list or not args.platform:
        platforms = [get_adapter(p) for p in list_supported()]
        _print_table(platforms)
    else:
        adapter = get_adapter(args.platform)
        _print_table([adapter])
