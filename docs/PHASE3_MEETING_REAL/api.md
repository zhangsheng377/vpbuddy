# VPBuddy PHASE3 Meeting API 设计

> 维护者: api 子 session (`meeting:PHASE3_MEETING_REAL:api`)
> 最后更新: 2026-06-21 (v2, 增补会议转写 + 说话人标注)
> 来源: 累积摘要 7 REQ + 5 FEAT + GOAL-CEDF6C + SPEAKER 映射
> 风格: OpenAPI 3.0 (YAML 嵌入,便于贴到 Swagger Editor)

---

## 0. 设计原则

1. **REST 优先,WS 例外**: 协同编辑用 WebSocket,其余全 REST。
2. **租户隔离**: 所有路径含 `{tenant_id}`,SQL 层强制 row-level filter。
3. **限流前置**: 100 QPS/租户(REQ-302783)→ 网关层 token bucket,触发返 `429`。
4. **错误码统一**: 见 §7,禁止端点自定义。
5. **YAGNI**: 不画 UML 类图;SDK 示例用 curl;schema 用 `$ref` 复用。
6. **会议是核心资源**: §10 的 meeting/transcript/speakers 端点是 VPBuddy 主流程(GOAL-CEDF6C),权限严格(RBAC §2 必须 `meeting:edit`),所有其他端点(协同编辑/导出/通知)都挂在 meeting 之下或之上。

---

## 1. 认证与会话 (FEAT-1763F9 SSO)

### POST /api/v1/auth/sso/initiate
OIDC/SAML 登录入口。

请求:
```json
{
  "idp": "azure_ad",        // azure_ad | okta | auth0 | feishu | dingtalk
  "tenant_id": "tnt_xxx",
  "redirect_uri": "https://app.vpbuddy.com/cb"
}
```
响应 `302` → IdP 登录页;失败 `400 invalid_idp`。

**关键字段说明**:
- `idp`: 推荐 Azure AD(国内出海企业首选),Okta/Auth0 备选,飞书钉钉补齐国内场景(QUE-661567)。
- `redirect_uri`: 必须在租户白名单,否则 `403 redirect_not_whitelisted`。

### POST /api/v1/auth/sso/callback
IdP 回调,验证 ID Token 后签发 VPBuddy JWT。

请求:
```json
{ "id_token": "eyJ...", "state": "abc123" }
```
响应:
```json
{
  "access_token": "vpb_jwt...",
  "refresh_token": "vpb_rt...",
  "expires_in": 3600,
  "user": { "id": "usr_xxx", "tenant_id": "tnt_xxx", "roles": ["editor"] }
}
```

### POST /api/v1/auth/refresh
刷新 access_token,refresh 有效期 30 天,滚动续期。

---

## 2. RBAC 权限管理 (REQ-565948)

三元组:**角色 + 资源 + 操作**。

### GET /api/v1/roles
列出当前租户所有角色。

### POST /api/v1/roles
```json
{
  "name": "meeting_editor",
  "permissions": [
    { "resource": "meeting", "action": "edit", "scope": "tenant" },
    { "resource": "export",  "action": "create", "scope": "own" }
  ]
}
```
**scope**: `tenant` | `own` | `dept`,影响 SQL `WHERE` 注入。

### POST /api/v1/permissions/check
权限校验端点(网关内部调用,前端也可直查):
```json
{ "user_id": "usr_xxx", "resource": "meeting", "action": "edit", "target_id": "mtg_yyy" }
→ { "allowed": true }
```

**错误码**:
- `403 permission_denied` — 角色未授予该资源操作
- `403 scope_violation` — 角色有权限但 scope 不够(own vs tenant)

---

## 3. 协同编辑 (REQ-780B14, FEAT-2F8528)

CRDT yjs@13.6,WebSocket 协议,快照 50 ops / 5 分钟。

### WS /api/v1/docs/{doc_id}/ws
**协议**: 客户端→服务端 yjs binary update;服务端广播给同 room 其他客户端。

连接握手需带 `Authorization: Bearer <jwt>` + `?doc_id=...`。

