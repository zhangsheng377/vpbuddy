# VPBuddy 服务端架构分析：微服务化 / 流式化 评估

> 日期: 2026-07-04
> 范围: GPU 服务端内部组件（Python 单进程架构）

---

## 1. 当前服务端架构全景

### 1.1 进程模型

```
┌─ 单个 Python 进程 ─────────────────────────────────────────────────┐
│                                                                    │
│  sub_session_controller 线程                                       │
│  ┌────────────────────────────────────┐                            │
│  │ main_loop(): 每 30s 跑一轮          │                            │
│  │  └─ ThreadPoolExecutor(3)          │                            │
│  │     ├─ _dispatch_kind(mid, batch)  │                            │
│  │     └─ _dispatch_kind(mid, demo)   │                            │
│  └────────────────────────────────────┘                            │
│                                                                    │
│  ui_server (http.server.HTTPServer - 同步, 非 async)                │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │ 每个 HTTP 请求 = 一个线程                                      │  │
│  │                                                              │  │
│  │  POST /stream_chunk?sync=false                                │  │
│  │  └─ _parse_multipart() ─ 手写                                │  │
│  │  └─ 起 daemon Thread ── _process_chunk_background()          │  │
│  │     └─ funasr.transcribe() ← 每次新建 AutoModel (28s!)       │  │
│  │     └─ ingest (分类 → append state)                          │  │
│  │     └─ ThreadPoolExecutor(6) → trigger 6 sub_sessions        │  │
│  │     └─ push_event() → SSE fan-out                           │  │
│  │                                                              │  │
│  │  GET  /events (SSE 长连接)                                    │  │
│  │  POST /chat                                                  │  │
│  │  GET  /api/meetings 等                                        │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                    │
│  SSE 订阅者管理 (线程安全的 Queue[] / pig)                          │
│  _AGENT_CACHE (dict[str, AIAgent])                                 │
└────────────────────────────────────────────────────────────────────┘
```

### 1.2 并发能力基线

| 维度 | 现状 | 评估 |
|------|------|------|
| HTTP 服务 | `http.server.HTTPServer` 同步 | 每个请求一个 OS 线程，CPython 进程内无并行 |
| ASR 处理 | daemon Thread | funasr GPU 推理期间 block 该线程 |
| Document agent | ThreadPoolExecutor(3) | LLM HTTP 调用期间 block 线程 |
| 六个子 session | 已合并为 2 个 batch agent | 比原来好，但仍阻塞 |
| SSE 推送 | threading.Queue fan-out | 轻量，不是瓶颈 |
| 全局锁 | GIL | CPU-bound 任务(ASR 推理)会被 GIL 限制 |
| 文件 IO | NFS / 本地磁盘 | 轻量，不是瓶颈 |

### 1.3 性能瓶颈排序

```
#1 ⚠️  funasr AutoModel 每次新建 ─── 28s/请求 (90%+ time)
#2 ⚠️  单进程 GIL + 同步 HTTP ─── 多会议并发时排队
#3 ⚠️  手写 multipart 解析 ─── 大文件可能 OOM / 边界情况崩溃
#4 ⚡  无连接池 ─── 每次 SSE 新建线程/queue
#5 ⚡  AIAgent 无超时监控 ─── daemon thread 泄漏
```

---

## 2. 三个正交方向

你提出了三个问题，它们**不是互斥的**，但解决思路不同：

```
问题A: "内部组件服务化"  →  把各模块拆成独立进程 + HTTP/gRPC 调用
问题B: "并发性能更高"    →  让更多任务同时跑 (解决 GIL / 阻塞)
问题C: "改成流式的"      →  从 30s batch → 连续流式 ASR
```

---

## 3. 方向分析

### 3.1 方向A: 微服务化（拆独立进程）

**方案构想**:
```
┌─────────┐   HTTP    ┌──────────┐   HTTP    ┌───────────┐
│ UI Server│ ───────→ │ ASR Service│ ───────→ │ Ingest    │
│ (FastAPI)│          │ (funasr)  │          │ Service   │
│          │ ←─────── │ 单例缓存   │          │           │
│ port 8765│  SSE     │ 模型       │          └───────────┘
└─────────┘           └──────────┘               │
     │  SSE                                       │
     │                                           ▼
     │                                    ┌───────────┐
     └──────────────────────────────────→ │ Agent     │
                                          │ Service   │
                                          │ (batch    │
                                          │  + demo)  │
                                          └───────────┘
```

