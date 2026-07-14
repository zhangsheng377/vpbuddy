# VPBuddy HTTP API 参考

> **版本**: v0.22.8 · `@ 2026-07-14`
> **Base URL**: `http://47.100.182.3:28765`（公网 GPU 服务器）
> **协议**: HTTP/1.1 · WebSocket 实时 ASR · SSE 实时推送 · Multipart 上传
> **编码**: 所有请求/响应使用 UTF-8
> **CORS**: 所有端点返回 `Access-Control-Allow-Origin: *`
> **认证 (ADR-0047)**: 除 `/healthz` 和 `/api/auth/*` 外所有端点要求 `Authorization: Bearer <token>`。WebSocket 端点通过 `?token=<JWT>` query param 认证。
> **会议隔离 (ADR-0050)**: 所有单会议端点 (state/docs/events/aggregate/collab/chat/close/materials) 仅 owner 可访问, 非 owner 返回 `403`
> **百炼 ASR (ADR-0051)**: API Key 从 `DASHSCOPE_API_KEY` 环境变量读取, 仓库不存明文; 服务端**必须**通过 `bash run.sh` 启动以注入 key。
> **⚠️ Breaking in v0.20**: `upload_audio`、`stream_chunk`、`stream_stop` 已移除，30s 切片模式已废弃，请使用 WebSocket 实时 ASR。
>
> **v0.22.7 关键变更**:
> - **暂停 ≠ 结束 (ADR-0055)**: 客户端 `stop_capture` 新增 `close_meeting` 参数 — 暂停录音不再调用 `POST /close`，SSE 保持连接，前端不再误显示"未连接"
> - **chat 历史注入子 agent (ADR-0055)**: `format_state_summary()` 读取 `{mid}.chat.json`，最近 20 条对话 + 完整文件路径注入 prompt，子 agent 可 `read_file` 读全量；上传文件路径同样暴露给子 agent
> - **`_close_meeting()` 延迟关闭**: 不再立即 `close_meeting()` 杀 SSE，改为 120s 后台线程兜底关闭
>
> **v0.22.8 关键变更**:
> - **百炼 idle timeout 自动重连**: 用户长时间不说话 → 百炼 WS 关闭 → 服务端检测 `needs_reconnect` → 自动 `restart_session()` 重建 Recognition，客户端 WS 不断、不推 `recording-disconnected`
> - **WS 与 SSE 完全解耦 (客户端 v0.22.8)**: 暂停录音不再关闭 SSE 长连接。录音(WS)和事件推送(SSE)是独立通道——停止录音后文档生成、demo 更新、chat 消息继续通过 SSE 推流，会议保持活跃
> - **停止录音按钮即时响应 (客户端 v0.22.8)**: 点击"停止录音"后 JS 立即更新按钮状态为"开始录音"，不再等待 Rust 侧 30 秒延迟；`meeting-complete` 事件不覆盖暂停状态
> - **agent sandbox 强化 (prompt 铁律)**: demo/batch_docs/single-agent 三处 prompt 新增——严禁读取/提及服务器文件系统路径、主机用户名(/home/xxx)、环境变量；禁止用终端工具探索系统(whoami/uname/hostname/等)；种子/示例数据禁用可能泄露身份的信息，只用中性占位符
> - **`DELETE /api/meetings/{id}` 资源清理完善**: 新增清理 uploads 目录、KB Chroma 记录、`_AGENT_CACHE`/`_CHAT_AGENT_CACHE`/`_CLEAN_AGENT_CACHE`、experience 候选文件；返回 `deleted` 对象增加 `uploads`/`kb`/`agents`/`experiences` 字段
> - **Experience 自排除**: `search_experiences()` 新增 `exclude_meeting_id` 参数，batch_docs 调用时排除当前会议自身经验，防止自我循环引用
> - **`handle_chat_upload` KB metadata 补全**: 补 `scope=meeting_material`、`labels`、`meeting_callable` 字段，与 `handle_kb_upload` 保持一致
> - **`stream_start` reuse 保留转录**: 断线重连时不再重置 `transcript_segments`，保留之前的转写记录
> - **图片上传/chat 非阻塞化**: `handle_chat_upload` + `_run_vp_chat` 通过 `await loop.run_in_executor()` 在线程池中执行，不阻塞 event loop，WS ASR 持续收音频帧
> - **图片上传后强制触发文档重生成**: `post_chat` 图片路径非空时通过 `task_manager.submit()` 提交 BATCH_DOCS_KIND + DEMO_KIND
>
> **v0.22.6 关键变更**:
> - **vision 三层逃生通道 (ADR-0054)**: OpenAI 兼容端点 (DashScope qwen-vl-max) → monkeypatch Hermes 路由 → mmx-cli MiniMax 原生 VLM 后备，确保图片识图在任何情况下都不 401
> - **新增 toolsets**: agent 从 `["terminal","file"]` 扩展为 `["terminal","file","vision","web"]`（vision 读图、web DDG 搜索）
> - **KB search POST 非阻塞**: `async def` → `await run_in_executor(None, ...)`，不再阻塞 event loop
> - **.env 自动加载**: 服务启动时从 `.env` 注入 `DASHSCOPE_API_KEY` 等环境变量（多路径 fallback + `OPENAI_*` 从 `DASHSCOPE_API_KEY` 兜底推导）
> - **gkd 无字数阈值**: hash-based 触发，不设字数枷锁；空文本 `< 1 字` 跳过（防误触发）
> - **Vision 配置看护**: Hermes `auxiliary.vision` 需 `provider: custom` + `model: qwen-vl-max` + DashScope key
> - **mmx-cli 后备**: `npm install -g mmx-cli` + `mmx auth login`，图片上传时 OpenAI 主路径失败自动走 MiniMax 原生 VLM
> - `doc-update` SSE 不再推送 `content` 字段（只推元信息 `{kind, status, doc_size}`）
> - SSE 重连支持增量恢复（读取客户端 `Last-Event-ID` header/query）
> - Chat 文件上传不塞内容只放路径（agent 用 `read_file` 按需读取）
> - 图片上传 → OpenAI vision API 异步分析 → mmx-cli 备份 → 结果追加到 chat 并入库 KB
> - KB 去重按 `user_id` 隔离（`content_hash` 查询加 `user_id` 过滤）