消息类型(简化为业务事件):
| type | 方向 | 说明 |
|------|------|------|
| `awareness` | 双向 | 光标/选区(短 TTL,不入库) |
| `update` | 双向 | yjs binary update |
| `snapshot` | 服务端→客户端 | 服务端主动推送的版本号(乐观锁) |
| `rollback` | 服务端→客户端 | 冲突回滚通知(理论上 yjs 极少触发) |

### GET /api/v1/docs/{doc_id}/snapshots?before={ts}
历史快照查询(回溯基础;MVP 仅返服务端快照,P2 再加操作级)。

### POST /api/v1/docs/{doc_id}/fork
从某快照 fork 新文档。

**关键约束**:
- 10 人并发上限,超过返 `429 room_full`。
- doc 大小 > 1 MB 触发服务端 snapshot 强制刷盘。

---

## 4. 跨平台消息推送 (REQ-0D5DB7, FEAT-C93459)

Event Bus + 三平台 Adapter,指数退避重试(QUE-5EE081 待定参数)。

### POST /api/v1/notifications/send
发送通知(由内部业务事件触发,或手动测试)。
```json
{
  "target_user_ids": ["usr_a", "usr_b"],
  "channels": ["feishu", "dingtalk", "wecom"],   // 至少 1
  "template": "meeting_invite",
  "data": { "meeting_id": "mtg_xxx", "time": "2026-07-01T10:00:00+08:00" }
}
```

### POST /api/v1/webhooks/feishu  (同形 /dingtalk, /wecom)
平台回调端点,接收用户点击/已读事件,**仅做事件入库,业务逻辑异步消费**。

**字段约束**:
- `channels` 不能为空(防误发空消息)
- 单租户每分钟最多 1000 条通知(防 spam,超出进死信队列)
- 三平台 Adapter 抽象在 `notification/adapters/<platform>.py`,重试策略由 Adapter 内部决定(QUE-5EE081)

---

## 5. Excel 导出 (REQ-B12E21, FEAT-BAB8DB)

openpyxl + 字段映射,S3 临时链接 24h 有效。

### POST /api/v1/exports
创建导出任务。
```json
{
  "resource": "meeting_list",     // meeting_list | attendance | minutes
  "filters": { "date_from": "2026-06-01", "date_to": "2026-06-30" },
  "field_mapping_id": "fmap_xxx"  // 可选,默认租户配置
}
```
响应 `202 Accepted`:
```json
{ "export_id": "exp_xxx", "status": "pending", "poll_url": "/api/v1/exports/exp_xxx" }
```

### GET /api/v1/exports/{export_id}
查状态。`completed` 时返 `download_url`(S3 presigned,24h 过期)。

**字段说明**:
- `field_mapping_id` 由租户预配置(后台管理界面),允许自定义列名/列序/格式化。
- 大数据集(>10 万行)自动切分 sheet,文件名 `vpbuddy_{resource}_{yyyyMMdd}_{HHmm}.xlsx`。

---

## 6. 审计日志 (REQ-B82FAC)

保留 90 天(P2 可配置),查询接口 MVP 仅供 admin。

### GET /api/v1/audit-logs
查询参数:
- `user_id`, `resource`, `action`, `from`, `to`, `cursor`(分页)

响应:
```json
{
  "items": [
    { "id": "log_xxx", "ts": "2026-06-21T07:00:00Z", "user_id": "usr_xxx",
      "resource": "meeting", "action": "delete", "target_id": "mtg_xxx",
      "ip": "10.0.0.5", "result": "success" }
  ],
  "next_cursor": "..."
}
```

---

## 7. 统一错误码

| HTTP | code | 含义 |
|------|------|------|
| 400 | `invalid_request` | 字段校验失败(附 `fields` 数组) |
| 401 | `unauthenticated` | 缺 token / token 过期 / refresh 失败 |
| 403 | `permission_denied` | RBAC 拒绝 |
| 403 | `scope_violation` | scope 不够 |
| 403 | `tenant_mismatch` | 跨租户访问 |
| 404 | `not_found` | 资源不存在 / 已删除 |
| 409 | `conflict` | 乐观锁冲突(版本号 mismatch) |
| 409 | `version_conflict` | 说话人标签版本过期(`base_version` 与服务端不一致,客户端重 GET 后重试,见 §10) |
| 422 | `business_rule_violated` | 业务规则阻止(如删除有子资源的父资源) |
| 429 | `rate_limited` | 触发 100 QPS 限流(REQ-302783) |
| 429 | `room_full` | WS 房间满(协同编辑 10 人上限) |
| 429 | `job_queue_full` | 会议转写任务排队超限(单租户同时 ≤ 5,见 §10) |
| 500 | `internal_error` | 服务端未捕获异常 |
| 503 | `dependency_unavailable` | PG/Redis/S3 暂时不可用,客户端可重试 |

