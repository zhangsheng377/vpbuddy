# VPBuddy HTTP API 参考

> **版本**: v0.19.0 · `@ 2026-07-08`
> **Base URL**: `http://47.100.182.3:28765`（公网 GPU 服务器）
> **协议**: HTTP/1.1 · WebSocket 实时 ASR · SSE 实时推送 · Multipart 上传
> **编码**: 所有请求/响应使用 UTF-8
> **CORS**: 所有端点返回 `Access-Control-Allow-Origin: *`
> **认证 (ADR-0047)**: 除 `/api/auth/*` 外所有端点要求 `Authorization: Bearer <token>`
> **会议隔离 (ADR-0050)**: 会议单端点 (state/chat history) 仅 owner 可访问, 非 owner 返回 `403`
> **百炼 ASR (ADR-0051)**: API Key 从 `DASHSCOPE_API_KEY` 环境变量读取, 仓库不存明文

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
   - [POST /api/meetings/upload](#43-上传音频自动生成文档)
   - [POST /api/meetings/stream_start](#44-创建流式会议)
   - [WS /api/meetings/{id}/realtime_asr](#45-websocket-实时-asr百炼-fun-asr-realtime)
   - [POST /api/meetings/{id}/stream_chunk](#46-推送音频切片http-模式)
   - [POST /api/meetings/{id}/stream_stop](#47-停止录音)
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
   - [GET /api/timeline](#141-时间线)

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
  -H "Authorization: Bearer $TOKEN"

# 2. WebSocket 实时 ASR — 连接后持续推送 PCM 音频帧
# ws://47.100.182.3:28765/api/meetings/my-meeting/realtime_asr

# 3. 15 秒后自动生成文档, 查看
curl -H "Authorization: Bearer $TOKEN" \
  http://47.100.182.3:28765/api/meetings/my-meeting/docs
```

### 上传音频 (离线模式)

```bash
curl -X POST http://47.100.182.3:28765/api/meetings/upload \
  -H "Authorization: Bearer $TOKEN" \
  -F "audio=@meeting.wav" \
  -F "project_name=产品评审会"
```

---

## 2. 通用约定

### 响应格式

- **成功**: JSON，根对象包含请求数据
- **错误**: 返回 HTTP 4xx/5xx，body 为 `{"detail": {"error": "描述", "status": 4xx}}` 或旧格式 `{"error": "描述", "status": 4xx}`
- **纯文本端点**: 静态文件服务返回对应 Content-Type

### 认证

所有 API 端点（除 `/api/auth/*`）要求 `Authorization: Bearer <token>` header。无 token 返回 `401 Unauthorized`。token 通过注册或登录获取，JWT 72 小时过期。参见 [§3 认证](#3-认证)。

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

事件类型: `transcript` / `asr_status` / `asr_complete` / `asr_error` / `doc-update` / `chat-message` / `collab-update` / `meeting-complete` / `demo-new-version`

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

### 4.3 上传音频自动生成文档

```
POST /api/meetings/upload
```

**请求**: `Content-Type: multipart/form-data`

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| audio | file | 是 | WAV 文件 (16kHz, mono, PCM) |
| project_name | string | 否 | 会议名称 |
| platform | string | 否 | 来源平台标识 |

---

### 4.4 创建流式会议

```
POST /api/meetings/stream_start
```

**Query 参数**:
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| meeting_id | string | 否 | 复用已有会议 ID (不传则自动生成) |
| audio_source | string | 否 | `microphone` / `loopback` / `both` (默认 microphone) |

**响应**:
```json
{
  "meeting_id": "api_908de970",
  "chunk_interval_sec": 30,
  "audio_source": "microphone",
  "reused": false,
  "message": "Stream started, send 30s WAV chunks to /api/meetings/{id}/stream_chunk"
}
```

创建会议后会初始化 `MeetingState`（含空的 `cleaned_text` 字段）。后续通过 WebSocket（推荐）或 HTTP chunk 推送音频。

---

### 4.5 WebSocket 实时 ASR（百炼 Fun-ASR-Realtime）

```
WS /api/meetings/{id}/realtime_asr
```

**协议**: WebSocket，全双工。客户端先发 JSON 控制消息，然后持续发 binary PCM 帧。服务端每句完成后推送 `transcript` JSON 消息。

**控制消息** (客户端 → 服务端):

```json
{"type": "start", "format": "pcm", "sample_rate": 16000}
```

**音频帧**: binary, 16kHz mono 16-bit PCM little-endian。推荐每帧 20ms (= 640 bytes)。

**停止**: 发 `{"type": "stop"}` 或关闭 WebSocket。

**服务端推送** (服务端 → 客户端):

| type | 说明 |
|------|------|
| `asr_status` | `{"status": "connected"/"closed"}` — 百炼连接状态 |
| `transcript` | `{"text": "...", "begin_time": ms, "end_time": ms, "is_sentence_end": bool}` — 转写结果 |
| `asr_complete` | `{"sentence_count": N, "full_text": "..."}` — 识别完成 |
| `asr_error` | `{"error": "..."}` — 百炼错误 |

**特点**:
- 百炼 fun-asr-realtime 全程同一条 WebSocket 双工流，模型内部利用上文语音特征提升下文识别
- 每句完成时自动写入 `MeetingState.cleaned_text`，文档生成 agent 实时读取
- 15 秒后自动触发第一轮文档生成（无需等 close）
- 持续轮询（每 30s），文本增量 >50 字自动重新触发

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

### 4.6 推送音频切片 (HTTP 模式)

```
POST /api/meetings/{id}/stream_chunk
```

**说明**: HTTP 多路音频上传（每 30s 一个 WAV 切片），适用于无法建立 WebSocket 的场景。

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| audio | file | 是 | WAV 切片 (30s, 16kHz, mono) |
| chunk_index | int | 是 | 切片序号 (从 0 开始) |
| chunk_start_sec | float | 是 | 切片开始时间 (秒) |
| overlap_sec | float | 否 | 与前一片重叠秒数 (默认 0) |

**Query 参数**:
| 参数 | 类型 | 说明 |
|------|------|------|
| sync | bool | `true`=同步阻塞等 ASR 完成, `false`=异步后台 (默认 true) |

---

### 4.7 停止录音

```
POST /api/meetings/{id}/stream_stop
```

关闭 SSE 订阅者，停止接收音频。与 close 不同，不结束会议。

---

### 4.8 结束会议

```
POST /api/meetings/{id}/close
```

**说明**: 推送 `meeting-complete` SSE 事件 → 关闭 SSE 订阅者 → 清 proactive 节流 → 触发经验蒸馏 → 提交最终文档生成任务。

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

**文档生成生命周期** (v0.16+):
```
WebSocket ASR 连接建立
  → 每句完成 → 写入 MeetingState.cleaned_text
  → 15s 后 → 自驱动 poll 提交第一轮 task_manager 任务
     → batch_docs agent (5 文档, 1 次 LLM 调用)
     → demo agent (HTML 原型, 独立 session)
  → 之后每 30s 检查 cleaned_text 增量, >50 字自动重触发
  → 文档就绪 (总 ~25-60s)
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
| `doc-update` | `{kind, status, doc_size, content?}` | 某个文档生成完成 |
| `demo-new-version` | `{version, summary}` | 新 demo 版本写入 |
| `chat-message` | `{role, content, source, ...}` | Chat 助理消息 |
| `collab-update` | `{action, qid, section, question, answer?}` | 协作提问/回答 |
| `meeting-complete` | `{status: "user_closed"}` | 会议结束 |

---

## 9. 知识库 (KB)

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/kb/search?q=X&meeting_id=Y` | 关键词搜索 |
| POST | `/api/kb/search` | JSON 搜索 `{"query":"...", "top_k":5}` |
| POST | `/api/kb/upload` | 上传文件 (.txt/.md/.pdf, ≤50MB) |
| GET | `/api/kb/list?meeting_id=Y` | KB 统计 + 文档列表 |
| DELETE | `/api/kb/{doc_id}` | 删除文档 |
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

### 14.1 时间线

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
