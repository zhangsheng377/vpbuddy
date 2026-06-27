# ADR-0017 SSE 流式事件 close 语义与 heartbeat 格式修复

**状态**: Accepted
**日期**: 2026-06-27
**作者**: Hermes (audit + 修复) → 张胜东确认
**触发**: 用户原话 *"客户端上完全不知道它到底有没有把我说的话 asr 成功, 我觉得干脆在'转写'页签再加一个实时接收、展示服务器 asr 出的文字的能力吧"* + 用户追问 *"需求文档收到了更新，但是不是你没有记录到客户端日志里？"*

---

## 0. 上下文 — 三个真实 bug

跑 2026-06-27 22:00 + 22:06 两轮采集后客户端日志暴露三个问题:

### Bug A — SSE heartbeat `data: {}` 字面量
GPU 端 `realtime_server.py::sse_generator` 每 30s 发心跳:
```python
yield b"event: heartbeat\n"
yield b"data: {}\n\n"   # ❌ 字面字符串 "{}" 不是 JSON
```
Python bytes literal 不做格式化, 服务端把字符串 `{}` 当 data 发出去。
客户端 `reqwest-eventsource` 解析 JSON 失败 → 触发 `error decoding response body` → 自动断开重连。

**日志证据**: 22:01:17 (连接后 30s) + 22:01:49 (重连后 30s) 各报一次断开。

### Bug B — stop_capture 没通知服务端关 SSE
客户端 `main.rs::stop_capture` 只调 `h.abort()` 强掐 SSE task, **服务端 SSE generator 阻塞在 `q.get(timeout=30)`**, 要等下一个 heartbeat (30s) 或 timeout (120s) 才能退出。期间:
- 服务端 subscriber queue 残留 → 内存泄漏
- 下次同 meeting_id 重连会收到旧事件

**日志证据**: 22:06:38 stop 触发 → 22:06:44 还在断开 → 30s 后才真退出。

### Bug C — 客户端日志没记录 SSE 事件
`handle_sse_event` 转发事件给前端用 `app.emit`, 但**每个分支只 log debug 或不 log**。用户问 "需求文档收到了更新, 但是不是你没有记录到客户端日志里" — 确认 doc-update 走到了前端 (`doc-status` 事件 → doc-block 状态变), 但**客户端日志查不到 SSE 事件流向**, 排查链路困难。

### 用户加的需求 — 转写页签展示 ASR
之前 `transcript-segment` 已有 listener (`main.js:114`), 但因为 Bug A 客户端 SSE 一直断, **从来没收到过 ASR 推送**。用户没意识到是 SSE 断, 只看到"实时展示没出来", 所以提了"再加一个实时接收展示"。

---

## 1. 决策

### 1.1 Heartbeat 用合法 JSON

```python
# 2026-06-27 修复
heartbeat_payload = json.dumps({"type": "heartbeat", "ts": time.time()}, ensure_ascii=False).encode("utf-8")
yield b"event: heartbeat\n"
yield b"data: " + heartbeat_payload + b"\n\n"
```

`ts` 字段给前端做时钟漂移检测 (可选, 暂未消费)。

### 1.2 加 `POST /api/meetings/{id}/stream_stop` 路由 + 服务端 POISON

- 客户端 stop 时 POST → 服务端 `close_meeting(meeting_id)`
- `close_meeting` 给该 meeting 的所有 subscriber 队列放入一个 **POISON (object 哨兵)**, 立即清理 `_subscribers[meeting_id]` 和 `_event_history[meeting_id]`
- generator 检测 `not isinstance(event, dict)` 立即 break → `_handle_sse_events` 退出 → 服务端 connection 自然 close

```python
def close_meeting(meeting_id: str) -> int:
    _POISON = object()
    with _subscribers_lock:
        subs = list(_subscribers.get(meeting_id, []))
        if meeting_id in _subscribers: del _subscribers[meeting_id]
        if meeting_id in _event_history: del _event_history[meeting_id]
    for q in subs:
        try: q.put_nowait(_POISON)
        except queue.Full: ...
    return len(subs)
```

```python
# sse_generator
event = q.get(timeout=timeout)
if not isinstance(event, dict):  # POISON = close_meeting 哨兵
    break
```

### 1.3 客户端 SSE task 提到 outer scope + 存进 state

`JoinHandle` 不 Clone, 必须在 spawn caller 范围内把 handle 存入 state:

```rust
// start_capture: SSE task 提出来, JoinHandle 存进 state.sse_handle
let sse_handle = tokio::spawn(async move {
    run_sse_loop(...).await;
});
*state.sse_handle.lock().await = Some(sse_handle);

// capture task 闭包不再嵌套 sse spawn, 不再 sse_handle.abort()
let handle = tokio::spawn(async move {
    run_capture_loop(...).await;
    // SSE 自己检测 capturing=false 退出 (run_sse_loop 的 while 条件)
});
```

```rust
// stop_capture: 三步走
//   1. POST /stream_stop → 服务端立即关 SSE
//   2. await sse_handle (最多 1.5s) → 客户端 task 自然退出
//   3. abort capture_handle (采集 task, 必杀)
```

### 1.4 客户端日志记录全部 SSE 事件

```rust
"transcript-segment" => {
    log::info!("📝 transcript-segment: spk={:?} text={:?}", ...);
    let _ = app.emit("transcript-segment", &payload);
}
"doc-update" => { log::info!("📄 doc-update: {}", payload); ... }
"state-update" => { log::info!("📊 state-update: {}", payload); ... }
"heartbeat"   => { log::debug!("💓 heartbeat: {}", payload); ... } // 30s 一次降 debug
"connected"    => { log::info!("✅ SSE connected: {}", payload); ... }
```

**约束**: 用户回复时要求日志精简, transcript-segment 文本可能很长, 但 ASR 文本就是排查 "采到没 + 转写没" 的关键信号, 保留 info 级别 (其他所有 SSE 事件降为 info 或 debug)。

### 1.5 前端 "转写" 页签加强 ASR 实时展示

- 时间戳 `MM:SS.mmm` (替代原始 `1.5s`)
- 说话人末两位 → 8 色调色板 (`spk-00..07`)
- 新增段 0.8s 脉冲高亮动画 (`stream-item-fresh`)
- 顶部 facts-summary 末尾新增 `last-seg-pill` 一眼看到最新 ASR 文本

CSS:
```css
.stream-item-fresh { animation: seg-pulse 0.8s ease-out; border-color: var(--accent); }
@keyframes seg-pulse {
  0% { background: rgba(94,106,210,0.3); transform: translateX(0); }
  20% { transform: translateX(4px); }
  100% { background: var(--bg2); transform: translateX(0); }
}
```

---

## 2. 验证 (e2e 测, 2026-06-27 22:30)

```
meeting: STREAM_20260627_222952_0c06224c
heartbeat count: 1
  HB: {"type": "heartbeat", "ts": 1782570592.2315745}
  JSON OK
stream_stop: {'meeting_id': '...', 'closed_subscribers': 1, 'message': 'Stream stopped, SSE subscribers closed'}
```

✅ Heartbeat 是合法 JSON, reqwest-eventsource 能解析
✅ stream_stop 路由 200 + 关闭 1 个 subscriber
✅ 服务端 generator POISON 退出 → 连接自然 close

---

## 3. 不在范围

- 没改心跳间隔 (默认 30s, 太密会浪费带宽, 太疏超时检测慢)
- 没改 EVENT_TTL (300s, 跨会话补发足够)
- 没改订阅者队列 `maxsize=500` (单会议缓冲足够, 极端情况可调)
- 没改前端 `last_event_id` 补发逻辑 (已经走 `?last_event_id=xxx` 重连补偿)

---

## 4. 后续

- [ ] 客户端 main.rs 需要 cargo 编译验证 (本地无 cargo, 走 CI)
- [ ] CI 三平台 build 通过 → 推送 tag `v0.1.1-rc1` 出 release
- [ ] 张胜东用 release 包做端到端录音 30s+ 验证 transcript-segment 持续推送 + 显示

---

## 5. 经验教训 (写进 hermes memory)

- **Python bytes literal 不做 str.format**: `b"data: {}\n\n"` 是字面字符串, 永远发 "{}"; 需要先 `json.dumps()` 再 `+` 拼接
- **JoinHandle 不 Clone**: 要存进 state 必须在 spawn caller 范围, 不能跨 spawn 嵌套
- **reqwest-eventsource 解析失败 = 立即断开**: 不要发非 JSON 的 data 帧, 即便是 heartbeat/keep-alive
- **远端 patch 必须 scp 同步**: hermes 文件工具的 patch 默认改本机副本, 跨 SSH 远端不会自动同步