错误响应统一格式:
```json
{ "code": "rate_limited", "message": "...", "request_id": "req_xxx", "retry_after_ms": 1000 }
```

---

## 8. 待定/未决

- QUE-5EE081: 飞书 WS 重试参数(jitter / 死信阈值)→ 写进 §4 Adapter 实现,本 API 不暴露。
- RISK-07A255 偶发断连: API 层无变化,服务端心跳 ping frame 每 30s,客户端断连后通过 `reconnect` 事件触发重连(协议细节见 yjs 文档)。
- ADR-待定: PostgreSQL JSON 字段索引策略(FEAT-BBD14D)。
- **ADR-新增**: 飞书妙记 (miaoji) 数据回灌策略 — webhook 优先(`POST /webhooks/feishu/miaoji`) / 轮询 fallback(`POST /webhooks/feishu/miaoji/poll`,admin 触发) / 双写防丢(miaoji_event_id 唯一索引),详见 §10。
- **ADR-新增**: 说话人标签作用域 — `this_meeting` 默认,`tenant_global` 需 GDPR/PIPL 评估(同说话人跨会议共享映射涉及生物特征关联,RISK-F14066,见 §10 `/speakers/label`)。
- **ADR-新增**: 转写引擎选择策略 — `engine=auto` 时,服务端按 `language_hint` + 音频采样率自动选 paraformer(中文为主)/sensevoice(多语种),MVP 规则表 hardcode,P2 改 ML 分类器。

---

## 9. Curl 示例

```bash
# 1. SSO 登录
curl -X POST https://api.vpbuddy.com/api/v1/auth/sso/initiate \
  -H "Content-Type: application/json" \
  -d '{"idp":"azure_ad","tenant_id":"tnt_001","redirect_uri":"https://app/cb"}'

# 2. 查权限
curl -X POST https://api.vpbuddy.com/api/v1/permissions/check \
  -H "Authorization: Bearer $JWT" \
  -d '{"resource":"meeting","action":"edit","target_id":"mtg_001"}'

# 3. 发起导出
curl -X POST https://api.vpbuddy.com/api/v1/exports \
  -H "Authorization: Bearer ***" \
  -d '{"resource":"meeting_list","filters":{"date_from":"2026-06-01"}}'
```

---

## 10. 会议与转写 (GOAL-CEDF6C, ★ 当前 E2E 测试目标)

VPBuddy 核心场景。飞书妙记 miaoji → VPBuddy 转写(sensevoice+paraformer)+ 说话人分离(campplus)+ 协同编辑(§3)+ 导出(§5)。

### POST /api/v1/meetings
创建会议。
```json
{
  "title": "PHASE3 E2E 测试会议",
  "scheduled_at": "2026-06-21T10:00:00+08:00",
  "source": "feishu_miaoji",     // feishu_miaoji | manual_upload | recording
  "external_ref": {              // source=feishu_miaoji 必填
    "miaoji_meeting_id": "mc_xxx",
    "tenant_key": "tnt_feishu_xxx"
  },
  "participants": ["usr_a", "usr_b"]   // 可选
}
```
响应 `201`:
```json
{
  "id": "mtg_xxx",
  "status": "created",          // created | audio_ready | transcribing | transcribed | failed
  "audio_url": null,
  "created_at": "2026-06-21T10:00:00Z"
}
```

**关键字段说明**:
- `source=feishu_miaoji` (本测试主线,GOAL-CEDF6C): 由下方 `/webhooks/feishu/miaoji` 异步回灌音频 + 转写结果。
- `source=manual_upload`: MVP 兼容,客户端直接传 wav/mp3(见 `/meetings/{id}/audio`)。
- `external_ref.miaoji_meeting_id`: webhook 回调时用于匹配回 meeting_id(主键映射,唯一)。

