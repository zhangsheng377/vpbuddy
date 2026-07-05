# VPBuddy HTTP API 参考

> **版本**: v0.8.5 · `@ 2026-07-04`
> **Base URL**: `http://47.100.182.3:28765`（公网 GPU 服务器）
> **协议**: HTTP/1.1 · SSE 实时推送 · Multipart 音频上传
> **编码**: 所有请求/响应使用 UTF-8
> **CORS**: 所有端点（除静态文件外）返回 `Access-Control-Allow-Origin: *`

---

## 目录

1. [快速开始](#1-快速开始)
2. [通用约定](#2-通用约定)
3. [会议](#3-会议)
   - [GET /api/meetings](#31-列出所有会议)
   - [GET /api/meetings/{id}](#32-获取会议详情)
   - [POST /api/meetings/upload](#33-上传音频自动生成文档)
   - [POST /api/meetings/stream_start](#34-创建流式会议)
   - [POST /api/meetings/{id}/stream_chunk](#35-推送音频切片)
   - [POST /api/meetings/{id}/stream_stop](#36-停止录音)
   - [POST /api/meetings/{id}/close](#37-结束会议)
   - [GET /api/meetings/check_id](#38-校验会议名)
4. [文档](#4-文档)
   - [GET /api/meetings/{id}/docs](#41-获取全部文档)
   - [GET /api/meetings/{id}/docs/{kind}](#42-获取单个文档)
   - [GET /api/meetings/{id}/demo/versions](#43-获取demo版本列表)
5. [Chat 对话](#5-chat-对话)
   - [POST /api/meetings/{id}/chat](#51-发送chat消息)
   - [GET /api/meetings/{id}/chat/history](#52-获取chat历史)
6. [协作提问](#6-协作提问)
   - [POST /api/meetings/{id}/ask_question](#61-提问)
   - [POST /api/meetings/{id}/answer_question](#62-回答)
   - [GET /api/meetings/{id}/collab](#63-获取协作记录)
7. [SSE 实时事件流](#7-sse-实时事件流)
   - [GET /api/meetings/{id}/events](#71-sse-事件流)
8. [知识库 (KB)](#8-知识库-kb)
   - [GET /api/kb/search](#81-kb搜索get)
   - [POST /api/kb/search](#82-kb搜索post)
   - [POST /api/kb/upload](#83-上传文件到kb)
   - [GET /api/kb/list](#84-kb统计)
   - [DELETE /api/kb/{doc_id}](#85-删除kb文档)
9. [系统](#9-系统)
   - [GET /api/status](#91-系统状态)
   - [GET /api/timeline](#92-时间线)

---

## 1. 快速开始

### 最简单的场景 — 上传音频自动生成文档

```bash
# 1. 上传 30s WAV 音频
curl -X POST http://47.100.182.3:28765/api/meetings/upload \
  -F "audio=@meeting.wav" \
  -F "project_name=产品评审会"

# 返回:
# {
#   "meeting_id": "UPLOAD_20260704_174209_71171a80",
#   "transcript_segments": 14,
#   "num_speakers": 1,
#   "state_items": { "requirements": 0, "risks": 1, "questions": 0 },
#   "docs_ready_in_seconds": 30,
#   "message": "Audio processed, docs will be ready in ~30s"
# }

# 2. 等 60s 后查看生成的文档
curl http://47.100.182.3:28765/api/meetings/UPLOAD_20260704_174209_71171a80
# → docs 数组包含 6 个文档 (req/arch/tasks/api/risk/demo)

# 3. 获取某个文档正文
curl http://47.100.182.3:28765/api/meetings/UPLOAD_20260704_174209_71171a80/docs/req
```

### 流式会议 (客户端每 30s 推音频)

1. `POST /api/meetings/stream_start` — 创建会议
2. 循环 `POST /api/meetings/{id}/stream_chunk` — 每 30s 推一次
3. `GET /api/meetings/{id}/events` — SSE 长连接收实时转写
4. `POST /api/meetings/{id}/close` — 结束会议

---

## 2. 通用约定

### 响应格式

- **成功**: JSON，根对象包含请求数据
- **错误**: 返回 HTTP 4xx/5xx，body 为 `{"error": "描述", "status": 400}` 或 `{"error": "描述", "trace": "..."}`
- **纯文本端点**: 静态文件服务返回对应 Content-Type

### 认证

当前 API **无认证**。公网部署建议通过防火墙/反向代理限制访问来源。

### SSE 事件流

所有实时事件通过 `Transfer-Encoding: chunked` 推流。事件格式:

```
event: {event_type}
data: {json_string}

```

事件类型: `transcript-segment` / `state-update` / `metrics-update` / `doc-update` / `chat-message` / `collab-update` / `meeting-complete`

---

## 3. 会议

### 3.1 列出所有会议

```
GET /api/meetings
```

**请求参数**: 无

**响应示例**:
```json
{
  "meetings": [
    {
      "meeting_id": "UPLOAD_20260704_174209_71171a80",
      "platform": "e2e",
      "audio_source": null,
      "project_name": "产品评审会",
      "started_at": "2026-07-04T17:42:09",
      "last_updated": "2026-07-04T17:43:00",
      "item_count": 1
    }
  ],
  "count": 1
}
```

**说明**: 从 DATA_DIR 读取所有 `*.json` 会议文件，排除 `*.chat.json`。

---

### 3.2 获取会议详情

```
GET /api/meetings/{id}
```

**路径参数**:
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| id | string | 是 | 会议 ID |

**响应字段** (部分):
| 字段 | 类型 | 说明 |
|------|------|------|
| meeting_id | string | 会议 ID |
| state | object | 会议状态 (facts / metrics / transcript) |
| docs | array | 6 个文档摘要 (kind/status/doc_size/content_preview) |
| recent_transcript | array | 最近 20 段转写 |
| recent_metrics | array | 最近 5 条性能指标 |

---

### 3.3 上传音频自动生成文档

```
POST /api/meetings/upload
```

**请求**: `Content-Type: multipart/form-data`

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| audio | file | 是 | WAV 文件 (16kHz, mono, PCM) |
| project_name | string | 否 | 会议名称 |
| platform | string | 否 | 来源平台标识 |

**响应**:
```json
{
  "meeting_id": "UPLOAD_20260704_174209_71171a80",
  "transcript_segments": 14,
  "num_speakers": 1,
  "state_items": {
    "requirements": 0,
    "risks": 1,
    "questions": 0
  },
  "docs_ready_in_seconds": 30,
  "message": "Audio processed, docs will be ready in ~30s"
}
```

**说明**: 上传后服务端异步进行 ASR 转写 → 事实抽取 → controller 触发 6 文档生成。文档就绪后通过 GET 接口查询。

---

### 3.4 创建流式会议

```
POST /api/meetings/stream_start
```

**Query 参数**:
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| meeting_id | string | 否 | 复用已有会议 ID (不传则自动生成) |
| audio_source | string | 否 | 音频源: `microphone` / `loopback` / `both` (默认 microphone) |

**响应**:
```json
{
  "meeting_id": "auto_generated_id",
  "chunk_interval_sec": 30,
  "audio_source": "microphone",
  "reused": false,
  "message": "Stream started, send 30s WAV chunks to /api/meetings/{id}/stream_chunk"
}
```

---

### 3.5 推送音频切片

```
POST /api/meetings/{id}/stream_chunk
```

**请求**: `Content-Type: multipart/form-data`

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| audio | file | 是 | WAV 切片 (30s, 16kHz, mono) |
| chunk_index | int | 是 | 切片序号 (从 0 开始) |
| chunk_start_sec | float | 是 | 切片开始时间(秒) |
| overlap_sec | float | 否 | 与前一片重叠秒数 (默认 0) |
| client_sent_at | float | 否 | 客户端发送时间戳 (unix秒) |

**Query 参数**:
| 参数 | 类型 | 说明 |
|------|------|------|
| sync | bool | `true`=同步阻塞等待ASR完成, `false`=异步后台处理(默认true) |

**同步响应**:
```json
{
  "meeting_id": "{id}",
  "chunk_index": 0,
  "new_segments": [
    { "start_sec": 0.5, "end_sec": 3.2, "text": "今天我们讨论一下", "speaker_id": "spk_0", "chunk_index": 0 }
  ],
  "state_items": { "requirements": 0, "risks": 0, "questions": 0 },
  "metrics": {
    "chunk_index": 0,
    "processing_ms": 28150,
    "end_to_end_ms": 29300,
    "new_segments": 4
  },
  "docs_triggered": true
}
```

---

### 3.6 停止录音

```
POST /api/meetings/{id}/stream_stop
```

**说明**: 关闭 SSE 订阅者，停止接收音频切片。与 close 不同，stream_stop 只停录音，不结束会议。

---

### 3.7 结束会议

```
POST /api/meetings/{id}/close
```

**说明**: 推送 `meeting-complete` SSE 事件 → 关闭 SSE 订阅者 → 清 proactive 节流。客户端收到后切到"closed"状态。

---

### 3.8 校验会议名

```
GET /api/meetings/check_id?id=XXX
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| id | string | 是 | 会议名 (3-32 字符, `[A-Za-z0-9_-]`) |

**响应**:
```json
{
  "id": "产品评审会",
  "valid": true,
  "exists": false
}
```

---

## 4. 文档

### 4.1 获取全部文档

```
GET /api/meetings/{id}/docs
```

**响应**:
```json
{
  "meeting_id": "{id}",
  "docs": [
    { "kind": "req",  "label": "需求",        "content": "# 需求\n...", "version": 1 },
    { "kind": "arch", "label": "架构",        "content": "# 架构\n...", "version": 1 },
    { "kind": "tasks","label": "任务",        "content": "# 任务\n...", "version": 1 },
    { "kind": "api",  "label": "接口",        "content": "# 接口\n...", "version": 1 },
    { "kind": "risk", "label": "风险",        "content": "# 风险\n...", "version": 1 },
    { "kind": "demo", "label": "演示",        "content": "<html>...",   "version": 2 }
  ]
}
```

**文档类型**:
| kind | 说明 | 生成方式 | 内容风格 |
|------|------|----------|----------|
| req | 需求分析 | AIAgent (MiniMax-M3) | bullet points, 中文 |
| arch | 架构方案 | AIAgent (MiniMax-M3) | 方案描述或占位 |
| tasks | 任务拆解 | AIAgent (MiniMax-M3) | 责任人 + 截止时间 |
| api | 接口定义 | AIAgent (MiniMax-M3) | REST/API 描述或占位 |
| risk | 风险评估 | AIAgent (MiniMax-M3) | 含 RISK-XXXXXX 唯一编号 |
| demo | HTML 原型 | AIAgent (独立 session) | 可交互 HTML 页面 |

**文档生成生命周期**:
```
上传音频 → ASR 转写 (~28s/30s 音频)
  → MeetingState ingest
  → controller 触发
     → batch_docs agent: 1 次 LLM 调用, 5 次 write_file (req/arch/tasks/api/risk)
     → demo agent: 独立 session, 生成 HTML
  → 文档就绪 (总 ~60-120s)
```
---

### 4.2 获取单个文档

```
GET /api/meetings/{id}/docs/{kind}
```

| 参数 | 类型 | 说明 |
|------|------|------|
| kind | string | `req` / `arch` / `tasks` / `api` / `risk` / `demo` |

---

### 4.3 获取 Demo 版本列表

```
GET /api/meetings/{id}/demo/versions
```

**响应**:
```json
{
  "meeting_id": "{id}",
  "versions": [
    { "version": 1, "created_at": "2026-07-04T17:42:30", "summary": "初始版本", "file_size": 1255 },
    { "version": 2, "created_at": "2026-07-04T17:43:00", "summary": "🛡️ 规范风险评估工具", "file_size": 1255 }
  ],
  "count": 2
}
```

---

## 5. Chat 对话

### 5.1 发送 Chat 消息

```
POST /api/meetings/{id}/chat
```

**模式 A — 纯文本对话**: `Content-Type: application/json`
```json
{
  "message": "这个会议主要讨论了什么？",
  "context": {}  // 可选, 客户端上下文
}
```

**模式 B — 上传文件+对话**: `Content-Type: multipart/form-data`
| 字段 | 类型 | 说明 |
|------|------|------|
| text | string | 对话文本 (可选) |
| files | file[] | 文件 (文本入 KB, 图片转 base64) |

**响应**:
```json
{
  "meeting_id": "{id}",
  "user_message": { "role": "user", "content": "..." },
  "assistant_message": {
    "role": "assistant",
    "content": "根据会议记录，主要讨论了风险评估规范的制定...",
    "source": "hermes",
    "status": "completed"
  },
  "upload": {
    "status": 200,
    "text": "",
    "files": ["report.pdf"],
    "kb_doc_ids": ["mid:uuid"],
    "image_count": 0
  }
}
```

**说明**: Chat 内部使用 Hermes AIAgent，session_id = `meeting:{id}:vp-chat`。这个 session 也是 doc agent 的 parent session，所以 chat 里讨论的内容会自动注入到后续文档生成的上下文中。

---

### 5.2 获取 Chat 历史

```
GET /api/meetings/{id}/chat/history
```

---

## 6. 协作提问

### 6.1 提问

```
POST /api/meetings/{id}/ask_question?section=X&question=Y&asker=Z
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| section | string | 是 | 文档章节: `req`/`arch`/`tasks`/`api`/`risk`/`demo`/`docs` |
| question | string | 是 | 问题正文 |
| asker | string | 否 | 提问者 (默认 `agent`) |

**节流**: 同 (mid, section, 相似问题) 一次会议只记录 1 次，重复自动 mark 为 `duplicate`。

**响应**:
```json
{
  "ok": true,
  "qid": "a1b2c3",
  "section": "req",
  "question": "客户的预算是多少？",
  "asker": "VP",
  "status": "pending"
}
```

---

### 6.2 回答

```
POST /api/meetings/{id}/answer_question?qid=X&answer=Y&answerer=Z
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| qid | string | 是 | 问题 ID (从 ask 返回或 GET collab 列表获得) |
| answer | string | 是 | 回答内容 |
| answerer | string | 否 | 回答者 (默认 `VP`) |

---

### 6.3 获取协作记录

```
GET /api/meetings/{id}/collab
```

**响应**:
```json
{
  "meeting_id": "{id}",
  "collab": "## Pending\n...\n## Answered\n...",
  "pending": [
    { "qid": "a1b2c3", "section": "req", "question": "客户的预算是多少？", "asker": "VP", "asked_at": "..." }
  ],
  "answered": [],
  "stats": { "total": 1, "pending": 1, "answered": 0, "by_section": { "req": 1 } }
}
```

---

## 7. SSE 实时事件流

### 7.1 SSE 事件流

```
GET /api/meetings/{id}/events
```

| Header | 说明 |
|--------|------|
| `Accept: text/event-stream` | 建议加, 但非必需 |
| `Last-Event-ID` | 可选, 断线重连用 |

**SSE 事件类型**:

| 事件名 | data 关键字段 | 触发时机 |
|--------|-------------|----------|
| `transcript-segment` | `{start_sec, end_sec, text, speaker_id, chunk_index, speaker_name, cleaned?}` | ASR 每段转写完成 |
| `state-update` | `{meeting_id, requirements, risks, questions, ...}` | 状态更新 |
| `metrics-update` | `{chunk_index, processing_ms, end_to_end_ms, new_segments}` | 每个 chunk 处理完 |
| `doc-update` | `{kind, status, doc_size, content?}` | 某个文档生成完成 |
| `doc-update (batch)` | `{status: "triggered", kinds: [...], message}` | batch_docs 触发时 |
| `chat-message` | `{role, content, source, ...}` | Chat 助理消息 |
| `collab-update` | `{action, qid, section, question, answer?}` | 协作提问/回答 |
| `meeting-complete` | `{status: "user_closed"}` | 用户结束会议 |

**客户端示例**:
```javascript
const events = new EventSource("http://47.100.182.3:28765/api/meetings/{id}/events");
events.addEventListener("transcript-segment", (e) => {
  const seg = JSON.parse(e.data);
  console.log(seg.text);
});
events.addEventListener("doc-update", (e) => {
  const doc = JSON.parse(e.data);
  console.log(`文档 ${doc.kind} 就绪: ${doc.doc_size}B`);
});
```

---

## 8. 知识库 (KB)

### 8.1 KB 搜索 (GET)

```
GET /api/kb/search?q=XXX&meeting_id=YYY
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| q | string | 是 | 搜索关键词 |
| meeting_id | string | 否 | 按会议过滤，不传则搜全部 |

---

### 8.2 KB 搜索 (POST)

```
POST /api/kb/search
Content-Type: application/json

{
  "query": "关键词",
  "top_k": 5,
  "meeting_id": "可选",
  "scope": "current"
}
```

---

### 8.3 上传文件到 KB

```
POST /api/kb/upload
Content-Type: multipart/form-data
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| meeting_id | string | 是 | 归属会议 ID |
| file | file | 是 | `.txt` / `.md` / `.pdf` (最大 50MB) |

---

### 8.4 KB 统计

```
GET /api/kb/list?meeting_id=YYY
```

---

### 8.5 删除 KB 文档

```
DELETE /api/kb/{doc_id}
```

---

## 9. 系统

### 9.1 系统状态

```
GET /api/status
```

**响应**:
```json
{
  "controller": {
    "running": true,
    "pid": 1234,
    "poll_interval": "30s",
    "last_log": "..."
  },
  "stats": {
    "active_meetings": 3,
    "total_docs": 16,
    "kb_docs": 5
  },
  "paths": {
    "data_dir": "/data/vpbuddy/server/data/meetings",
    "docs_dir": "/data/vpbuddy/server/docs",
    "ui_dir": "..."
  }
}
```

### 9.2 时间线

```
GET /api/timeline
```

返回全部会议的累积项按时间倒序排列，适合做"全部活动"视图。

---

## 附录

### 典型用户流程

```
客户端                                          API 服务器
  │                                                 │
  │  POST /api/meetings/stream_start                │
  │  ─────────────────────────────────>              │
  │  ← {"meeting_id": "mid-xxx"}                    │
  │                                                 │
  │  GET /api/meetings/mid-xxx/events (SSE)         │
  │  ─────────────────────────────────>              │
  │  ← event: transcript-segment ═══╗               │
  │  ← event: state-update       ║  ║               │
  │  ← event: metrics-update     ║  ║               │
  │                             ║  ║               │
  │  ─── 每 30s 循环 ───          ║  ║               │
  │  POST /meetings/mid-xxx/stream_chunk  ║          │
  │  ─────────────────────────────────>               │
  │  ← {new_segments: [...], docs_triggered}         │
  │                               ║  ║               │
  │  ← event: doc-update (6 次)   ║  ║               │
  │  ─── 约 60s 后 ───            ║  ║               │
  │                                                 │
  │  GET /meetings/mid-xxx/docs                     │
  │  ─────────────────────────────────>              │
  │  ← {docs: [6 个文档]}                           │
  │                                                 │
  │  POST /meetings/mid-xxx/chat                    │
  │  ─────────────────────────────────>              │
  │  ← {assistant_message: {content: "..."}}        │
  │                                                 │
  │  POST /meetings/mid-xxx/close                   │
  │  ─────────────────────────────────>              │
  │  ← {status: "closed"}                           │
```

### 错误码

| 状态码 | 说明 |
|--------|------|
| 200 | 成功 |
| 400 | 请求参数错误 (缺少必填字段 / 格式无效) |
| 404 | 资源不存在 (meeting / doc / doc_id) |
| 500 | 服务端处理错误 (返回 error + trace) |

### 数据目录

| 路径 | 说明 |
|------|------|
| `/data/vpbuddy/server/data/meetings/` | 会议 JSON 状态文件 (MeetingState) |
| `/data/vpbuddy/server/docs/{mid}/` | 6 文档 + demo HTML |
| `/data/vpbuddy/server/src/` | 服务端 Python 源码 |
