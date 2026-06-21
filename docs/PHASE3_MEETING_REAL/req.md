# PHASE3_MEETING_REAL 需求清单

最后更新: 2026-06-21T15:30+08:00 (v2 · 第 4 次端到端 · VPBUDDY_DIRECT 模式)
会议: Q3 协同 MVP 评审 (真音频转写 · 150s · 3 说话人)
session_id: `meeting:PHASE3_MEETING_REAL:req`

---

## 统计

| 优先级 | 数量 |
|---|---|
| high | 3 (REQ-780B14, REQ-565948, REQ-0D5DB7) |
| medium | 3 (REQ-1AD367, REQ-302783, REQ-B12E21) |
| low | 1 (REQ-B82FAC) |
| **合计** | **7** |

## 说话人映射(已校准)

- SPEAKER_00 → 张胜东(VP)
- SPEAKER_01 → 周华健(产品总监)
- SPEAKER_02 → 李丹(设计师)

---

## HIGH 优先级(3 条)

### REQ-780B14 实时协同编辑: 基于 CRDT 算法(yjs),支持 10 人并发
- **优先级**: high
- **状态**: ItemStatus.PENDING
- **来源**: 周华健
- **创建**: 2026-06-21T06:33:45

### REQ-565948 RBAC 权限管理: 角色 + 资源 + 操作 三元组
- **优先级**: high
- **状态**: ItemStatus.PENDING
- **来源**: 周华健
- **创建**: 2026-06-21T06:33:45

### REQ-0D5DB7 跨平台消息推送: 飞书 WebSocket + 钉钉 Stream + 企微回调
- **优先级**: high
- **状态**: ItemStatus.PENDING
- **来源**: 周华健
- **创建**: 2026-06-21T06:33:45


## MEDIUM 优先级(3 条)

### REQ-1AD367 协同编辑历史回溯 (P2, MVP 先做实时)
- **优先级**: medium
- **状态**: ItemStatus.PENDING
- **来源**: 李丹

### REQ-302783 API 限流 100 QPS 每租户 (Redis token bucket)
- **优先级**: medium
- **状态**: ItemStatus.PENDING
- **来源**: 张胜东

### REQ-B12E21 Excel 导出报表: xlsx + 字段映射配置 (MVP 内做)
- **优先级**: medium
- **状态**: ItemStatus.PENDING
- **来源**: 李丹


## LOW 优先级(1 条)

### REQ-B82FAC 审计日志保留 90 天 (P2 可配置)
- **优先级**: low
- **状态**: ItemStatus.PENDING
- **来源**: 张胜东


---

## v2 更新说明

第 4 次端到端触发(VPBUDDY_DIRECT=1)重写:
- 说话人映射从占位符(SPEAKER_00/01/02)升级到真名(张胜东/周华健/李丹)
- 加入 3 真名角色标签(VP / 产品总监 / 设计师)
- 统计表 + 来源列

数据无新增(累积与 v1 一致)。
