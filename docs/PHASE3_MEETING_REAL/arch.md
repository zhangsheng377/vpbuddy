# PHASE3_MEETING_REAL 架构设计

最后更新: 2026-06-21T18:55+08:00 (v3 · 第 5 次端到端 · VPBUDDY_DIRECT 模式 · **修正错版**)
session_id: `meeting:PHASE3_MEETING_REAL:arch`
会议: Q3 协同 MVP 评审 (真音频转写 · 累积 2026-06-21T10:52:39Z)

---

## 目标

- **Q3 末**MVP 评审通过(GOAL-Q3MVP)
- 支撑 50 家种子客户日常协同(FEAT-COLLAB)
- 实时协同:10 人并发无感知延迟(yjs CRDT 自动合并)
- 离线可用:断网编辑 → 联网自动 sync,无冲突
- 文档级权限安全:服务端强制鉴权,前端不可绕过

## 总体架构(Mermaid)

```mermaid
graph TD
  subgraph Client[浏览器客户端]
    UI[React 富文本 UI<br/>ProseMirror]
    Y[Yjs Y.Doc<br/>共享 CRDT 数据结构]
    Quill[y-prosemirror 绑定]
    IDB[(IndexedDB<br/>y-indexeddb 持久化)]
    Aware[awareness<br/>光标 / 在线状态]
  end

  subgraph Server[服务端 Node.js]
    WS[y-websocket<br/>WebSocket 服务]
    AUTH[鉴权中间件<br/>JWT → user]
    ACL[文档级 ACL<br/>owner/editor/<br/>commenter/viewer]
    DOC[(Y.Doc 持久化<br/>Postgres y_updates)]
    SNAP[(快照存储<br/>S3 / MinIO)]
    NOTI[通知服务<br/>@mention / 评论]
    EXP[导出服务<br/>Puppeteer PDF]
  end

  UI <--> Quill
  Quill <--> Y
  Y <-->|实时更新| WS
  Y <-->|本地缓存| IDB
  Y <-->|awareness| Aware
  WS --> AUTH --> ACL --> DOC
  DOC -->|定时快照| SNAP
  DOC -->|评论/mention| NOTI
  Y -->|客户端导出 MD| UI
  DOC -->|服务端渲染| EXP
```

## 关键模块

### 1. 协同编辑核心(yjs + ProseMirror)
- **职责**:富文本实时协同 + 无冲突合并
- **接口**:`Y.Doc` API:`getText()` / `getMap()` / `getArray()`
- **技术选型**:
  - **yjs**: 生产级 CRDT 库,YATA 算法,RFC 兼容
  - **y-prosemirror**: 绑定 ProseMirror(支持表格/列表/嵌入)
  - **awareness**: 光标位置 / 选区 / 用户头像

### 2. 离线持久化层(y-indexeddb)
- **职责**:浏览器本地缓存所有 Y.Doc 更新
- **接口**:启动时自动从 IDB 加载 → 联网后双向 sync
- **技术选型**:y-indexeddb(官方持久化 provider,无需自研)
- **关键点**:写操作先入 IDB → 再经 WebSocket 广播,**离线写不丢失**

### 3. 实时同步层(y-websocket)
- **职责**:服务端广播 Y.js 更新包(op log)
- **接口**:WebSocket binary 协议(`y-protocols/sync` + `awareness`)
- **技术选型**:
  - **服务端**:官方 `y-websocket` server(Node.js + `ws` 库)
  - **协议**:`y-protocols` 标准 sync 协议
  - **MVP 单实例**:50 客户 × 10 并发 = 500 连接,单 Node 进程够用

### 4. 鉴权与 ACL(REQ-0D5DB7)
- **职责**:每个 WebSocket 消息级鉴权 + 文档级权限过滤
- **接口**:
  - JWT 解析 → `user_id`
  - ACL 表:`doc_id` × `user_id` × `role`
- **关键点**:**服务端强制**(防前端绕过),op 经 ACL 过滤后再 broadcast

### 5. 文档持久化
- **职责**:Y.Doc 二进制更新流持久化 + 元数据
- **接口**:`y_updates` 表按 `(doc_id, clock)` 追加
- **技术选型**:**PostgreSQL**(关系型 + JSONB 兼容,初版不分库)
- **重启恢复**:加载 doc_id 所有 update → Y.Doc apply → 重发

### 6. 版本快照(REQ-B12E21)
- **职责**:定时/手动冻结 Y.Doc 快照 + diff 视图
- **接口**:`Y.snapshot()` 序列化 → S3
- **技术选型**:
  - 快照 = `Y.encodeSnapshotAsUpdate(doc, snapshot)` 二进制
  - **存储**:S3 / MinIO(对象存储便宜)
  - **diff**:基于 op log 自实现(同段 text diff)
- **触发**:每 5 分钟自动 + 用户手动"保存版本"

### 7. 评论与 @mention(REQ-1AD367)
- **职责**:文档内评论线程 + 通知
- **接口**:
  - `comments: Y.Array<{thread_id, anchor, body, mentions}>`
  - 锚点:相对文本范围(yjs RelativePosition)
- **技术选型**:
  - 评论节点嵌入 Y.Doc(随主文档协同)
  - @mention → 通知服务 → WebSocket 推送 → 红点