### GET /api/v1/meetings
列表。查询参数: `status`, `from`, `to`, `cursor`(分页)。

### GET /api/v1/meetings/{meeting_id}
详情。响应含 `status`, `duration_seconds`, `language`, `speakers`(原始聚类标签列表), `transcript_url`。

### POST /api/v1/meetings/{meeting_id}/audio  (source=manual_upload)
上传音频。`multipart/form-data`,字段名 `file`,最大 500 MB,支持 wav/mp3/m4a。
响应 `200`:
```json
{ "audio_url": "s3://vpbuddy/meetings/mtg_xxx/audio.wav", "duration_seconds": 209.0 }
```

### POST /api/v1/meetings/{meeting_id}/transcribe
触发转写(GPU 192.168.10.63 上的 sensevoice+paraformer+campplus pipeline)。
```json
{
  "engine": "auto",            // auto | sensevoice | paraformer
  "enable_diarization": true,   // 默认 true,走 campplus
  "language_hint": "zh"        // zh | en | auto
}
```
响应 `202`:
```json
{
  "job_id": "job_xxx",
  "status": "queued",          // queued | running | completed | failed | dead_letter
  "poll_url": "/api/v1/meetings/{meeting_id}/jobs/{job_id}"
}
```

**关键字段说明**:
- `engine=auto`: 服务端按 `language_hint` + 音频采样率自动选 paraformer(中文为主)/sensevoice(多语种),规则表 ADR 见 §8。
- `enable_diarization=false`: 跳过 campplus,纯转写,速度 ×2(应对实时直播场景)。

### GET /api/v1/meetings/{meeting_id}/jobs/{job_id}
查任务状态。
```json
{ "status": "transcribing", "progress": 0.45, "eta_seconds": 30 }
```
终态: `completed | failed | dead_letter`(`dead_letter` 时响应附 `reason` 字段)。

### GET /api/v1/meetings/{meeting_id}/transcript
查转写结果(MVP 全量返回,P2 加 `?from=&to=` lazy load)。
```json
{
  "status": "transcribed",
  "language": "zh",
  "engine": "paraformer+diarization",
  "duration_seconds": 209.0,
  "segments": [
    { "start": 0.0,  "end": 5.2,  "speaker": "SPEAKER_00", "text": "今天讨论 VPBuddy 第三阶段..." },
    { "start": 5.5,  "end": 12.1, "speaker": "SPEAKER_01", "text": "API 设计有变动..." }
  ],
  "speakers": ["SPEAKER_00", "SPEAKER_01", "SPEAKER_02"],
  "speaker_label_version": "v2"      // 见下方 /speakers/label
}
```

**关键字段说明**:
- `speaker` 默认是模型聚类标签(`SPEAKER_XX`),人类可读名通过 `speaker_label_version` 关联 `/speakers/labels` 表。
- `start/end` 精度 0.1 秒(切句粒度,不是 word 级;word 级 P2)。
- 长会议 (>1h) P2: 按 30s 切段 lazy load,需 `?from=&to=` 参数。

### ★ POST /api/v1/meetings/{meeting_id}/speakers/label  (应对 RISK-F14066)
说话人标签校准 — 修复 campplus 误聚(歌曲聚出 8 类 / 独唱拆多人)。
```json
{
  "base_version": "v1",           // 乐观锁,可选(MVP 默认 latest)
  "mappings": [
    { "from": "SPEAKER_00", "to": "张胜东",   "action": "rename" },
    { "from": "SPEAKER_01", "to": "张胜东",   "action": "merge" },   // 误拆
    { "from": "SPEAKER_02", "to": "_discard", "action": "drop"  }    // 歌曲/背景音
  ],
  "scope": "this_meeting"          // this_meeting | tenant_global
}
```
响应 `200`:
```json
{ "applied": 3, "version": "v2", "segment_count": 156 }
```
`base_version` 与服务端不一致时返 `409 version_conflict`(客户端需重 GET 后重试)。

