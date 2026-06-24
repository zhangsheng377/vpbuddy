# VPBuddy 实时架构交接说明

分支：`feature/requirements-architecture-update`

本文档面向 VPBuddy 原作者，说明当前分支在实时链路、客户端-服务端架构、VP Chat、端到端测试方面做了什么、为什么这么做、怎么跑、哪些已经验证、哪些还需要在真实环境继续验证。

## 1. 这个分支的核心变化

这个分支把 VPBuddy 从“会议结束后生成文档”的模式，推进到“会议进行中实时展示 + VP 可随时干预”的模式。

核心变化有四条：

1. 客户端-服务端实时链路打通
2. 6 类文档和 Demo 可实时展示
3. 新增 VP Chat 窗口，直接接 Hermes 主控 agent
4. 新增无头客户端和端到端测试，可在无 GUI、无麦克风、无 GPU 的环境下验证协议链路

设计文档参考：

- `docs/decisions/0013-流式E2E-端到端工作流.md`
- `docs/design/总体架构.md`
- `README.md` 中“实时链路与 VP Chat”相关段落

## 2. 为什么这么设计

### 2.1 为什么必须是客户端-服务端架构

VPBuddy 不应该是纯本地工具。原因：

- ASR/说话人分离模型重，不适合塞进桌面客户端
- 6 类文档 + Demo 生成都需要 Hermes/LLM，本地很难稳定跑
- VP 只需要一个轻量桌面客户端，负责收音、展示、交互
- 服务端负责音频解析、结构化抽取、文档生成、Demo 生成、KB、SSE 推送

所以最终形态是：

```text
VPBuddy Desktop Client（Tauri）
        ↕ HTTP / SSE
VPBuddy Server（Python / Hermes / ASR）
```

### 2.2 为什么 VP Chat 直接接 Hermes

VP 在客户端里的自由输入不是普通聊天，而是对当前会议智能系统的指令。它天然需要：

- 当前会议上下文
- 当前 5 类事实
- 当前 6 类文档
- 历史会议记忆
- 工具调用
- skill 选择
- 子 agent 调度

这些都是 Hermes 已经在做的事情，VPBuddy 不应该再自己做一套。

因此 VP Chat 的定位是：

```text
VP Chat = 会议主控入口
Hermes = 智能运行时
6 个子 agent = 专业交付物维护者
VPBuddy Server = 音频/状态/SSE/API glue layer
```

固定 session 命名：

```text
meeting:{meeting_id}:vp-chat
meeting:{meeting_id}:req
meeting:{meeting_id}:arch
meeting:{meeting_id}:tasks
meeting:{meeting_id}:api
meeting:{meeting_id}:risk
meeting:{meeting_id}:demo
```

其中 `vp-chat` 是主控，负责回答、追问、调度 6 个子 agent。

## 3. 当前代码结构入口

### 3.1 服务端

主入口：

- `src/vpbuddy/ui_server.py`

核心模块：

- `src/vpbuddy/realtime_server.py`：SSE pub/sub、事件历史、多订阅者
- `src/vpbuddy/sub_session_controller.py`：6 子 session 触发、Hermes AIAgent 接入、KB 状态
- `src/vpbuddy/state.py`：MeetingState、5 类事实、去重
- `src/vpbuddy/storage.py`：会议状态持久化
- `src/vpbuddy/ingest.py`：音频转写结果入库、结构化抽取

新增 API：

```http
POST /api/meetings/stream_start
POST /api/meetings/{id}/stream_chunk
GET  /api/meetings/{id}/events          # SSE
GET  /api/meetings/{id}/state
GET  /api/meetings/{id}/docs
GET  /api/meetings/{id}/docs/{kind}
POST /api/meetings/{id}/chat
GET  /api/meetings/{id}/chat/history
```

### 3.2 客户端桌面端

前端：

- `vpbuddy-client/ui/index.html`
- `vpbuddy-client/ui/main.js`
- `vpbuddy-client/ui/style.css`

Tauri Rust 后端：

- `vpbuddy-client/src-tauri/src/main.rs`
- `vpbuddy-client/src-tauri/src/audio.rs`
- `vpbuddy-client/src-tauri/src/upload.rs`

客户端当前能力：

- 音频采集（cpal）
- 30s 切片 + 2s overlap
- 上传 chunk 元数据
- SSE 自动重连 + Last-Event-ID 历史补偿
- 实时转写展示
- 结构化事实面板
- 6 文档正文展示
- Demo iframe 预览
- VP Chat 窗口
- 连接状态 / 延迟指标
- 音频设备选择
- 基础中英文界面切换

