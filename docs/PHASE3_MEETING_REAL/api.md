# PHASE3_MEETING_REAL API 规格

最后更新: 2026-06-21T15:30+08:00 (v2)
session_id: `meeting:PHASE3_MEETING_REAL:api`

---

## 1. 会议转写 API (GPU)

### POST /api/v1/meetings/{meeting_id}/transcribe
```json
请求:
  {
    "audio_url": "https://feishu.cn/recording/xxx.mp3",
    "platform": "feishu"
  }

响应 200:
  {
    "meeting_id": "PHASE3_MEETING_REAL",
    "sentences": 53,
    "speakers": 3,
    "speaker_map": {
      "SPEAKER_00": "张胜东(VP)",
      "SPEAKER_01": "周华健(产品总监)",
      "SPEAKER_02": "李丹(设计师)"
    },
    "rtf": 0.009,
    "duration": 150.0
  }
```

## 2. 文档生成 API

### POST /api/v1/docs/{doc_kind}
触发单个 doc_kind 的 LLM 文档生成(6 种: req/arch/tasks/api/risk/demo)

### 触发模式

| 模式 | env | 流程 |
|---|---|---|
| hermes sub | 默认 | `hermes chat -q` → sub-session (没写文件工具,失败) |
| **direct** | `VPBUDDY_DIRECT=1` | **controller 渲染 prompt → 主 session 写文件** |

## 3. 知识库 API

### POST /api/v1/kb/search
```json
请求: { "query": "协同编辑", "top_k": 5 }
响应: [
  { "doc_id": "REQ-780B14", "score": 0.92, "meeting": "PHASE3_MEETING_REAL" },
  ...
]
```

## 4. 跨平台通知 API

### POST /api/v1/notify
```json
请求: {
  "event": "meeting.minutes_generated",
  "platforms": ["feishu", "dingtalk", "wecom"],
  "payload": { "meeting_id": "PHASE3_MEETING_REAL", "doc_count": 6 }
}
```

平台适配器:
- 飞书: WebSocket(30s 心跳,断连指数退避)
- 钉钉: Stream(已对接)
- 企微: 回调(审核中 7-15d)

## 5. 导出 API

### POST /api/v1/export/xlsx
```json
请求: { "meeting_id": "PHASE3_MEETING_REAL", "fields": ["start_at", "speakers", "duration", "tags"] }
响应: { "url": "https://s3/xxx.xlsx", "expires_in": 86400 }
```

## v2 改动

- API 1 加入 speaker_map 真名映射
- API 2 区分 hermes sub vs direct 模式
- API 3/4/5 补完
