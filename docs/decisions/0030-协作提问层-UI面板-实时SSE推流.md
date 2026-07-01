# 0030. 协作提问层 — UI 折叠面板 + 实时 SSE 推流 (Commit 4 客户端落地)

- **状态**: 已接受
- **日期**: 2026-07-01
- **作者**: 张胜东 (起草: Hermes)
- **依赖**: [ADR-0028](../decisions/0028-协作提问层-collab-md三方共享.md) (collab.py + 3 HTTP 端点) · [ADR-0029](../decisions/0029-6sub-session合并为2batch-agent.md) (6→2 kinds 合并)
- **前置**: Commit 1 (`7a877bc`) + Commit 2 (`5d7650a`) + Commit 3 (`4ab04bb`) 已落库

## 背景

ADR-0028 在服务端搭好 collab 协议层 (collab.py + 3 HTTP 端点), 但**客户端没有任何 UI** 能看到 agent 提问、回答疑问、或主动追问。问题:

1. **看不见**: agent 提的问题写在 `docs/{mid}/collab.md`, VP 不知道
2. **不能答**: 即使知道有 Q, 没法在 UI 里直接回答
3. **不实时**: agent 提问后, VP 不主动 GET 就不更新

## 决策

### 1. Chat 面板顶部加 [🤝 协作疑问] 折叠面板

**位置**: `vpbuddy-client/ui/index.html` — `<section id="panel-chat">` 最顶部, 在 `<div id="chat-list">` 之前。

**结构**:
```html
<details id="collab-panel" class="collab-panel">  <!-- 默认折叠 -->
  <summary>
    <span class="collab-label">🤝 协作疑问</span>
    <span id="collab-pending-count" class="collab-badge" style="display:none;">0</span>
    <span id="collab-collapsed-info" class="collab-collapsed-info"></span>
  </summary>
  <div class="collab-body">
    <div id="collab-pending">...</div>      <!-- 待答 Q 列表 -->
    <div class="collab-ask-row">...</div>   <!-- 主动提问 (section + text + 按钮) -->
    <details class="collab-answered-toggle">
      <summary>已回答 (N)</summary>
      <div id="collab-answered">...</div>  <!-- 已答 Q 列表 -->
    </details>
  </div>
</details>
```

**关键设计**:
- **默认折叠**: 折叠时只显示 "🤝 协作疑问 [N 徽标]" + "有 N 个待答疑问"
- **有 pending 时显示黄色徽标** (.collab-badge, 18px, --warning 背景)
- **新 Q 进来自动展开** (SSE `collab-update action=ask` → `panel.open = true`)

### 2. 3 个 HTTP 端点 + 字段兼容

| HTTP 端点 | 字段 (请求) | 字段 (响应) | 说明 |
|---|---|---|---|
| `GET /api/meetings/{id}/collab` | — | `asked_by` / `answered_by` (来自 markdown 解析) | 拉全量 |
| `POST /api/meetings/{id}/ask_question` | `section` + `question` + `asker` | `qid` + `status` | 主动提问 |
| `POST /api/meetings/{id}/answer_question` | `qid` + `answer` + `answerer` | `qid` + `status` | 回答 |

**字段兼容陷阱**: GET 端点返 `asked_by/answered_by` (markdown 解析字段名), POST 端点参数用 `asker/answerer` (端点参数名), SSE 推 `asker/answerer` (跟 POST 一致)。前端渲染用 `q.asked_by || q.asker` 兼容两种来源。

### 3. SSE `collab-update` 实时推流

服务端 (ADR-0028 Commit 2 已实现) 推 2 种 action:

```js
listen("collab-update", (e) => {
  const p = e.payload || {};
  if (p.action === "ask") {
    upsertPendingQuestion({qid, section, question, asker, status});  // +1
    panel.open = true;                                                // 自动展开
  } else if (p.action === "answer") {
    movePendingToAnswered({qid, answer, answerer});                    // -1
  }
});
```

### 4. 主动提问栏 (ask row)

折叠面板底部固定一个 ask row:

```html
<div class="collab-ask-row">
  <select id="collab-section">
    <option value="req">需求</option>
    <option value="arch">架构</option>
    <option value="tasks">任务</option>
    <option value="api">API</option>
    <option value="risk">风险</option>
    <option value="demo">演示</option>
  </select>
  <input id="collab-q-input" placeholder="主动追问 (例: 性能预算?)" maxlength="200" />
  <button id="collab-ask-btn">提问</button>
</div>
```

**Enter 提交**, **节流**: 同 (mid, section, 相似问题) 1 会议只 1 次 (server `_throttle_key` 实现, ADR-0028)。

### 5. 回答 UI 内嵌 (无 modal)

每个 pending Q 卡片里直接嵌一个 `<textarea>` + 2 按钮:

```
┌──────────────────────────────────────────┐
│ [req] agent 2026-07-01    [回答]         │
│ 性能预算多少?                             │
│ ┌──────────────────────────────────┐    │
│ │ (textarea, 默认隐藏)              │    │
│ └──────────────────────────────────┘    │
│                       [发送] [取消]     │
└──────────────────────────────────────────┘
```

**无 modal**: KISS, 直接内联, VP 点 [回答] 展开, Enter 提交 / Shift+Enter 换行, 提交后表单自动收起。

## 状态管理

前端 2 个 `Map<qid, item>`:
- `pendingQuestions`: 待答 (SSE ask 增 / answer 删 + 移到 answered)
- `answeredQuestions`: 已答 (倒序展示, 最新在前)

`refreshCollab()` 在切会议时调 (跟 `refreshChatHistory` / `loadDemoVersions` 同位置, in start_capture success), 用 `pendingCountDelta = 0` 重置增量计数。

## 性能影响

- 网络: SSE `collab-update` 1 event / Q (轻量)
- 渲染: 每次 SSE 更新重渲染整个 panel (`renderCollabPanel` 全量 replaceHTML)
- **不做**: 增量 DOM diff (Q 数 < 10 完全够, KISS)

## 测试覆盖

UI 端没法跑单元测试 (浏览器/Vite 端), 验证靠:
1. **Vite build** (ESM + Tauri API import 解析) — 7 modules transformed
2. **Node `--check ui/main.js`** — syntax
3. **服务端 pytest** (125 passed, 含 collab 25 + endpoints 13 + batch_docs 19 + 4 回归套件)

未来要加 UI 测试: Playwright (Tauri 推荐) 或 jsdom + 手动 mock `fetch` + `invoke`。

## 弃用方案

- ❌ **WebSocket 替换 SSE**: 单向推流够, 多一协议不值得
- ❌ **Modal 弹窗**: 打断流程, 内嵌更轻
- ❌ **3 个独立页签 (Pending/Answered/Ask)**: 折叠 + 内嵌足够, 不必拆
- ❌ **加 optimistic UI** (本地立即改, 不等 SSE): 节流可能在 server 拒, 假阳体验差

## 后续

- **Agent 端 read_collab**: 3 个 agent prompt (chat / batch_docs / demo) 已加 collab 协议段 (ADR-0028 Commit 3 同步), 实际读取触发增量 patch 留给后续 commit
- **主动消息走 collab** (Phase 5/6, 待 commit): agent 触发不再是聊天, 而是 collab 提问