## 4. 关键实现决策

### 4.1 SSE 用 pub/sub 而不是单队列

最开始是单 queue 模式，多个客户端会竞争消费同一个事件。现在改成每个订阅者独立队列，push_event 时 fan-out。

位置：`src/vpbuddy/realtime_server.py`

这支持了：

- 同一会议多个订阅者
- VP 客户端 + 助理端 + 后台监控端都能订阅同一会议
- 事件历史只维护一份，每个订阅者从自己的 offset 开始收

### 4.2 chunk 带元数据，而不是只传音频

客户端上传不再只是 wav 文件，还带：

- `chunk_index`
- `chunk_start_sec`
- `overlap_sec`
- `client_sent_at`

服务端用这些做：

- 重复 chunk 去重
- 绝对时间换算
- 延迟指标计算
- overlap 区域的片段去重

### 4.3 文档生成完成后主动推正文，而不是只推状态

文档生成完成后，SSE 会推：

```text
doc-update { kind, status, doc_size, content, is_demo }
```

客户端收到就能直接渲染，不用再额外拉一次。

### 4.4 VP Chat 走同一套 SSE 通道

VP Chat 不单独开 WebSocket，也复用 `/api/meetings/{id}/events`，新增事件：

```text
chat-message { role, content, source, status, error }
```

这样客户端只需要维护一条 SSE 连接。

## 5. 测试怎么跑

### 5.1 进程内实时链路测试

```bash
cd /workspace/vpbuddy
PYTHONPATH=src python src/tests/test_e2e_realtime_standalone.py
```

覆盖：

- SSE 端点
- push_event
- 多客户端订阅
- 会议隔离
- 心跳
- 历史补偿
- state/docs API
- stream_chunk 元数据和去重

共 9 个用例，全部通过。

### 5.2 无头客户端端到端测试

```bash
cd /workspace/vpbuddy
PYTHONPATH=src python src/tests/test_headless_client_standalone.py
```

模拟真实客户端行为：

- 创建会议
- 连 SSE
- 上传 chunk
- 收转写/事实/指标/文档/Demo
- 发 VP Chat
- 收 chat-message
- 查 chat history

全部通过。

### 5.3 两进程独立端到端测试

服务端进程：

```bash
cd /workspace/vpbuddy
PYTHONPATH=src python src/tests/headless_test_server.py --host 127.0.0.1 --port 18767
```

客户端进程：

```bash
cd /workspace/vpbuddy
PYTHONPATH=src python src/tests/headless_client.py \
  --server http://127.0.0.1:18767 \
  --chunks 1 \
  --chat "把 Demo 改成面向企业管理员的后台视角" \
  --json
```

这个最接近真实部署形态：一个进程跑服务端，另一个进程跑客户端。

## 6. 当前已验证到哪一步

当前测试全部通过，但要分清“真的验证了什么”和“还没验证什么”。

### 6.1 已经验证的

已经验证的是协议和实时链路：

- HTTP API 可用
- SSE 可用
- 多客户端 fan-out 可用
- 事件历史补偿可用
- chunk 元数据传递可用
- 重复 chunk 去重可用
- 状态和文档 API 可用
- VP Chat API 可用
- chat history 落盘可用
- chat-message SSE 推送可用
- 6 文档/Demo 回流可用
- 无头客户端可完整走通一轮

### 6.2 还没有验证的

下面这些在当前测试里是 fake/stub，还没有真实验证：

#### 真实 ASR

测试里 `gpu_transcribe.process` 是 fake 的，返回固定 segment。

还没验证：

- funasr 真实模型
- 说话人分离准确率
- 30s chunk 下的说话人跨 chunk 一致性
- 中文会议真实语料效果

#### 真实 Hermes / 真实 LLM

测试里 VP Chat 走的是 fake `run_agent.AIAgent`。

还没验证：

- Hermes AIAgent 真实运行
- `meeting:{mid}:vp-chat` 能否正确理解 VP 指令
- 能否正确调度 6 个子 agent
- skill 选择是否正确
- 工具调用是否稳定
- 上下文长度控制
- 多轮对话质量

#### 真实 6 子 agent 生成质量

测试里 `trigger_sub_session` 是 fake 的，直接写一份固定内容到文件。

还没验证：

- 6 类文档的真实生成质量
- 上下文累积是否正确
- Demo 真实可运行性
- KB 入库真实效果

#### Tauri 桌面客户端真实构建

当前 Tauri 代码已写，但在当前 Linux 环境里因为缺少 GTK/WebKit/GLib 系统依赖，`cargo check` 无法完整跑通。

