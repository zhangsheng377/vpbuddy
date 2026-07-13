# ADR-0056: Phase 0 数据隔离审计报告

> **状态**: confirmed (部分) + suspected (待复现)
> **日期**: 2026-07-13
> **关联**: [Issue #41](https://github.com/zhangsheng377/vpbuddy/issues/41) (Audit First v2)
> **审计者**: TRAE agent
> **版本**: v0.22.7

---

## 审计结论概览

| 编号 | 问题 | 严重度 | 状态 |
|------|------|:---:|:---:|
| A | **KB 检索不按 `meeting_id` 过滤** → 同用户所有会议 KB 互可见 | — | **by design** — KB 按 `user_id` 隔离，不按 `meeting_id` |
| B | `handle_chat_upload` 写 KB 缺少 `scope` 字段 | 🟡 P1 | **confirmed** |
| C | **Experience 无 PII 检测/脱敏/抽象，原文直接存储** | — | **暂缓** — 先不做，以免引入隐含问题 |
| D | `search_experiences()` 调用未传任何过滤参数 | 🟡 P1 | **confirmed** |
| E | `DELETE /api/meetings/{id}` 不清理 uploads/KB/agent cache | 🟡 P1 | **confirmed** |
| F | Agent 文件工具 **无目录 sandbox**，可跨会议读文件 | — | **暂缓** |
| G | `stream_start` reuse 时清空 transcript_segments | 🟠 P2 | **confirmed** |
| H | `parent_session_id` fork 不生效（已知，已有补偿） | — | **known** |
| I | **反复出现的人名来源** — 待复现 | 🔴 P0 | **suspected** |

**待确认 P0**: 仅 I（人名来源），需要生产数据抽样复现。

---

## 1. 当前数据调用链总览

### 1.1 数据对象物理路径与隔离矩阵

| 数据对象 | 物理路径/键 | user_id 隔离 | meeting_id 隔离 | scope 标识 | PII 风险 |
|----------|------------|:---:|:---:|:---:|:---:|
| MeetingState | `{DATA_DIR}/meetings/{mid}.json` | `owner_id` 字段 | 文件名 | — | ⚠️ 含需求和原文 |
| Chat history | `{DATA_DIR}/{mid}.chat.json` | 无(路径层面) | 文件名 | — | ⚠️ 含原始对话 |
| Stream meta | `{DATA_DIR}/{mid}.stream.json` | 无(路径层面) | 文件名 | — | ⚠️ 含转写全文 |
| Material 原件 | `{DATA_DIR}/materials/{mid}/{mat_id}/...` | 无(路径层面) | 是 | — | ⚠️ 原始文件 |
| Uploads | `{DATA_DIR}/uploads/{mid}/...` | 无(路径层面) | 是 | — | ⚠️ 原始文件 |
| KB (Chroma) | `{DATA_DIR}/chroma/` (单一 collection `vpbuddy_kb`) | metadata 有 `user_id` | metadata 有 `meeting_id` | ⚠️ `handle_chat_upload` 不写 `scope` | ⚠️ 全文内容 |
| Experience | `{DATA_DIR}/experiences/{mid}.json` + `_all.json` | 无 | 文件名 | ⚠️ `_all.json` 为全局 | **🔴 原文含 PII、人名、需求** |
| Docs | `{DOCS_DIR}/{mid}/...` | 无(路径层面) | 是 | — | — |
| Demo versions | `{DOCS_DIR}/{mid}/demo/` | 无(路径层面) | 是 | — | — |
| Agent cache | `_AGENT_CACHE = {meeting:{mid}:{kind}: AIAgent}` | **否** | 是(session_id) | — | ⚠️ 内存残留 |
| SSE history | `_event_history[mid] = [Event]` (内存) | 否(仅内存) | 是(key) | — | ⚠️ 跨生成 leak |
| `_CHAT_AGENT_CACHE` | `{meeting:{mid}:vp-chat: AIAgent}` | **否** | 是 | — | ⚠️ 内存残留 |

### 1.2 当前完整调用链

```
用户操作
├── POST /api/meetings/{id}/chat (multipart)
│   ├── handle_chat_upload()
│   │   ├── uploads/{mid}/UUID_name.ext        ← 文件落盘
│   │   └── Chroma KB add()                    ← metadata: {user_id, meeting_id, source:"chat-upload:..."}
│   │                                            ⚠️ 无 scope / labels / meeting_callable
│   ├── _run_vp_chat()                         ← vision/chat agent 响应
│   ├── _append_chat_message() → {mid}.chat.json  ← 持久化聊天
│   └── 图片上传后 → task_manager.submit() → _dispatch_kind(BATCH_DOCS_KIND + DEMO_KIND)
│
├── POST /api/meetings/{id}/materials
│   ├── material_storage.store_file()
│   │   └── materials/{mid}/{mat_id}/...       ← 文件落盘
│   ├── 图片: _run_vision_async()               ← vision API (异步线程)
│   ├── 文件: handle_kb_upload()
│   │   ├── uploads/{mid}/UUID_name.ext
│   │   └── Chroma KB add()                    ← metadata: {user_id, meeting_id, scope:"personal_kb", ...}
│   └── _append_chat_message() → {mid}.chat.json
│
├── WebSocket /api/meetings/{id}/realtime_asr
│   ├── 百炼 ASR → MeetingState.cleaned_text
│   └── _kick_docs (gkd): hash-based 检测变更 → task_manager.submit(_doc_runner)
│
├── _close_meeting() (POST /close 或 delete)
│   ├── push_event("meeting-complete")
│   ├── clear_throttle()
│   ├── extract_from_meeting_state()           ← 从 requirements/goals/risks 提取
│   │   └── ExperienceItem (text=原文, ⚠️ 无PII检测/脱敏)
│   ├── save_experiences() → experiences/{mid}.json + _all.json
│   ├── task_manager.submit(_doc_runner)
│   └── Thread(daemon=True): sleep(120) → close_meeting()
│
├── run_one_round() → trigger_sub_session()
│   ├── format_state_summary(state)
│   │   ├── state.cleaned_text + speaker_map
│   │   ├── uploads/{state.meeting_id}/         ← 读取磁盘文件列表
│   │   └── {state.meeting_id}.chat.json        ← 最近 20 条对话
│   ├── render_prompt(batch_docs) → search_experiences()  ← 所有已确认经验，无过滤
│   └── _get_or_create_agent() → AIAgent.chat()
│       └── toolsets = ["terminal", "file", "vision", "web"]  ← 无目录 sandbox
│
└── DELETE /api/meetings/{id}
    ├── close_meeting()                          ← ✅ SSE + event_history
    ├── task_manager.cancel_meeting()            ← ✅
    ├── storage.delete()                          ← ✅ MeetingState JSON
    ├── {mid}.chat.json unlink                   ← ✅
    ├── {mid}.stream.json unlink                 ← ✅
    ├── materials delete                          ← ✅
    ├── DOCS_DIR/{mid}/ rmtree                    ← ✅
    ├── uploads/{mid}/                            ← ❌ 未清理
    ├── _AGENT_CACHE pop                          ← ❌ 未清理
    ├── _CHAT_AGENT_CACHE pop                     ← ❌ 未清理
    ├── _CLEAN_AGENT_CACHE pop                    ← ❌ 未清理
    ├── KB Chroma 记录                            ← ❌ 未清理
    └── Experience 候选                            ← ❌ 未清理
```

---

## 2. KB 检索范围审计

### 2.1 检索入口 where 条件

| 入口 | where 条件 | 过滤 meeting_id | 过滤 scope |
|------|--|:---:|:---:|
| `handle_kb_search` (`GET/POST /api/kb/search`) | `{"user_id": user_id}` | ❌ | ❌ |
| `tools/kb_search.search()` (agent 工具) | `{"user_id": user_id}` | ❌ | ❌ |
| `handle_kb_list` (`GET /api/kb/list`) | `{"user_id": user_id}` | ❌ | ❌ |

**所有检索入口只按 `user_id` 过滤，不过滤 `meeting_id` 也不过滤 `scope`。**

gen 代码注释：

```python
# ADR-0047: 只按 user_id 隔离 (用户可查自己所有会议的 KB)
```

这是有意为之的设计。**已确认**: KB 按 `user_id` 隔离是正确行为，不按 `meeting_id` 隔离——用户有权在同一账户下跨会议检索自己的知识库。

### 2.2 Personal KB 与 Meeting Materials 共享相同 collection

`handle_kb_upload`、`handle_chat_upload`、materials 端点都写到 Chroma 的同一个 collection `vpbuddy_kb`。靠 `source` metadata 字段区分来源（`upload:xxx` vs `chat-upload:xxx`），不作为检索过滤条件。

### 2.3 KB 写入 metadata 差异

| 字段 | `handle_kb_upload` | `handle_chat_upload` |
|------|:--:|:--:|
| `user_id` | ✅ | ✅ |
| `meeting_id` | ✅ | ✅ |
| `scope` | ✅ `"personal_kb"` (默认) | ❌ **缺失** |
| `labels` | ✅ `""` (默认) | ❌ **缺失** |
| `meeting_callable` | ✅ `"true"` (默认) | ❌ **缺失** |

### 审计结论

| 确认项 | 结论 |
|--------|------|
| `meeting_id` 参数是否真正进入数据库过滤 | ❌ **confirmed** — KB 检索 where 不包含 meeting_id |
| `scope` 是否进入过滤 | ❌ **confirmed** — 不写也不读 scope |
| Personal KB 与 Meeting Material 共享 collection | ✅ **confirmed** |
| metadata 缺失 fail-open vs fail-closed | ⚠️ `handle_chat_upload` 缺失 scope/labels/meeting_callable，检索时找不到但不会报错（fail-open） |
| 新会议 Agent 搜索 Personal KB 时是否可能命中旧会议材料 | ✅ **confirmed** — 因为不按 meeting_id 过滤，agent 可以检索同用户所有会议的 KB |

---

## 3. Global Experience 审计

### 3.1 Experience 数据模型

当前 Experience 通过 `extract_from_meeting_state()` 生成，来源仅 3 类：
- `requirements` → `domain_fact`（置信度 0.4）
- `risks` → `failure_lesson`（置信度 0.5）
- `goals` → `decision_rule`（置信度 0.3）

**声明的 6 种 ExperienceKind 中有 3 种完全未使用**：`product_pattern`、`terminology`、`user_preference`。

### 3.2 PII 检测/脱敏/抽象 — **零覆盖**

- `extract_from_meeting_state()` 直接将 `requirement.text` 等原文复制为 `ExperienceItem.text`
- 没有任何 PII 检测（人名、邮箱、电话、地址、公司名）
- 没有任何脱敏/匿名化
- 没有任何抽象化（例如将"张三要求短信登录"抽象为"客户要求移动端登录方式"）
- 没有隐私审查管线（raw → classify → redact → abstract → privacy scan → approve → publish）

### 3.3 全局索引 `_all.json`

所有已确认的 experience 聚合到 `data/experiences/_all.json`，格式：
```json
{"items": [{ExperienceItem dicts}], "updated_at": "..."}
```

**没有任何 PII 过滤或项目事实检测发生在写入 `_all.json` 时。**

### 3.4 `search_experiences()` 调用方式

**唯一调用点**: `batch_docs.py` 的 `render_batch_prompt()`（L78-87）

```python
experiences = search_experiences()  # ⚠️ 不传任何参数，返回所有已确认经验
```

**注释写的"先通过 domain/product_type 匹配"是假注释，实际代码未传任何过滤条件。**

### 3.5 Experience 注入为原始文本

`format_experiences_for_prompt()` 直接将 `exp.text` 拼入 batch_docs agent 的 prompt 模板（最多 5 条）：

```text
## 历史经验参考 (自动检索)

- **📌 领域事实** 某公司要求支持微信支付（领域：支付）
```

### 3.6 Demo agent 和 Chat agent 不使用经验

确认：`prompts/demo.md` 全文不含 `experience` 字样，`sub_session_controller.py` 的 chat/render_prompt 也不注入经验。**只有 batch_docs agent 会收到经验注入。**

### 审计结论

| 确认项 | 结论 |
|--------|------|
| Experience 是否存在人名、需求原文 | 🔴 **confirmed** — 取决于 `requirements`/`goals`/`risks` 原文内容 |
| 是否存在 PII 检测管线 | 🔴 **confirmed: 无** |
| 是否存在抽象化/脱敏管线 | 🔴 **confirmed: 无** |
| `search_experiences()` 是否按 domain 过滤 | ❌ **confirmed: 无参数** |
| 注入时是否保留原始文本 | 🔴 **confirmed: 是** |
| batch_docs agent 是否可收到跨会议经验 | 🔴 **confirmed: 是**（返回所有已确认经验） |
| 反复出现的人名是否存在于 Experience 中 | **suspected** — 待抽样检查生产 `_all.json` 内容 |

---

## 4. Session 与 Agent Cache 隔离

### 4.1 Cache key

```python
_AGENT_CACHE        = {f"meeting:{meeting_id}:{doc_kind}": AIAgent}
_CHAT_AGENT_CACHE   = {f"meeting:{meeting_id}:vp-chat": AIAgent}
_CLEAN_AGENT_CACHE  = {f"meeting:{meeting_id}:asr-clean": AIAgent}
```

**Cache key 中不包含 `user_id`**。如果两个用户有相同 meeting_id（理论上不应该，但 meeting_id 是客户端指定的，不是服务端 UUID），他们共享同一个 agent session。

### 4.2 删除会议时的清理

`DELETE /api/meetings/{id}` **未清理** `_AGENT_CACHE`、`_CHAT_AGENT_CACHE`、`_CLEAN_AGENT_CACHE` 中的条目。旧 agent 内存残留，下次同一个 meeting_id 被使用时会复用。

### 4.3 Agent 文件工具无目录 sandbox

子 agent 的工具集为 `["terminal", "file", "vision", "web"]`。**没有任何路径限制。** Agent 理论上可以使用 `read_file` 读取任意会议的数据文件。

唯一"隔离"依靠 KB search 工具命令行示例中硬编码的 meeting_id 参数，但 agent 可以绕过 KB search 直接用文件读取。

### 4.4 `parent_session_id` fork 不生效

已确认（ADR-0055）：Hermes `parent_session_id` 仅作 DB 血缘标记，对话循环不消费。VPBuddy 通过 `format_state_summary` 手动注入 chat 历史作为补偿。

### 审计结论

| 确认项 | 结论 |
|--------|------|
| Cache key 是否只使用 meeting_id | ✅ **confirmed** — 不含 user_id |
| 删除后 Agent cache 是否清理 | ❌ **confirmed: 否** |
| 服务进程内旧 Agent 是否保留 | ✅ **confirmed: 是** |
| Agent 能否跨 meeting 读文件 | ✅ **confirmed: 是** — 无目录 sandbox |
| `format_state_summary` 是否对 | ✅ **confirmed** — 使用 `state.meeting_id` |
| `parent_session_id` fork 是否生效 | ❌ **confirmed: 否** (已知) |

---

## 5. Meeting ID 生命周期

### 5.1 创建/复用行为

- meeting_id 由客户端指定（非服务端 UUID）
- `stream_start` 返回 `reused=True` 当 meeting 已存在时
- reuse 时清空 `processed_chunks` 和 `transcript_segments` → **丢失之前转写记录**
- 前端是否把 resume 表现为"新会议" — **待前端审计**

### 5.2 删除时的资源清理

`DELETE /api/meetings/{id}` 已清理：
- MeetingState JSON ✅
- Chat history ✅
- Stream meta ✅
- Materials ✅
- Docs ✅
- SSE subscribers ✅
- Task manager tasks ✅

未清理：
- Uploads ❌
- KB Chroma 记录 ❌
- `_AGENT_CACHE` / `_CHAT_AGENT_CACHE` / `_CLEAN_AGENT_CACHE` ❌
- Experience 候选 ❌

---

## 6. 交付物隔离

### 6.1 API owner 检查

**所有** deliverable API 都有 `_require_meeting_owner` 检查：
`state` · `docs` · `aggregate` · `collab` · `chat` · `close` · `delete` · `events(SSE)` · `deliverables` · `materials` · `experiences approve/reject` · `kb delete`

### 6.2 文件系统隔离

- 文档物理路径：`{DOCS_DIR}/{meeting_id}/` — 不含 user namespace
- Materials：`{DATA_DIR}/materials/{meeting_id}/` — 不含 user namespace
- Uploads：`{DATA_DIR}/uploads/{meeting_id}/` — 不含 user namespace

用户级隔离仅依赖 `owner_id` 字段 + API 层 check，不依赖文件系统权限。

### 6.3 Agent 文件工具

Agent 可以通过 `read_file`/`write_file` 访问任意文件系统路径，无目录 sandbox 限制。

---

## 7. 下一步行动

### Phase 0 必须完成的事项

| # | 事项 | 优先级 | 备注 |
|---|------|:---:|------|
| 1 | 抽样 `_all.json` 检查是否含人名/PII | P0 | 需要在服务器上执行 |
| 2 | 搭建可复现矩阵（用户 A/B 交叉测试）| P0 | 需要部署测试环境 |
| 3 | 追踪反复出现的人名在 Experience/chat.json/KB 中的来源 | P0 | 需要 grep 生产数据 |

### Phase 1 推荐修复（按当前状态裁剪）

> A（KB 按 user_id 隔离）为 by design，不做修改；
> C（Experience PII）暂缓；
> F（agent sandbox）暂缓。

| # | 修复 | 优先级 | 理由 |
|---|------|:---:|------|
| 1 | `handle_chat_upload` 补 `scope` 字段 | P1 | metadata 与 `handle_kb_upload` 保持一致 |
| 2 | `search_experiences()` 传过滤参数 | P1 | 按 domain 过滤，目前无任何过滤 |
| 3 | `DELETE /api/meetings/{id}` 清理 uploads + KB + agent cache | P1 | 避免磁盘/内存泄漏，会议复用不残留旧数据 |
| 4 | `stream_start` reuse 时保留 transcript | P2 | 断线重连不丢失转写记录 |
| 5 | Agent cache key 加 `user_id` | P2 | `meeting_id` 是客户端指定，防止多用户碰撞 |
| 6 | **人名来源追踪 (I)** | **P0** | 抽样 `_all.json` + meeting JSON 中搜索该人名 |