**好处**:
- 故障隔离（ASR 挂了，UI 还能响应）
- 独立扩缩容（没那么必要——单租户场景）
- 语言独立的服务边界

**坏处**:
- **每次 IPC 增加 ~1ms 延迟**（ASR 场景 30s 不差这 1ms，但 agent 场景 LLM 响应加 1ms 无感）
- **引入新问题**：序列化开销、超时重试、服务发现、进程管理
- 需要 systemd/supervisor 管理 4 个进程
- **运维复杂度跳跃式上升**

**结论**: ❌ **不推荐。单租户单实例场景下得不偿失。**

---

### 3.2 方向B: 并发优化（不拆进程，改架构）

**方案构想**:
```python
# 当前:
http.server.HTTPServer → 每个请求一个线程 → GIL 串行化 CPU 任务

# 优化后:
uvicorn + FastAPI        → 异步事件循环
  ├─ ASR:                ProcessPoolExecutor(1)       ← 绕过 GIL
  ├─ 网络 I/O:           asyncio                      ← 事件驱动
  ├─ agent LLM 调用:     ThreadPoolExecutor(3)        ← IO-bound
  └─ SSE 推送:           asyncio.Queue                ← 原生异步
```

**关键改动**:
| 组件 | 现状 | 优化后 | 收益 |
|------|------|--------|------|
| HTTP 框架 | `http.server` | `uvicorn + FastAPI` | async 事件循环, 不再 1 请求=1 线程 |
| ASR 模型 | 每次新建 | **模块级单例 LRU 缓存** | 28s → < 1s |
| ASR 推理 | 同线程 | **ProcessPoolExecutor(1)** | 不阻塞事件循环 |
| multipart 解析 | 手写 | `python-multipart` | 去掉 1 个 bug 源 |
| Agent 触发 | daemon thread | `asyncio.create_task` | 可控取消 |
| SSE | threading.Queue | `asyncio.Queue` + `StreamingResponse` | 零线程开销 |

**好处**:
- **改动范围小**（不改组件边界、不改数据流、不改进程模型）
- **收益明确**（模型缓存 28s → < 1s，改造代价 ~2 天）
- 渐进式：可以只改模型缓存就得到 90% 收益，再改 FastAPI

**坏处**:
- 仍受单机资源限制
- `ProcessPoolExecutor` 有序列化开销

**结论**: ✅ **强烈推荐。最低成本获得最大收益。**

---

### 3.3 方向C: 流式 ASR（从 batch 30s → 流式）

**关键洞察**: 当前"客户端每 30s 上传一个 chunk，服务器 batch 处理"的模式不是问题之源。**问题在于每次处理都重载模型**。

| ASR 方案 | 延迟 | RTX 3090 实测 | 复杂度 |
|----------|------|---------------|--------|
| 当前 batch 30s + 模型缓存修复 | **~1s / 30s chunk** | ✅ 简单 | 低 |
| funasr 流式 | ~3s 首字 | ✅ funasr 支持 | 中 |
| SenseVoiceSmall 流式 | ~0.5s 首字 | ✅ 小模型 | 高 |
| Whisper streaming | ~5s | ❌ 不适合中文 | 高 |

**但真正的瓶颈不是 ASR batch vs streaming**：

```
用户感知的延迟链路:
┌──────────┐    ┌──────────┐    ┌──────────┐    ┌───────────┐
│ 用户说话  │ →  │ 客户端采集 │ →  │ 上传chunk │ →  │ 服务端ASR  │ → UI展示
│          │    │ 30s切片   │    │ ~0.2s    │    │ 模型缓存后  │
│ T+0s 开始│    │ T+30s上传│    │ T+30.2s  │    │ ~1s        │ T+31.2s
└──────────┘    └──────────┘    └──────────┘    └───────────┘
                                      ↑
                         真正的问题: 客户端要等 30s 才上传
```

**即使 ASR 是 0.5s，客户端也等 30s 才发出第一块音频。**

所以流式的真正收益在于 **缩短客户端积累 30s chunk 的等待**。方案:

1. **缩短 chunk size**: 30s → 5s（需要调 funasr VAD 参数）
2. **真流式 SenseVoiceSmall**: 客户端 websocket 持续推音频流，服务端实时出字

