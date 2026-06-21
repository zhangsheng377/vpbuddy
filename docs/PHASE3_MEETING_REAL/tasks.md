# PHASE3_MEETING_REAL 任务列表

最后更新: 2026-06-21 (v3)
session_id: `meeting:PHASE3_MEETING_REAL:tasks`

---

## 任务来源

3 GOAL + 7 REQ + 5 FEAT → 拆解为 10 个可执行任务
(v2 → v3: 来源 ID 已重新对齐当前累积;新增 T-008/009/010)

---

## ✅ Done (沿用 v2,本会议核心目标)

### T-001 GPU 端到端 pipeline 跑通
- **Owner**: 张胜东
- **来源**: GOAL-CEDF6C (飞书妙记会议转写)
- **状态**: ✅ done (2026-06-21T05:30)
- **完成**: 150s 真会议 / 1.4s 处理 / 3 说话人精准分出
- **验证**: demo.html §01 + §05

### T-002 6 sub-session 文档自动生成
- **Owner**: 张胜东
- **来源**: GOAL-CEDF6C
- **状态**: ✅ done
- **完成**: 6 docs 全部生成,17 KB 跨会议 RAG
- **教训**: hermes sub-session 没写文件工具 → VPBUDDY_DIRECT 模式
- **验证**: req.md / arch.md / tasks.md / api.md / risk.md / demo.html

### T-003 说话人校准 (PHASE2 歌曲误分 8 类 → 真会议精准 3 类)
- **Owner**: 张胜东
- **来源**: GOAL-CEDF6C
- **状态**: ✅ done
- **完成**: speaker_map 真名映射,3 说话人精准分出
- **教训**: 歌曲 vs 会议 campplus threshold 不同,真会议校准后保持稳定

---

## 🚧 In Progress (沿用 v2,本会议评审)

### T-004 跨平台通知适配
- **Owner**: 待分配
- **来源**: REQ-0D5DB7 / FEAT-C93459
- **状态**: 🚧 partial
- **已完成**: 飞书 WS + 钉钉 Stream
- **进行中**: 企微回调 (RISK-2F0662 审核 7-15d)
- **风险**: RISK-07A255 飞书 WS 断连 (30s 心跳 + 重试队列)
- **决策点**: QUE-5EE081 飞书 WS 断连重试参数 (指数退避 jitter 死信阈值)

### T-005 协同编辑 MVP (yjs + PG JSON)
- **Owner**: 待分配
- **来源**: REQ-780B14 / FEAT-2F8528 / FEAT-BBD14D
- **状态**: 🚧 in progress
- **已定**: yjs@13.6 + PostgreSQL JSON (QUE-62A0A8 评审通过)
- **进行中**: 50 ops / 5 min 快照策略
- **风险**: RISK-D4D687 CRDT 边界 case-by-case 测试
- **关联**: REQ-1AD367 历史回溯 (P2, MVP 先做实时)

---

## 📋 Pending (v3 新增/调整)

### T-006 RBAC + SSO 落地
- **Owner**: 待分配
- **来源**: REQ-565948 / FEAT-1763F9
- **状态**: 📋 pending
- **范围**: OIDC + SAML 2.0 / 三元组(角色-资源-操作)
- **决策点**: QUE-661567 SSO IdP 选型 (倾向 Azure AD,Okta 备选,Auth0 贵,飞书钉钉补充)
- **风险**: RISK-33E783 OAuth 第三方限流 (Auth Service 内置本地缓存 + 429 退避)

### T-007 Excel 导出报表
- **Owner**: 待分配
- **来源**: REQ-B12E21 / FEAT-BAB8DB
- **状态**: 📋 pending (MVP 内做)
- **范围**: openpyxl + 字段映射配置 / S3 临时链接 24h 有效

### T-008 API 限流 (v3 新增)
- **Owner**: 待分配
- **来源**: REQ-302783
- **状态**: 📋 pending
- **范围**: 100 QPS 每租户 / Redis token bucket

### T-009 审计日志 (v3 新增)
- **Owner**: 待分配
- **来源**: REQ-B82FAC
- **状态**: 📋 pending (P2 可配置)
- **范围**: 90 天保留

### T-010 私有化部署 (v3 新增)
- **Owner**: 待分配
- **来源**: GOAL-5FB2BA
- **状态**: 📋 pending (后续 ADR 细化)
- **范围**: 单租户 / 所有组件可镜像化
- **风险**: RISK-9EDDD6 模型管理复杂

---

## 决策点汇总 (来自开放问题)

- QUE-661567 SSO IdP 选型 → 影响 T-006
- QUE-5EE081 飞书 WS 重试参数 → 影响 T-004
- QUE-62A0A8 协同编辑后端存储 → ✅ 已定 PG JSON,关闭

---

## 风险关联

- RISK-07A255 → T-004 飞书 WS 断连
- RISK-33E783 → T-006 OAuth 限流
- RISK-2F0662 → T-004 企微审核
- RISK-D4D687 → T-005 CRDT 边界
- RISK-9EDDD6 → T-010 私有化模型管理
- RISK-F14066 → ✅ 已解决 (T-003 校准后稳定)

---

## v3 改动 (vs v2)

- **来源 ID 全部重映射**: v2 引用 GOAL-18CA7F / FEAT-136180 等旧 ID,当前累积已重生成 → 全部对齐到 GOAL-CEDF6C / FEAT-C93459 / FEAT-2F8528 / FEAT-BBD14D / FEAT-1763F9 / FEAT-BAB8DB
- **新增 T-008 API 限流** (REQ-302783)
- **新增 T-009 审计日志** (REQ-B82FAC)
- **新增 T-010 私有化部署** (GOAL-5FB2BA)
- **风险关联用新 ID**: RISK-XXXXX 占位符 → 真实 RISK-07A255/33E783/2F0662/D4D687/9EDDD6
- **决策点用新 ID**: QUE-5EE081 重试参数 / QUE-661567 IdP / QUE-62A0A8 已定 PG JSON
- **去掉具体 Owner**: v2 用会议说话人当 Owner 不严谨 → 改为"待分配"

---

## 任务统计

- ✅ done: 3 (T-001, T-002, T-003)
- 🚧 in progress: 2 (T-004, T-005)
- 📋 pending: 5 (T-006, T-007, T-008, T-009, T-010)
- **总计: 10**