---

## 目录

1. [快速开始](#1-快速开始)
2. [通用约定](#2-通用约定)
3. [认证](#3-认证)
   - [POST /api/auth/register](#31-注册)
   - [POST /api/auth/login](#32-登录)
   - [GET /api/auth/me](#33-校验当前用户)
4. [会议](#4-会议)
   - [GET /api/meetings](#41-列出所有会议)
   - [GET /api/meetings/{id}/state](#42-获取会议状态)
   - [GET /api/meetings/{id}](#43-获取会议详情)
   - [PATCH /api/meetings/{id}](#44-更新会议标题)
   - [DELETE /api/meetings/{id}](#45-删除会议)
   - [POST /api/meetings/stream_start](#46-创建流式会议)
   - [WS /api/meetings/{id}/realtime_asr](#47-websocket-实时-asr百炼-fun-asr-realtime)
   - [POST /api/meetings/{id}/close](#48-结束会议)
   - [GET /api/meetings/check_id](#49-校验会议名)
5. [文档](#5-文档)
   - [GET /api/meetings/{id}/docs](#51-获取全部文档)
   - [GET /api/meetings/{id}/docs/{kind}](#52-获取单个文档)
   - [GET /api/meetings/{id}/docs/{kind}/download](#53-下载文档文件)
   - [GET /api/meetings/{id}/demo/versions](#54-获取demo版本列表)
6. [Chat 对话](#6-chat-对话)
   - [POST /api/meetings/{id}/chat](#61-发送chat消息)
   - [GET /api/meetings/{id}/chat/history](#62-获取chat历史)
7. [协作提问](#7-协作提问)
8. [SSE 实时事件流](#8-sse-实时事件流)
9. [知识库 (KB)](#9-知识库-kb)
   - [GET /api/kb/search](#91-kb搜索get)
   - [POST /api/kb/search](#92-kb搜索post)
   - [POST /api/kb/upload](#93-上传文件到kb)
   - [GET /api/kb/list](#94-kb统计)
   - [DELETE /api/kb/{doc_id}](#95-删除kb文档)
   - [GET /api/kb/{doc_id}/file](#96-下载kb原文件)
10. [会议材料 (v0.19.0)](#10-会议材料)
   - [GET /api/meetings/{id}/materials](#101-列出会议材料)
   - [POST /api/meetings/{id}/materials](#102-上传会议材料)
   - [GET /api/materials/{id}](#103-材料详情)
   - [DELETE /api/materials/{id}](#104-删除材料)
11. [文件下载](#11-文件下载)
   - [GET /api/meetings/{id}/docs/{kind}/download](#111-下载交付物文件)
   - [GET /api/materials/{id}/file](#112-下载会议材料文件)
   - [GET /api/kb/{doc_id}/file](#113-下载知识库文件)
12. [AI 设置 (v0.19.0)](#12-ai-设置)
   - [GET /api/settings/ai](#121-获取ai配置)
   - [PUT /api/settings/ai](#122-保存ai配置)
   - [POST /api/settings/ai/test](#123-测试ai连接)
13. [经验蒸馏 (v0.19.0)](#13-经验蒸馏)
   - [GET /api/experiences](#131-已确认经验列表)
   - [GET /api/experiences/candidates](#132-会议经验候选)
   - [POST /api/experiences/{id}/approve](#133-确认经验)
   - [POST /api/experiences/{id}/reject](#134-拒绝经验)
14. [系统](#14-系统)
   - [GET /healthz](#140-健康检查)
   - [GET /api/status](#141-服务状态)
   - [GET /api/timeline](#142-时间线)

---

## 1. 快速开始

### 认证 (必读)

```bash
# 注册新用户, 获得 JWT token (72h 有效)
curl -X POST http://47.100.182.3:28765/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"you@example.com","password":"your-password"}'

# 响应: {"user_id":"...", "email":"you@example.com", "token":"eyJ..."}

# 所有后续请求携带 token
TOKEN="eyJ..."
curl -H "Authorization: Bearer $TOKEN" http://47.100.182.3:28765/api/meetings
```

### 实时会议 (WS 百炼 ASR — 推荐)

```bash
# 1. 创建流式会议 (需要 token)
curl -X POST "http://47.100.182.3:28765/api/meetings/stream_start?meeting_id=my-meeting&audio_source=microphone" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"platform": "cli"}'

# 2. WebSocket 实时 ASR — 需要 token query param
# ws://47.100.182.3:28765/api/meetings/my-meeting/realtime_asr?token=$TOKEN

# 3. 15 秒后自动生成文档, 查看
curl -H "Authorization: Bearer $TOKEN" \
  http://47.100.182.3:28765/api/meetings/my-meeting/docs
```

### 后续操作

```bash
# 更新会议标题
curl -X PATCH "http://47.100.182.3:28765/api/meetings/my-meeting" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"project_name":"新标题"}'

# 删除会议
curl -X DELETE "http://47.100.182.3:28765/api/meetings/my-meeting" \
  -H "Authorization: Bearer $TOKEN"
```

---

## 2. 通用约定

### 响应格式

- **成功**: JSON，根对象包含请求数据
- **错误**: 返回 HTTP 4xx/5xx，body 为 `{"detail": {"error": "描述", "status": 4xx}}` 或旧格式 `{"error": "描述", "status": 4xx}`
- **纯文本端点**: 静态文件服务返回对应 Content-Type

### 认证

所有 API 端点（除 `/healthz` 和 `/api/auth/*`）要求 `Authorization: Bearer <token>` header。无 token 返回 `401 Unauthorized`。WebSocket 端点通过 `?token=<JWT>` query param 认证。token 通过注册或登录获取，JWT 72 小时过期。参见 [§3 认证](#3-认证)。

### 知识库隔离 (ADR-0047)

知识库按 `user_id` 隔离——每个用户上传的文件只会被自己的检索结果命中。`POST /api/kb/search` 在 Chroma metadata 中按 `user_id` 过滤。

### 会议所有权隔离 (ADR-0050)

`GET /api/meetings` 只返回当前用户的会议。单会议端点（`/state`、`/chat/history`）仅 owner 可访问，非 owner 认证用户返回 `403 Forbidden`。参见 [ADR-0050](decisions/0050-meeting-owner-isolation.md)。

### 会议名规则

会议 ID 必须是 3-48 字符, 仅含 `[A-Za-z0-9_-]`。中文请用 `project_name` 字段。

### SSE 事件流

所有实时事件通过 `Transfer-Encoding: chunked` 推流。事件格式:

```
event: {event_type}
data: {json_string}

```

事件类型: `asr_status` / `asr_complete` / `asr_error` / `transcript` / `recording-disconnected` / `doc-update` / `chat-message` / `collab-update` / `meeting-complete` / `demo-new-version`

---

## 3. 认证

### 3.1 注册

```
POST /api/auth/register
```

创建新用户，成功后直接返回 JWT token。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| email | string | 是 | 邮箱（唯一），自动转小写 |
| password | string | 是 | 密码（≥6 位），bcrypt 存储 |

**成功响应** `200`:
```json
{
  "user_id": "a1b2c3d4e5f6",
  "email": "you@example.com",
  "token": "eyJhbGciOiJIUzI1NiIs..."
}
```

**错误响应**:
| 状态码 | 说明 |
|--------|------|
| 400 | 邮箱格式无效 / 密码 < 6 位 |
| 409 | 邮箱已注册 |

```bash
curl -X POST http://47.100.182.3:28765/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"you@example.com","password":"yourpassword"}'
```

### 3.2 登录

```
POST /api/auth/login
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| email | string | 是 | 邮箱 |
| password | string | 是 | 密码 |

**成功响应** `200`: 同注册，返回 `{user_id, email, token}`。

**错误响应**:
| 状态码 | 说明 |
|--------|------|
| 400 | 邮箱/密码为空 |
| 401 | 邮箱或密码错误 |

```bash
curl -X POST http://47.100.182.3:28765/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"you@example.com","password":"yourpassword"}'
```

### 3.3 校验当前用户

```
GET /api/auth/me
```

验证 token 有效性并返回用户信息。**注意：此端点也要求 Bearer token**——它既是验证端点也是需要认证的端点。

**Header**: `Authorization: Bearer <token>`

**成功响应** `200`:
```json
{
  "user_id": "a1b2c3d4e5f6",
  "email": "you@example.com",
  "created_at": "2026-07-07T14:30:00.000Z"
}
```

| 状态码 | 说明 |
|--------|------|
| 401 | token 缺失 / 无效 / 已过期 |
| 404 | 用户已删除 |

```bash
curl -H "Authorization: Bearer $TOKEN" \
  http://47.100.182.3:28765/api/auth/me
```

---

## 4. 会议

### 4.1 列出所有会议

```
GET /api/meetings
```

**响应示例**:
```json
{
  "meetings": [
    {
      "meeting_id": "api_908de970",
      "owner_id": "f34bd8df1dc94d10",
      "platform": "local",
      "audio_source": "microphone",
      "project_name": "产品评审会",
      "started_at": "2026-07-04T17:42:09",
      "last_updated": "2026-07-04T17:43:00",
      "item_count": 1
    }
  ],
  "count": 1
}
```

`owner_id` 为会议创建者的 user_id (ADR-0047)。

---

### 4.2 获取会议状态

```
GET /api/meetings/{id}/state
```

返回 `MeetingState` JSON，包含 `state.owner_id`、`state.cleaned_text`（累积 ASR 文本）、`meeting_id`、`platform`、`audio_source`、`last_updated` 等字段。

**权限 (ADR-0050)**: 仅会议 owner 可访问。非 owner 返回 `403 Forbidden`。

---

### 4.3 获取会议详情

```
GET /api/meetings/{id}
```

**v0.20 新增**。返回会议聚合信息，包含 state、docs 摘要等。需 owner 校验。

---

### 4.4 更新会议标题

```
PATCH /api/meetings/{id}
```

**v0.20 新增**。更新会议 `project_name`。

**请求 JSON**:
```json
{"project_name": "新标题"}
```

**权限 (ADR-0050)**: 仅会议 owner 可操作。非 owner 返回 `403`。

---

### 4.5 删除会议

```
DELETE /api/meetings/{id}
```

**v0.20 新增**。删除会议 state、chat history、materials、docs 目录。v0.22.8: 新增清理 uploads、KB Chroma、agent cache、experience 候选。

**响应**: `{"meeting_id": "{id}", "deleted": {"state": true, "chat": true, "materials": 3, "docs": true, "stream_meta": true, "uploads": 5, "kb": 12, "agents": 2, "experiences": true}}`

**权限 (ADR-0050)**: 仅会议 owner 可操作。

---

### 4.6 创建流式会议

```
POST /api/meetings/stream_start
```

**Query 参数**:
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| meeting_id | string | 否 | 复用已有会议 ID (不传则自动生成) |
| audio_source | string | 否 | `microphone` / `loopback` / `both` (默认 microphone) |
| project_name | string | 否 | 会议名称 (v0.20 新增, 默认 "长连接会议 {id}") |

**响应**:
```json
{
  "meeting_id": "api_908de970",
  "audio_source": "microphone",
  "reused": false,
  "message": "Stream started, connect via WebSocket /api/meetings/{id}/realtime_asr"
}
```

创建会议后会初始化 `MeetingState`（含空的 `cleaned_text` 字段）。后续通过 WebSocket 推送音频。

---

### 4.7 WebSocket 实时 ASR（百炼 Fun-ASR-Realtime）

```
WS /api/meetings/{id}/realtime_asr
```

**⚠️ v0.20 变更**: 必须在 URL 中携带 `token` query param: `ws://.../realtime_asr?token=<JWT>`。无 token 或无效 token 返回 `{"type":"error","error":"token 无效或缺失"}` 并关闭连接。

**协议**: WebSocket，全双工。客户端先发 JSON 控制消息，然后持续发 binary PCM 帧。服务端每句完成后推送 `transcript` JSON 消息。

**控制消息** (客户端 → 服务端):

```json
{"type": "start", "format": "pcm", "sample_rate": 16000}
```

**音频帧**: binary, 16kHz mono 16-bit PCM little-endian。推荐每帧 20ms (= 640 bytes)。

**停止**: 发 `{"type": "stop"}` 结束录制。**v0.21.3 变更**: 只有客户端显式发送 `stop` 才会触发会议 finalize (文档生成+经验蒸馏)。WebSocketDisconnect (网络断连/切网/代理 502) 不再错误地 close meeting。断连时服务端推送 `recording-disconnected` SSE 事件。

**服务端推送** (服务端 → 客户端):

| type | 说明 |
|------|------|
| `asr_status` | `{"status": "connected"/"closed"}` — 百炼连接状态 |
| `transcript` | `{"text": "...", "begin_time": ms, "end_time": ms, "is_sentence_end": bool, "is_noise": bool, "speaker_id": "UNKNOWN"}` — v0.21.3 新增 `is_noise` (噪声标记) 和 `speaker_id` (当前固定 "UNKNOWN"，百炼不做说话人分离) |
| `asr_complete` | `{"sentence_count": N, "full_text": "..."}` — 识别完成 |
| `asr_error` | `{"error": "..."}` — 百炼错误 |

**特点**:
- 百炼 fun-asr-realtime 全程同一条 WebSocket 双工流，模型内部利用上文语音特征提升下文识别
- 每句完成时自动写入 `MeetingState.cleaned_text`，**v0.21.3 变更**: 写入的是经过降噪过滤的 `cleaned_accumulated_text`（过滤填充词/设备测试短语/无意义重复），非原始累积文本
- **v0.21.3 变更**: 文档调度改为 hash-based 检测有意义变更，debounce 6s（不再使用 15s 无条件首轮 + 30s 字符增量策略）
- **v0.21.3 变更**: 断线不误关会议，会议数据保留可用于后续重连
- **v0.22.8 百炼重连**: 百炼 WS 长时间无有效语音会关闭 (idle timeout ~10-20s)，服务端检测到 `on_close` → 标记 `needs_reconnect` → 下次 `send_audio` 自动 `restart_session()` 重建 Recognition。客户端 WS **不断开**，SSE **不推** `recording-disconnected`。用户重新开始说话时 ASR 自动恢复。

**典型流程**:
```
客户端                                     GPU 服务器
  │                                           │
  │  POST /api/meetings/stream_start          │
  │  ─────────────────────────────────>        │
  │  ← {meeting_id}                           │
  │                                           │
  │  WS /api/meetings/{id}/realtime_asr        │
  │  ─────────────────────────────────>        │
  │  → {"type":"start","format":"pcm",...}    │
  │  ← {"type":"asr_status","status":"connected"}│
  │                                           │
  │  → [binary PCM 音频帧 × N]                │
  │  ← {"type":"transcript","text":"...",...} │ ← 实时转写
  │  ← {"type":"transcript","text":"...",...} │
  │                                           │
  │  ═══ 15s 后 ═══                           │
  │  文档自动生成 (poll 触发)                   │
  │  ← event: doc-update (6 次)               │ ← 通过 SSE events 端点
  │                                           │
  │  → {"type":"stop"}                        │
  │  ← {"type":"asr_complete",...}            │
  │  连接断开                                  │
```

---

### 4.8 结束会议

```
POST /api/meetings/{id}/close
```

**v0.22.7 变更**: `_close_meeting()` 不再立即 `close_meeting()` 杀 SSE，改为 120s 后台线程兜底关闭。客户端暂停录音 (**不**带 `close_meeting=true`) 不会调用此端点，SSE 保持连接。v0.22.8: 客户端暂停录音 (不带 `close_meeting=true`) 不触发 POST /close 是 v0.22.7 的客户端行为，本次无额外变更。

**说明**: 推送 `meeting-complete` SSE 事件 → 清 proactive 节流 → 触发经验蒸馏 → 提交最终文档生成任务。

### 4.8.1 暂停 vs 结束 (v0.22.7 客户端行为)

> **v0.22.8 更新**: 暂停录音 **不再关闭 SSE 长连接**。录音 (WS) 和事件推送 (SSE) 是两个独立通道——停止录音后文档生成、demo 更新、chat 消息继续通过 SSE 推流，会议保持活跃。前端按钮**即时**切换为"开始录音"，用户可随时重新开始录音。

客户端 `stop_capture` 入口区分以下场景:

> **v0.22.7 起** 客户端 `stop_capture` 通过 `close_meeting` 参数区分暂停与结束，服务端据此决定是否触发 `POST /close`。

| 操作 | 客户端调用 | 服务端行为 | SSE |
|------|-----------|-----------|:--:|
| 暂停录音 | `stop_capture()` (默认 `close_meeting=false`) | WebSocket 断开，会议保持开放 | ✅ 保持 |
| 结束会议 | `stop_capture({close_meeting: true})` | WS 断开 → `POST /close` → 经验蒸馏 + 文档生成 | 120s 后关 |

---

### 4.9 校验会议名

```
GET /api/meetings/check_id?id=XXX
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| id | string | 是 | 会议名 (3-48 字符, `[A-Za-z0-9_-]`) |

---

## 5. 文档

### 5.1 获取全部文档

```
GET /api/meetings/{id}/docs
```

**响应**:
```json
{
  "meeting_id": "{id}",
  "docs": [
    { "kind": "req",  "label": "需求", "content": "# 需求\n...", "version": "1", "status": "stored" },
    { "kind": "arch", "label": "架构", "content": "# 架构\n...", "version": "1", "status": "stored" },
    { "kind": "tasks","label": "任务", "content": "# 任务\n...", "version": "1", "status": "stored" },
    { "kind": "api",  "label": "接口", "content": "# 接口\n...", "version": "1", "status": "stored" },
    { "kind": "risk", "label": "风险", "content": "# 风险\n...", "version": "1", "status": "stored" },
    { "kind": "demo", "label": "演示", "content": "<html>...",  "version": "2", "status": "stored" }
  ]
}
```

**文档类型**:
| kind | 说明 | 内容风格 |
|------|------|----------|
| req | 需求分析 | bullet points, 中文 |
| arch | 架构方案 | 方案描述或占位 |
| tasks | 任务拆解 | 责任人 + 截止时间 |
| api | 接口定义 | REST/API 描述或占位 |
| risk | 风险评估 | 含 RISK-XXXXXX 唯一编号 |
| demo | HTML 原型 | 可交互 HTML 页面 (多版本) |

**文档生成生命周期** (v0.16+, v0.22.5 更新):

```
WebSocket ASR 连接建立
  → 每句完成 → 写入 MeetingState.cleaned_text
  → gkd 守护线程每 6s 扫描, 非空 cleaned_text 变化时触发文档生成 (hash-based, 无字数阈值)
     → batch_docs agent (5 文档, 1 次 LLM 调用)
     → demo agent (HTML 原型, 独立 session, 并行)
  → cleaned_text 有增量变化 → 自动重触发
  → 文档就绪 (总 ~25-60s)

⚠️ v0.22.5: demo 写入版本时检查内容合法性—HTML <3KB 且含"等待更多会议内容"/"暂无会议内容" → 拒绝写入版本
⚠️ v0.22.4: SSE 生命周期与音频采集解耦 — 停采集后 SSE 保持 30s 以接收 demo-new-version 等事后事件
```

---

### 5.2 获取单个文档

```
GET /api/meetings/{id}/docs/{kind}
```

| 参数 | 类型 | 说明 |
|------|------|------|
| kind | string | `req` / `arch` / `tasks` / `api` / `risk` / `demo` |

---

### 5.3 下载文档文件 (v0.19.0)

```
GET /api/meetings/{id}/docs/{kind}/download
```

返回文档文件原始内容，`Content-Disposition: attachment` 触发浏览器下载。`kind=demo` 返回 `demo.html`，其余返回 `{kind}.md`。需认证 + owner 校验。

---

### 5.4 获取 Demo 版本列表

```
GET /api/meetings/{id}/demo/versions
```

---

## 6. Chat 对话

### 6.1 发送 Chat 消息

```
POST /api/meetings/{id}/chat
```

**模式 A — 纯文本**: `Content-Type: application/json`
```json
{ "message": "这个会议主要讨论了什么？" }
```

**模式 B — 文件上传**: `Content-Type: multipart/form-data`
| 字段 | 类型 | 说明 |
|------|------|------|
| text | string | 对话文本 (可选) |
| files | file[] | 文件 (文本入 KB, 图片转 base64) |

### 6.1.1 Chat 与子 Agent 上下文 (v0.22.7, ADR-0055)

VP Chat 对话历史自动注入 batch_docs 和 demo 子 agent 的 prompt：
- 最近 20 条对话以格式化文本注入 `format_state_summary()`
- 超长消息 (>2000 字) 截断，但**完整 `{mid}.chat.json` 路径**暴露给子 agent
- 子 agent 可按需调用 `read_file` 读取全量对话历史
- 上传的文件路径同样在 prompt 中列出

> ⚠️ 设计背景：Hermes `parent_session_id` 实测**只存 DB 血缘标记不消费**，对​话循环全程不读父 session 历史。VPBuddy 在应用层手动注入 chat 历史到子 agent prompt (详见 [ADR-0055](decisions/0055-parent-session-fork-not-working-chat-history-injection.md))。

---

### 6.2 获取 Chat 历史

```
GET /api/meetings/{id}/chat/history
```

---

## 7. 协作提问

### 7.1 提问 / 7.2 回答 / 7.3 获取记录

```
POST /api/meetings/{id}/ask_question?section=X&question=Y&asker=Z
POST /api/meetings/{id}/answer_question?qid=X&answer=Y&answerer=Z
GET  /api/meetings/{id}/collab
```

---

## 8. SSE 实时事件流

### 8.1 SSE 事件流

```
GET /api/meetings/{id}/events
```

**SSE 事件类型**:

| 事件名 | data 关键字段 | 触发时机 |
|--------|-------------|----------|
| `transcript` | `{text, begin_time, end_time, is_sentence_end}` | 百炼每句转写完成 |
| `asr_status` | `{status: "connected"/"closed"}` | 百炼连接状态变化 |
| `asr_complete` | `{sentence_count, full_text}` | 识别完成 |
| `asr_error` | `{error}` | 百炼错误 |
| `doc-update` | `{kind, status, doc_size}` | 某个文档生成完成 (v0.22.6: 不再含 `content`，客户端按需 GET 获取) |
| `demo-new-version` | `{version, summary, file_size, file}` | 新 demo 版本写入 (v0.22.5: 客户端自动刷新版本列表) |
| `chat-message` | `{role, content, source, ...}` | Chat 助理消息 |
| `collab-update` | `{action, qid, section, question, answer?}` | 协作提问/回答 |
| `meeting-complete` | `{status: "user_closed"}` | 用户主动关闭会议 (POST /close) |
| `recording-disconnected` | `{status: "disconnected"}` | 录制连接断开。v0.22.8: 百炼自动重连后此事件**不再推送**——仅当百炼重连也失败时才推送 |

**SSE 断线恢复 (v0.22.6)**: 客户端重连时传 `Last-Event-ID` header 或 `?last_event_id=...` query param，服务端只推送该 ID 之后的新事件，不再全量重放。

---

## 9. 知识库 (KB)

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/kb/search?q=X&meeting_id=Y` | 关键词搜索 (threadpool) |
| POST | `/api/kb/search` | JSON 搜索 `{"query":"...", "top_k":5}` (非阻塞 — `run_in_executor`) |
| POST | `/api/kb/upload` | 上传文件 (.txt/.md/.pdf, ≤50MB) |
| GET | `/api/kb/list?meeting_id=Y` | KB 统计 + 文档列表 |
| DELETE | `/api/kb/{doc_id}` | 删除文档 (v0.21.1+: 需认证+owner校验，非文件 owner 返回 403) |
| GET | `/api/kb/{doc_id}/file` | 下载 KB 文档原始文件 |

### KB 上传参数 (v0.19.0)

`POST /api/kb/upload` multipart 新增可选字段:

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| scope | string | `personal_kb` | 知识库范围: `personal_kb` / `enterprise` / `industry` |
| labels | string | 空 | 逗号分隔标签, 如 `"ESG,碳管理"` |
| meeting_callable | string | `true` | 本次会议是否可调用: `"true"` / `"false"` |

上传响应中额外返回 `scope`、`labels`、`meeting_callable` 字段。KB 列表和搜索结果中的 `metadata` 也包含这些字段。

---

## 10. 会议材料 (v0.19.0)

所有材料端点均需认证 + owner 校验 (ADR-0050)。

### 10.1 列出会议材料

```
GET /api/meetings/{id}/materials
```

**响应**:
```json
{
  "meeting_id": "api_xxxx",
  "materials": [
    {
      "id": "mat_abc123",
      "meeting_id": "api_xxxx",
      "filename": "方案.pptx",
      "content_type": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
      "size": 18600000,
      "created_at": "2026-07-08T12:00:00+00:00",
      "status": "stored"
    }
  ],
  "count": 1
}
```

### 10.2 上传会议材料

```
POST /api/meetings/{id}/materials
```

`multipart/form-data`:
- `file`: 原始文件（必需）
- 支持格式: `.txt .md .pdf .png .jpg .pptx .docx .xlsx .csv .json .mp3 .wav .mp4` 等

上传后自动：保存 Material 实体 → 文本类喂给 Hermes → 异步入 KB。

**图片 Vision 分析管道 (v0.22.6, ADR-0054)**:

图片上传后走三层后备链路：

```
图片文件上传
  → 主路径: OpenAI /chat/completions (DashScope qwen-vl-max)
  → 后备 1 (monkeypatch): resolve_runtime_provider 注入 → _create_openai_client(DashScope)
  → 后备 2 (mmx-cli): mmx vision describe (MiniMax 原生 VLM, 不经过 Hermes)
  → 结果追加到 chat (source: "vision-analysis") + 以文本形式入库 KB
```

| 通道 | 技术 | 触发条件 |
|------|------|----------|
| OpenAI 兼容 | DashScope `qwen-vl-max` `/chat/completions` | 主路径，有 `OPENAI_API_KEY` / `MINIMAX_API_KEY` 时 |
| monkeypatch | Hermes `_resolve_custom_runtime` → `_create_openai_client` | AIAgent 创建前注入，防止路由到 OpenRouter |
| mmx-cli | `mmx vision describe --image <file>` | 主路径失败 / 无 API key / 返回空结果时 |

图片分析完成后，描述文本通过 SSE `chat-message` 推送给客户端（`source: "vision-analysis"` 或 `"vision-analysis-mmx"`）。

### 10.3 材料详情

```
GET /api/materials/{id}
```

返回 Material 元数据（文件名、大小、类型、状态等），不含文件原始内容。

### 10.4 删除材料

```
DELETE /api/materials/{id}
```

删除材料文件目录 + 从会议 index 中移除。返回 `{"deleted": true}`。

---

## 11. 文件下载 (v0.19.0)

三种文件下载接口, 均需认证 + owner 校验, 返回 `Content-Disposition: attachment`:

### 11.1 下载交付物文件

```
GET /api/meetings/{id}/docs/{kind}/download
```

### 11.2 下载会议材料文件

```
GET /api/materials/{id}/file
```

### 11.3 下载知识库文件

```
GET /api/kb/{doc_id}/file
```

> **合并导出**: 打包 ZIP / 生成 PDF 汇总由前端负责, 服务端只提供单个文件下载。

---

## 12. AI 设置 (v0.19.0)

每用户独立配置, 存储在 `data/settings/ai/{user_id}.json`. API Key 明文存储但 GET 返回时脱敏。

### 12.1 获取 AI 配置

```
GET /api/settings/ai
```

**响应示例**:
```json
{
  "provider": "openai-compatible",
  "model": "minimax-m3",
  "base_url": "https://api.minimax.chat/v1",
  "api_key_masked": "sk-****abcd",
  "api_key_configured": true,
  "updated_at": "2026-07-08T05:06:32+00:00"
}
```

未配置时返回 `{"api_key_configured": false, "status": "not_configured"}`.

### 12.2 保存 AI 配置

```
PUT /api/settings/ai
```

**请求 JSON**:
```json
{
  "provider": "openai-compatible",
  "model": "minimax-m3",
  "base_url": "https://api.minimax.chat/v1",
  "api_key": "sk-your-key-here"
}
```

所有字段可选, 空字符串表示清空对应值. 返回 `{"status": "saved", "updated_at": "..."}`.

### 12.3 测试 AI 连接

```
POST /api/settings/ai/test
```

用当前保存的配置创建临时 AIAgent → 发送 "回复 OK" → 验证连通性. **不修改任何文件、不调用任何工具。**

**成功响应**:
```json
{
  "status": "connected",
  "connected": true,
  "model": "minimax-m3",
  "provider": "openai-compatible",
  "elapsed_ms": 6020
}
```

**失败响应**:
```json
{
  "status": "failed",
  "connected": false,
  "error": "LLM returned error: 401 Unauthorized",
  "model": "minimax-m3",
  "elapsed_ms": 1234
}
```

---

## 13. 经验蒸馏 (v0.19.0)

会议结束后自动从 MeetingState 提取经验候选，用户可确认/拒绝。
已确认经验存入聚合索引 `data/experiences/_all.json`，后续会议自动注入上下文。

### 13.1 已确认经验列表

```
GET /api/experiences
```

**响应**:
```json
{
  "experiences": [
    {
      "id": "exp-abc123",
      "kind": "domain_fact",
      "text": "REQ: 实验平台需支持实时数据采集",
      "domain": "物理实验",
      "confidence": 0.6,
      "approved": true,
      "source_meeting_id": "meeting-001",
      "created_at": "2026-07-08T12:00:00+00:00"
    }
  ],
  "count": 1
}
```

### 13.2 会议经验候选

```
GET /api/experiences/candidates?meeting_id={meeting_id}
```

需 owner 校验。返回该会议提取的所有经验候选（含未确认项）。

### 13.3 确认经验

```
POST /api/experiences/{item_id}/approve
```

**请求 JSON**: `{"meeting_id": "..."}`

需 owner 校验。将经验标记为 approved=true，同步入聚合索引。

### 13.4 拒绝经验

```
POST /api/experiences/{item_id}/reject
```

**请求 JSON**: `{"meeting_id": "..."}`

需 owner 校验。从会议文件 + 聚合索引中永久移除该条候选。

---

## 14. 系统

### 14.0 健康检查

```
GET /healthz
```

**v0.20 新增**。公开端点，无需认证。用于负载均衡/监控探测。

**响应** `200`:
```json
{"ok": true}
```

---

### 14.1 服务状态

```
GET /api/status
```

**⚠️ v0.20 变更**: 现在需要认证 (`Authorization: Bearer <token>`)。无 token 返回 `401`。

---

### 14.2 时间线

```
GET /api/timeline
```

---

## 附录

### 错误码

| 状态码 | 说明 |
|--------|------|
| 200 | 成功 |
| 400 | 请求参数错误 |
| 401 | 未认证 — token 缺失/无效/过期 |
| 403 | 无权限 — 非会议 owner (ADR-0050) |
| 404 | 资源不存在 |
| 409 | 冲突 — 邮箱已注册 |
| 500 | 服务端处理错误 |

### 数据目录

| 路径 | 说明 |
|------|------|
| `/data/vpbuddy/server/data/meetings/` | 会议 JSON 状态文件 (MeetingState) |
| `/data/vpbuddy/server/docs/{mid}/` | 6 文档 + demo HTML |
| `/data/vpbuddy/server/src/` | 服务端 Python 源码 |
| `/data/vpbuddy/server/data/experiences/` | 经验蒸馏 JSON |
| `/data/vpbuddy/server/data/uploads/{mid}/` | 会议上传文件 (文本+图片原始文件) |
| `/root/.mmx/config.json` | mmx-cli 登录凭据 (MiniMax API key, ADR-0054) |

### 近期变更 (v0.22.8)

| 版本 | 日期 | 变更 |
|------|------|------|
| v0.22.8 | 2026-07-14 | **百炼自动重连**: idle timeout → restart_session() 静默重建 + **WS/SSE 解耦**: 暂停录音不再关SSE，会议保持活跃 + **停止按钮即时响应**: JS先设状态再await + **meeting-complete不覆盖暂停** + **agent sandbox prompt铁律**: 禁读宿主用户名/环境变量 + **delete 完善清理** uploads/KB/agent-cache/experience + **experience exclude_meeting_id** 防自我引用 + **handle_chat_upload补scope** + **stream_start保留转录** + **chat/图片非阻塞** run_in_executor + **图片上传强制触发doc重生成** |
| v0.22.7 | 2026-07-13 | **暂停≠结束**: 客户端 `stop_capture({close_meeting: bool})` + `_close_meeting()` 120s 延迟兜底 + **chat历史注入子agent (ADR-0055)**: `format_state_summary()` 读 `{mid}.chat.json`，最近20条+完整路径暴露给batch_docs/demo |
| v0.22.6 | 2026-07-12 | vision三层逃生通道 (ADR-0054): OpenAI兼容 → monkeypatch → mmx-cli VLM后备 + toolsets扩展 + KB search非阻塞 + .env自动加载 + gkd无阈值 + mmx-cli安装 + SSE增量恢复 + KB去重 |
| v0.22.5 | 2026-07-12 | demo版本占位拒绝 (write_demo_version 拦截"等待更多会议内容") + gkd阈值 10→50字 + demo-new-version SSE链路完整 (Rust显式分支 + 前端自动刷新版本列表) |
| v0.22.4 | 2026-07-12 | SSE生命周期与采集解耦 (sse_active独立flag, 停采集后保持30s) + WS发送失败不再设capturing=false (防止服务端断百炼WS时误杀SSE) + 服务端必须bash run.sh启动 (注入BAILIAN_API_KEY/DASHSCOPE_API_KEY) |
| v0.21.12 | 2026-07-11 | (基线) |
