# ADR-0018 SSE chunked encoding + meeting-complete 自动关闭 + stop_capture 不关 SSE

**状态**: Accepted
**日期**: 2026-06-28
**作者**: Hermes (audit + 修复) → 张胜东确认
**触发**: 张胜东 02:12 实测报告 — 录音时 0 个 SSE 事件 / 按钮停止后没切换 / 6 文档不更新 / Hermes 思考提示被遮挡

---

## 0. 上下文 — 三个真实 bug + 一个语义错误

### Bug A: SSE 30s 后必须靠 chunked encoding 才能工作

GPU 端用 Python `BaseHTTPRequestHandler` 推 SSE, wfile.write() 是裸字节不自动 chunked。
HTTP/1.1 + keep-alive + 无 Content-Length + **无 Transfer-Encoding: chunked** 头部 →
hyper / reqwest 不知道 body 何时结束 → `bytes_stream().next()` **永远 Pending** →
客户端 0 个 SSE 事件 (包括心跳), 即使 GPU 服务端已推 connected + heartbeat + doc-update 等。

**日志证据 (张胜东 02:12 测试)**:
- 客户端 0 个 `📝` / `📄` / `💓` 日志
- GPU 端 `[push_event] subs=0` — push_event 调用时**已经没有 subscriber**

### Bug B: stop_capture 立即关 SSE → GPU 后台 6 docs 推送不到客户端

GPU 端 6 子 session (req/tasks/arch/api/risk/demo) 是 **daemon thread fire-and-forget**,
funasr 转写 + ingest + 6 docs 生成通常 **30-90 秒**。客户端 stop_capture 后立刻 await sse_handle 1.5s,
SSE 断开 → GPU 后台完成的 6 docs **永远到不了前端**, UI 卡在 "等待生成"。

### Bug C: chat-status "Hermes 正在思考..." 被 chat-list 滚走

`.content { flex: 1; overflow-y: auto }` + `.chat-list` 有 max-height 但 `.chat-status`
在 chat-list **下方, 不 sticky**。chat 内容多了滚到底, chat-status **滚出视口, 用户看不见**。

### Bug D (语义错误): stop_capture ≠ 会议停止

旧 stop_capture 逻辑把"用户停止录音"等同于"关闭会议 + 关闭 SSE", 但用户**还想
等 6 docs 生成完**。"会议停止"应该是显式行为 (以后 UI 加"结束会议"按钮), 不是
隐式行为 (录音停就关 SSE)。

---

## 1. 决策

### 1.1 GPU 端: 手动 chunked transfer encoding

`ui_server._handle_sse_events` 改写每帧为 `<hex_len>\r\n<data>\r\n` 格式, 终止帧 `0\r\n\r\n`,
响应头显式声明 `Transfer-Encoding: chunked`。这样 hyper/reqwest 能正确切分 chunk,
`bytes_stream()` 每次返回一帧, 客户端 SSE handler 能立刻解析。

### 1.2 新建 `ui_server_helpers.py::check_all_docs_stored_and_close(meeting_id)`

6 子 session 每个 doc 完成 KB 入库后调这个 helper。检查 6 doc md 文件**全部存在且非空**,
是则:
1. `push_event(meeting_id, "meeting-complete", {doc_sizes: {...}})` — 客户端 UI 显示"会议完成"
2. `close_meeting(meeting_id)` — SSE 自然退出, 客户端 stream.next() 返回 None

### 1.3 客户端 stop_capture 改语义

只设 `capturing=false` (audio 停) + POST stream_stop 通知服务端 audio 已停。
**SSE 不立即关**, 让 GPU 后台 6 docs 完成后通过 SSE 推过来。客户端 SSE 收到
`meeting-complete` 事件 → 按钮文字改成"✅ 会议完成 (开始新会议)" + 状态文字 "🎉 6 文档已全部生成"。

"会议真正停止"留给以后 UI 增加"结束会议"按钮 (张胜东决策)。

### 1.4 audio stall 5s timeout

`run_capture_loop` 主循环 `rx.recv()` 加 `tokio::time::timeout(5s)` 兜底。
如果 cpal/USB 麦克风驱动 hang, 5s 没新 chunk 就 warn + break, **不再 hang 整个 webview**。

### 1.5 chat-status sticky bottom

CSS: `.chat-status { position: sticky; bottom: 0; background: var(--bg2); ... z-index: 5 }`,
外加 `.panel.active { display: flex; flex-direction: column; height: 100% }` +
`.chat-list { flex: 1; min-height: 240px }` 让 sticky 真正生效在 panel 底部。

### 1.6 客户端 listen meeting-complete

`main.js` 加 `listen("meeting-complete", ...)` 处理; `handle_sse_event` Rust 加 meeting-complete 分支 emit。

---

## 2. 验证 (待用户装机实测)

GPU 端 e2e (server side):
```bash
PY=/home/zsd/miniconda3/envs/vpbuddy-gpu/bin/python3.11
$PY -c "
import urllib.request, json, socket, re, time
req = urllib.request.Request('http://localhost:8765/api/meetings/stream_start', ...)
r = json.loads(urllib.request.urlopen(req).read())
s = socket.create_connection(('localhost', 8765), timeout=5)
s.send(f'GET /api/meetings/{mid}/events HTTP/1.1\r\nHost: localhost\r\nAccept: text/event-stream\r\n\r\n'.encode())
# 应该收到 chunked 字节: 'fa\r\nevent: heartbeat\ndata: {...}\n\n\r\n'
"
```

客户端 (装机后):
- 录 30s+ → 转写页签每段语音实时显示 (彩色块 + 脉冲)
- 停止录音 → 按钮**不立即切** (SSE 还在等 GPU 6 docs)
- 1-2 分钟内 GPU 6 docs 完成 → 客户端**自动看到** 6 文档更新 + 按钮变"✅ 会议完成"
- SSE 自动断开, 转写页签继续工作 (但下一个 chunk 没新数据)

---

## 3. 不在范围

- "结束会议"按钮 UI (以后做)
- 客户端音频驱动 hang 的根本解决 (用户换麦克风 / 禁 USB 节能)
- SSE 自动 reconnect / Last-Event-ID 补发 (rc3+ 已经做了, 这次没动)

---

## 4. 经验教训 (写进 hermes memory)

- **Python BaseHTTP 不自动 chunked**: 写长连接 SSE 必须手动 hex 长度前缀
- **SSE 长连接 + reqwest 0.12**: 必须 Transfer-Encoding: chunked + 每帧 flush
- **fire-and-forget thread 完成检测**: 6 个 daemon thread 全完成后触发 close,
  用 doc 文件存在性检查最简单 (不需要 atomic counter)