# PHASE3_MEETING_REAL 风险清单

最后更新: 2026-06-21T15:30+08:00 (v2 · 第 4 次端到端 · VPBUDDY_DIRECT 模式)
session_id: `meeting:PHASE3_MEETING_REAL:risk`

---

## 风险统计

| 严重度 | 数量 |
|---|---|
| critical | 0 |
| high | 0 () |
| medium | 6 (RISK-07A255, RISK-33E783, RISK-2F0662, RISK-D4D687, RISK-9EDDD6, RISK-F14066) |
| low | 0 () |
| **合计** | **6** |

---

### RISK-07A255 飞书 WebSocket 偶发断连: 30s 心跳 + 重试队列,具体策略待定
- **严重度**: Priority.MEDIUM
- **来源**: —
- **状态**: ItemStatus.PENDING
- **建议 Owner**: 待 V 指定
- **缓解方案**: 待 V 决策


### RISK-33E783 OAuth 第三方限流: Auth Service 内置本地缓存 + 429 退避
- **严重度**: Priority.MEDIUM
- **来源**: —
- **状态**: ItemStatus.PENDING
- **建议 Owner**: 待 V 指定
- **缓解方案**: 待 V 决策


### RISK-2F0662 微信开放平台审核 7-15 天: 提前申请 + 账号密码 fallback
- **严重度**: Priority.MEDIUM
- **来源**: —
- **状态**: ItemStatus.PENDING
- **建议 Owner**: 待 V 指定
- **缓解方案**: 待 V 决策


### RISK-D4D687 CRDT 冲突合并边界情况: yjs 已有保证,边界场景需 case-by-case 测试
- **严重度**: Priority.MEDIUM
- **来源**: —
- **状态**: ItemStatus.PENDING
- **建议 Owner**: 待 V 指定
- **缓解方案**: 待 V 决策


### RISK-9EDDD6 私有化部署模型管理复杂: 单租户镜像化,后续 ADR 细化
- **严重度**: Priority.MEDIUM
- **来源**: —
- **状态**: ItemStatus.PENDING
- **建议 Owner**: 待 V 指定
- **缓解方案**: 待 V 决策


### RISK-F14066 campplus 说话人聚类在歌曲/独唱上聚出多类(本测试歌曲聚出 8 类),真实会议需校准
- **严重度**: Priority.MEDIUM
- **来源**: —
- **状态**: ItemStatus.PENDING
- **建议 Owner**: 待 V 指定
- **缓解方案**: 待 V 决策



---

## 风险闭环机制

- 每次新会议开始前,scan 旧会议 RISK 列表,看是否仍 open
- open 风险带过新会议讨论,直到 V 显式 close
- 严重度升级: 触发连续 2 次会议讨论未解决 → 自动升 high

## v2 改动

- 加入 3 真名角色(张胜东/周华健/李丹)
- 风险统计表完整化
- 风险闭环机制章节(YAGNI 后加,因为这次踩了 V 抱怨"老问题不闭环"的坑)