| 方案 | 首字延迟 | 改动量 | 推荐 |
|------|---------|--------|------|
| 30s chunk + 模型缓存 | ~31s | ~1 天 | ✅ 最务实 |
| 5s chunk + 模型缓存 | ~6s | ~2 天 |  ✅ 好折中 |
| 真流式 ASR | ~2s | ~2 周 | ❌ 先不做 |

**结论**: ✅ 建议 **先缓存模型 + 改 5s chunk** 即可获得 ~6s 首字延迟。流式 ASR 作为 v1.1 规划。

---

## 4. 综合建议

### 4.1 立即动手（1-2 天，>90% 收益）

| # | 改动 | 收益 | 工作量 |
|---|------|------|--------|
| 1 | **funasr AutoModel 做成进程级单例** | 28s → < 1s | ~30 行 |
| 2 | **替换手写 multipart 解析器** | 消除一个 bug 源 | ~20 行 |
| 3 | **修复 prompt 模板转义** | 消除偶发崩溃 | ~10 行 |
| 4 | **添加端点级别的 timeout** | 消除 daemon 线程泄漏 | ~20 行 |

→ 这是 **P1 问题#4: funasr 模型缓存** 的记录。

### 4.2 本周（3-5 天）

| # | 改动 | 收益 | 工作量 |
|---|------|------|--------|
| 5 | **切 FastAPI + uvicorn** | 异步事件循环，并发加倍 | ~2 天 |
| 6 | ASR 推理移入 ProcessPoolExecutor | 不阻塞主事件循环 | ~0.5 天 |
| 7 | SSE 切 asyncio.Queue | 零线程 SSE 管理 | ~0.5 天 |
| 8 | chunk 从 30s 缩短到 5-10s | 首字延迟 30s → 5-10s | ~0.5 天 |

### 4.3 本月（规划中）

| # | 改动 | 收益 | 工作量 |
|---|------|------|--------|
| 9 | 拆分 `ui_server.py`（6 个模块） | 维护性 | ~1 天 |
| 10 | 统一路径到 `config.py` | 部署可靠性 | ~0.5 天 |

### 4.4 不做（v1.x 以后再考虑）

| # | 改动 | 原因 |
|---|------|------|
| ❌ | 真流式 ASR (SenseVoiceSmall) | 模型缓存后 30s→1s，5s chunk 足够快 |
| ❌ | 微服务化拆独立进程 | 单租户场景，运维成本 > 收益 |
| ❌ | 换 gRPC | HTTP/JSON 已够用 |

---

## 5. 最终回答

> **要不要服务化？** → **不用。** 这不是并发/稳定的瓶颈。28s 模型加载才是。
>
> **并发性能怎么提高？** → **缓存模型 → 切异步框架 → 加进程池。** 三步收益递减，但都远大于微服务化的收益。
>
> **流式要不要上？** → **先缩短 chunk size。** 缓存模型后 5s chunk 的首字延迟≈6s 是可接受的。真流式等 v1.x。
>
> **稳定怎么提高？** → **手写 multipart 解析器替换 + FastAPI 标准错误处理。** 替代 84KB 单文件里的 3 个手写解析器。

### 一句话

> **把 84KB 的 ui_server.py 先搞清爽，把 funasr 模型缓存了，然后切 FastAPI。微服务化和流式 ASR 对当前阶段都是过度设计。**

---

## 6. 新增问题跟踪

### P1 - funasr AutoModel 未缓存（新发现，替代原 P1#5）

| 字段 | 值 |
|------|-----|
| **文件** | `src/vpbuddy/scripts/gpu_transcribe.py` → `transcribe()` |
| **严重性** | 🔴 P1（阻塞级） |
| **状态** | 待处理 |
| **描述** | 每次 `transcribe()` 调用都执行 `AutoModel(model=..., vad_model=..., punc_model=..., spk_model=...)`，重新加载全部 4 个模型进 GPU，耗时 ~28s。真实推理 < 0.5s/10s 音频 |
| **影响** | 客户端首字转写延迟 30s+ (28s 空耗在模型加载) |
| **建议** | 模块级单例缓存，或 `functools.lru_cache` 包装 `transcribe()` 内的 AutoModel 初始化 |
| **优先级** | **0（最优先）** |