**关键字段说明**:
- `action=merge`: 把多个 `SPEAKER_XX` 合并为同一人 — 应对 campplus 在歌曲/独唱上拆出多类(RISK-F14066)。合并后 segment 中所有 `from` 标签统一替换为 `to`。
- `action=drop`: 标记为 `_discard`,前端不展示,segment 仍在(只改 speaker 标签,text 保留用于全文搜索)。
- `action=rename`: 单纯改名,不改 segment 内容。
- `scope=tenant_global`: 同一说话人在该租户所有会议共享标签(避免每次会议重复标注);涉及 GDPR/PIPL 评估(生物特征跨会议关联),见 §8 ADR。MVP 默认 `this_meeting`。
- `version` 自增,`transcript` 响应里同步更新,客户端按 version 决定是否重渲染。

### POST /api/v1/webhooks/feishu/miaoji  (与 §4 /webhooks/feishu 同形,语义分离)
飞书妙记回调端点(GOAL-CEDF6C 入口)。
- 验证 header `X-Lark-Signature`(HMAC-SHA256,密钥来自飞书开放平台)
- 入库 `miaoji_events` 表(主键 `miaoji_event_id`,唯一索引防重复)
- 异步消费: 拉取 miaoji 音频 + 转写 JSON → 写 `meeting.audio_url` + 触发 `/transcribe`
- 失败重试 3 次(指数退避 1s / 4s / 16s),进死信需人工介入

### POST /api/v1/webhooks/feishu/miaoji/poll  (备援)
miaoji webhook 漏单时的轮询补偿。admin RBAC 权限,默认 30s 间隔(服务端强制,客户端无法绕过)。

**关键约束**:
- 单租户同时在跑转写任务 ≤ 5(GPU 单卡并发限),超出排队 → `429 job_queue_full`。
- 转写任务硬超时 1h,超时 → `dead_letter`,MVP 不自动重试(人工介入,见 ADR)。
- 说话人标签 `version` 单调递增,删除 / 合并 / 改名均触发。

---

## 11. 限流状态查询 (REQ-302783 显式化)

### GET /api/v1/rate-limit
查当前租户限流余量(供前端 UI 展示 + 预判退避)。
```json
{
  "limit_qps": 100,
  "current_qps": 12,
  "remaining_qps": 88,
  "reset_at": "2026-06-21T10:01:00Z"
}
```
**响应头**(所有 API 响应统一携带,客户端无需额外调用):
- `X-RateLimit-Limit`: 100
- `X-RateLimit-Remaining`: 88
- `X-RateLimit-Reset`: unix timestamp

**字段说明**:
- `current_qps`: 滑动 1s 窗口内实际请求数(不是峰值),给前端做"快到顶了"提示。
- `reset_at`: 滑动窗口完全清零的时刻,精确到秒。

---

## 12. Curl 补充(会议转写)

```bash
# 1. 创建会议(飞书妙记源)
curl -X POST https://api.vpbuddy.com/api/v1/meetings \
  -H "Authorization: Bearer ***" \
  -d '{"title":"E2E","source":"feishu_miaoji","external_ref":{"miaoji_meeting_id":"mc_xxx","tenant_key":"tnt_feishu_xxx"}}'

# 2. 触发转写(sensevoice+paraformer+campplus pipeline)
curl -X POST https://api.vpbuddy.com/api/v1/meetings/mtg_xxx/transcribe \
  -H "Authorization: Bearer ***" \
  -d '{"engine":"auto","enable_diarization":true,"language_hint":"zh"}'

# 3. 拉取转写结果
curl https://api.vpbuddy.com/api/v1/meetings/mtg_xxx/transcript \
  -H "Authorization: Bearer ***"

# 4. 校准说话人标签(应对 campplus 在歌曲/独唱上误聚,RISK-F14066)
curl -X POST https://api.vpbuddy.com/api/v1/meetings/mtg_xxx/speakers/label \
  -H "Authorization: Bearer ***" \
  -d '{"base_version":"v1","mappings":[{"from":"SPEAKER_00","to":"张胜东","action":"rename"},{"from":"SPEAKER_02","to":"_discard","action":"drop"}],"scope":"this_meeting"}'

# 5. 查限流状态
curl https://api.vpbuddy.com/api/v1/rate-limit -H "Authorization: Bearer ***"
```

---

> 变更触发条件: 累积里新增 REQ/FEAT 影响端点;V 说"加接口/改字段";错误码统一性被破坏。
> 不变更场景: 风险(RISK)/开放问题(QUE)变化(除非直接影响协议),目标(GOAL)微调。