### 8. 导出(REQ-302783)
- **职责**:Markdown 客户端 + PDF 服务端
- **接口**:
  - **MD**:`Y.Doc.toJSON()` → 自定义序列化 → `.md` 下载(纯前端)
  - **PDF**:服务端 Puppeteer 渲染预置 HTML 模板
- **技术选型**:Puppeteer(跨平台一致,字体可控)

### 9. 深色模式 + 无障碍(REQ-B82FAC)
- **职责**:UI 主题切换 + WAI-ARIA
- **接口**:CSS variables 切换主题
- **技术选型**:标准 WAI-ARIA + 键盘导航

## 数据流(端到端)

```
用户敲键盘
  → ProseMirror 产生 transaction
  → y-prosemirror 产生 Y.Doc update
  → (1) 写 IndexedDB (本地持久化)
  → (2) 经 WebSocket 发往服务端
  → 服务端 ACL 鉴权 → 写 Postgres y_updates
  → 服务端广播给同 doc 其他在线客户端
  → 其他客户端 y-prosemirror 应用 update → 渲染
```

**离线场景**:
```
断网 → 用户继续编辑 → op 累积在 IDB
恢复联网 → y-indexeddb provider 检测连接恢复
  → 把累积 op 通过 WebSocket 发往服务端
  → 服务端 ACL 检查 → broadcast → 其他端无冲突合并
```

## 关键决策(ADR)

| ADR | 决策 | 理由 | 日期 |
|---|---|---|---|
| 0001 | yjs 作为 CRDT 引擎(不自研) | YATA 成熟、有 1 万+ star、生产验证 | 2026-06-21 |
| 0002 | ProseMirror + y-prosemirror(而非 Quill) | 表格/嵌入/自定义 schema 强,MVP 之后扩展省事 | 2026-06-21 |
| 0003 | y-indexeddb 做离线持久化 | 官方 provider,自动冲突合并,无额外代码 | 2026-06-21 |
| 0004 | 官方 y-websocket server(不自研) | 协议兼容保证、awareness 内置、单实例够用 | 2026-06-21 |
| 0005 | 服务端强制 ACL(防前端绕过) | 文档安全底线;前端鉴权 = 0 安全 | 2026-06-21 |
| 0006 | 快照存 S3 + op log 存 Postgres | 主存热数据 / 冷数据分离,成本最优 | 2026-06-21 |
| 0007 | PDF 服务端 Puppeteer(非客户端) | 跨平台一致 / 字体可控 / 大文档不卡浏览器 | 2026-06-21 |
| 0008 | 单进程 MVP(不分布式) | 50 客户 × 10 并发 = 500 ws,Node 单进程够 | 2026-06-21 |

## 已知风险(从累积 RISK 同步)

### RISK-CRDT · [MEDIUM] 算法复杂度高,实现风险
- **现状**:直接用成熟 yjs,**实现风险已大幅降低**
- **残余风险**:
  1. **内存增长**:大文档 Y.Doc 长期累积 op,需配置 `gc: true` 定期清理
  2. **文档大小上限**:单 doc > 10MB 编辑卡顿,MVP 限 1MB(约 5 万字)
  3. **schema 升级**:y-prosemirror schema 变更需 migrate,提前冻结 v1 schema
- **缓解**:MVP 限文档大小 + 监控 Y.Doc 内存 + schema 版本号

### 派生风险(MVP 阶段)
- **WebSocket 单点**:MVP 单进程,挂了就全断 — 50 客户可接受,Q4 评估 Redis pub/sub
- **快照膨胀**:每 5 分钟一次,3 个月 = 大量快照 → S3 lifecycle 30 天转冷存储

## 开放问题(从累积同步)

### OPEN-OFFLINE · 离线 sync 冲突如何解决?
- **结论(架构层)**:**CRDT 自带无冲突合并,本质不需要冲突解决**
  - YATA 算法保证:任意顺序 apply 一组 op,最终状态一致
  - 离线编辑 + 联网 sync = 自然的"最后写入者胜"合并,无需用户介入
- **UX 层遗留问题**(不算架构风险,产品决策):
  - 用户期望"撤回自己离线期间的别人编辑" → 需要 op history + undo stack
  - 这种场景在 MVP 阶段**不实现**(YAGNI),用 CRDT 自动合并即可

## v3 改动(修正错版)

| 维度 | v2(错) | v3(对) |
|---|---|---|
| **产品主题** | 描述 VPBuddy 自己的 ASR/GPU/funasr 流程 | 描述会议讨论的 Q3 协同 MVP 产品 |
| **核心模块** | GPU 推理 / MeetingState / controller | yjs / y-prosemirror / y-websocket / ACL / 快照 |
| **ADR** | 6 条 VPBuddy 内部(0001-0006) | 8 条产品架构决策(0001-0008) |
| **Mermaid 图** | LR 单向(音频→doc) | graph TD(客户端 / 服务端分明) |

**根因**:v2 端到端时把"会议讨论的产品架构"与"VPBuddy 自身架构"混淆。本次 v3 回归正确主题,与 req.md v3 / risk.md / tasks.md 一致。

---

## 待确认(留给 V 决策)

1. **认证方案**:JWT 自建 vs OAuth(Google / 企业 SSO)?— MVP 可只 JWT
2. **托管**:自建 K8s vs Vercel + Railway?— MVP 推荐 Railway(免运维)
3. **客户端框架**:React vs Vue?— 团队熟悉度优先