还没验证：

- 桌面端完整构建
- 真实麦克风采集
- 真实 SSE 长连接稳定性
- 真实设备选择
- macOS / Windows 跨平台

## 7. 原作者接手后建议优先做的事

按优先级排。

### 7.1 在真实 Hermes 环境里跑通 VP Chat

这是当前分支最重要但还没在真实环境验证的能力。

建议步骤：

1. 在装有 Hermes 的环境里启动 `ui_server`
2. 用 `headless_client.py` 发一条真实 VP Chat
3. 确认 `meeting:{mid}:vp-chat` session 被正确创建
4. 确认 Hermes 能结合当前会议上下文回答
5. 确认它能调用工具 / skill
6. 确认它能调度 `meeting:{mid}:req`、`meeting:{mid}:demo` 等子 session

### 7.2 用真实音频跑 stream_chunk 全链路

建议步骤：

1. 连真实 funasr / GPU 转写
2. 用真实会议录音切片喂给 `headless_client.py`
3. 看 5 类事实抽取是否稳定
4. 看 6 文档生成是否可用
5. 看 Demo 生成是否可用
6. 看 SSE 延迟是否在可接受范围

### 7.3 修 Tauri 构建环境，真机跑客户端

当前代码结构在，但真实桌面体验还没完整验证。

建议：

1. 装 Tauri Linux 依赖，或直接在 macOS / Windows 上 build
2. 跑真实麦克风
3. 验证 SSE 断线重连
4. 验证 VP Chat 实际交互体验
5. 验证 Demo iframe 安全性

### 7.4 给 VP Chat 加结构化工具接口

当前 VP Chat 调度 6 子 agent 主要靠 prompt + Hermes 自身能力。

如果要更稳，可以加更结构化的工具，比如：

- `list_docs(meeting_id)`
- `read_doc(meeting_id, kind)`
- `update_doc(meeting_id, kind, instruction)`
- `list_facts(meeting_id)`
- `trigger_demo_update(meeting_id, instruction)`

这样主控 agent 的行为更可控。

## 8. 当前代码的已知边界

这些不是 bug，是当前阶段有意为之的边界，交接时需要说清楚。

### 8.1 SSE 历史是内存级的

服务端重启后，历史事件会丢。

如果要做真正的断线重放，需要把事件也落盘，或者让客户端从 state/docs/history API 重建状态。

### 8.2 事件 ID 是简单时间戳前缀

现在 event id 格式是：

```text
{timestamp_ms}-{event_type}
```

它适合 Last-Event-ID 粗略补偿，但不保证全局严格单调递增。如果以后要做多节点，需要换成更严谨的序号机制。

### 8.3 事实分类还是启发式规则

`ingest.py` 里的 5 类事实识别是规则启发式的。

它能跑通链路，但准确率和召回率需要真实语料校准。

### 8.4 VP Chat 还没有速率限制 / 鉴权

当前 `/chat` 没有鉴权、没有速率限制、没有输入长度限制。

如果要暴露到非本机环境，这些必须补。

### 8.5 文档生成触发策略比较粗

现在基本是每块 chunk 处理完就触发 6 文档。长时间会议可能触发太频繁。

后面可以做更智能的策略：

- 有新事实才触发
- 按文档类型分别触发
- 防抖 / 合并更新

## 9. 推荐阅读顺序

如果原作者想最快上手这个分支，建议按这个顺序看：

1. `README.md`：先看整体定位和当前能力清单
2. `docs/decisions/0013-流式E2E-端到端工作流.md`：看设计决策和约束
3. `docs/VPBuddy-实时架构交接说明.md`：也就是本文档，看交接全貌
4. `src/vpbuddy/ui_server.py`：看所有 API 入口
5. `src/vpbuddy/realtime_server.py`：看 SSE pub/sub 机制
6. `src/tests/headless_client.py`：看客户端应该怎么用这些 API
7. `src/tests/headless_test_server.py`：看测试服务端 fake 了哪些东西
8. `vpbuddy-client/ui/main.js`：看前端交互逻辑
9. `vpbuddy-client/src-tauri/src/main.rs`：看 Tauri 后端事件转发

## 10. 最后一句结论

这个分支已经把“实时会议 + VP Chat + Hermes 主控 + 6 子 agent”的骨架和接口都搭好了，端到端协议链路是通的，测试是稳的。

下一步的核心不是再加功能，而是把 fake 替换成真实模型和真实 Hermes 环境，验证效果、校准体验、打磨产品细节。
