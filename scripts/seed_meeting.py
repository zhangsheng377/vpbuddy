"""准备一个完整的测试会议(8 REQ + 4 GOAL + 6 FEAT + 5 RISK + 3 QUE)"""
import os
import sys
from pathlib import Path

# 关键: vpbuddy 在 src/ 下, 把 src 加进 path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

os.environ["VPBUDDY_DATA_DIR"] = "/tmp/vpbuddy_data/meetings"
os.environ["VPBUDDY_DOCS_DIR"] = "/tmp/vpbuddy_docs"

from vpbuddy.state import (
    MeetingState, Platform, Priority,
)
from vpbuddy.storage import MeetingStorage

mid = "PHASE2_TEST"
state = MeetingState(
    meeting_id=mid,
    platform=Platform.FEISHU,
    speaker_map={"SPK_00": "周华健(产品总监)", "SPK_01": "张胜东(VP)"},
)

# 8 个需求(直接 add_requirement(text, priority, speaker_id))
r1 = state.add_requirement("支持单点登录(SSO),对接企业 AD", priority=Priority.HIGH)
r1.speaker_name = "张胜东(VP)"

state.add_requirement("微信扫码登录,降注册门槛", priority=Priority.MEDIUM)
state.add_requirement("导出 Excel 报表,字段自定义", priority=Priority.MEDIUM)

r4 = state.add_requirement("支持多人同时编辑文档(类石墨)", priority=Priority.HIGH)
r4.speaker_name = "周华健(产品总监)"

state.add_requirement("API 调用限流 100 QPS/租户", priority=Priority.MEDIUM)
state.add_requirement("审计日志保留 90 天", priority=Priority.LOW)
state.add_requirement("支持飞书/钉钉/企微三平台消息推送", priority=Priority.HIGH)
state.add_requirement("管理后台 RBAC 权限管理", priority=Priority.HIGH)

# 4 个目标
g1 = state.add_goal("Q3 末上线 MVP,目标 50 家种子客户")
g2 = state.add_goal("首月日活 1000+,周留存 60%+")
g3 = state.add_goal("支持私有化部署(单租户)")
g3.speaker_name = "张胜东(VP)"
state.add_goal("集成飞书妙记做会议转写")

# 6 个功能
state.add_feature("SSO:支持 OIDC + SAML 2.0", priority=Priority.HIGH)
state.add_feature("微信扫码:OAuth 2.0 授权码模式", priority=Priority.MEDIUM)
state.add_feature("Excel 导出:支持 xlsx + 字段映射配置", priority=Priority.MEDIUM)
state.add_feature("协同编辑:CRDT 算法(yjs)+ WebSocket", priority=Priority.HIGH)
state.add_feature("多平台消息:飞书 WebSocket + 钉钉 Stream + 企微回调", priority=Priority.HIGH)
state.add_feature("RBAC:角色 + 资源 + 操作 三元组", priority=Priority.HIGH)

# 5 个风险(severity 用 Priority)
rsk1 = state.add_risk("OAuth 提供商可能限流", priority=Priority.HIGH)
rsk1.speaker_name = "周华健(产品总监)"
state.add_risk("协同编辑 CRDT 冲突合并有边界情况", priority=Priority.MEDIUM)
state.add_risk("微信开放平台审核周期长(7-15 天)", priority=Priority.MEDIUM)
state.add_risk("飞书 WebSocket 偶发断连,需心跳", priority=Priority.LOW)
rsk5 = state.add_risk("私有化部署时模型管理复杂", priority=Priority.HIGH)
rsk5.speaker_name = "张胜东(VP)"

# 3 个开放问题
state.add_question("SSO 走哪个 IdP?Okta/Azure AD/Auth0/钉钉/飞书?", is_urgent=True)
state.add_question("协同编辑后端用什么存储?MongoDB/PostgreSQL JSON/专用 CRDT DB?")
state.add_question("飞书 WebSocket 断连重试策略?指数退避?")

# 最后 touch 一下,确保 last_updated 更新
state._touch()

storage = MeetingStorage(data_dir="/tmp/vpbuddy_data/meetings")
storage.save(state)
print(f"✅ Saved meeting {mid}:")
print(f"   REQ: {len(state.requirements)}")
print(f"   GOAL: {len(state.goals)}")
print(f"   FEAT: {len(state.features)}")
print(f"   RISK: {len(state.risks)}")
print(f"   QUE: {len(state.open_questions)}")
