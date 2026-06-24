# VPBuddy

> **会议操作系统级 AI 助手** —— 为 VP / 售前 / 项目负责人设计。桌面客户端采集会议音频，服务端实时转写、结构化累积、生成文档与 Demo，结果回传客户端实时展示。

[English](#english) | [中文](#中文)

---

## 中文

### 什么是 VPBuddy?

VPBuddy 是一款**会议 AI 系统**，桌面客户端直接捕获系统音频（腾讯会议/钉钉/企微 不需要 SDK），上传到服务端后在会议过程中实时：

- 🎙️ **实时转写** — funasr paraformer-zh（服务端 ASR，中文识别率 > 90%）
- 📋 **结构化累积** — REQ / GOAL / FEAT / RISK / QUE 5 类事实自动分类
- 📄 **6 种文档实时生成** — 需求 / 架构 / 任务 / API / 风险 / 演示，会议中持续更新
- 🧠 **跨会议知识库** — sqlite-vec + sentence-transformers，余弦相似度检索
- 🌐 **客户端实时展示** — 结构化事实、文档、Demo 在客户端对应窗口实时刷新

**关键特性**：客户端装在 VP 本地，负责采集和展示；重计算（ASR、LLM、向量检索）在服务端运行；支持私有化部署，数据可控。

### 5 分钟上手

```bash
# 1. 安装(开发机/桌面客户端)
git clone https://github.com/zhangsheng377/vpbuddy.git
cd vpbuddy
pip install -e .

# 2. 启动 UI
vpbuddy ui
# → 浏览器打开 http://localhost:8765

# 3. 启动后台 controller(7×24 监听)
vpbuddy controller --start
```

**第一次启动会自动**:
- 预下载 256MB embedding 模型(`paraphrase-multilingual-MiniLM-L12-v2`)
- 创建本地数据库(`data/knowledge.db`)
- 初始化 6 种子 session prompts

### 架构

```
┌─────────────────────────────────────────────────────┐
│  VP 桌面客户端 (Ubuntu 24.04 / macOS 14+ / Win11)  │
│  - 系统音频采集 (PipeWire / WASAPI / BlackHole)     │
│  - 实时展示: 结构化事实 / 6 类文档 / Demo            │
│  - VP steer / 采纳 / 修改 / 外发                     │
└──────────────┬──────────────────────────────────────┘
               │ 音频流上传
               ▼
┌─────────────────────────────────────────────────────┐
│  服务端 (GPU 可选)                                   │
├─────────────────────────────────────────────────────┤
│  ASR (funasr paraformer-zh)                        │
│        ↓                                            │
│  MeetingState (5 类事实累积)                       │
│        ↓                                            │
│  6 × sub_session (in-process AIAgent)              │
│  ┌────┬────┬────┬────┬────┬────┐                  │
│  │req │arch│tasks│api │risk│demo│                  │
│  └────┴────┴────┴────┴────┴────┘                  │
│        ↓                                            │
│  Knowledge Base (sqlite-vec + sentence-transformers)│
└──────────────┬──────────────────────────────────────┘
               │ 文档文本 + Demo 实时回传
               ▼
        客户端对应窗口实时展示
```

部署形态：服务端可部署在 GPU 服务器加速 ASR/embedding；客户端在 VP 本地采集和展示。

### 文档

| 主题 | 链接 |
|------|------|
| **架构** | [docs/design/总体架构.md](docs/design/总体架构.md) |
| **产品需求** | [docs/product-spec/](docs/product-spec/) |
| **决策记录** | [docs/decisions/](docs/decisions/) (ADR-0004 / 0005 / 0009) |
| **安装指南** | [docs/部署/INSTALL.md](docs/部署/INSTALL.md) |
| **模型 swap** | [docs/部署/MODEL_SWAP.md](docs/部署/MODEL_SWAP.md) |
| **踩坑记录** | [docs/部署/踩坑记录.md](docs/部署/踩坑记录.md) |
| **用户手册** | [docs/用户手册.md](docs/用户手册.md) |
| **🔒 安全 (ADR-0010)** | [INSTALL.md §🔒](./docs/部署/INSTALL.md#-安全信息隔离-adr-0010) / [ADR-0010](./docs/decisions/0010-信息隔离-deployment-clean-install.md) |
| **🇨🇳 HF 离线 (ADR-0011)** | [INSTALL.md §🇨🇳](./docs/部署/INSTALL.md#hf-模型离线铁律-adr-0011) / [ADR-0011](./docs/decisions/0011-HF模型离线铁律.md) |

### 🔒 安全:信息隔离 (ADR-0010)

> 张胜东在 2026-06-22 发现 GPU 服务器的 `~/.hermes/.env` 含本机真实 API key,立即清理 + 修补 install 脚本。

**铁律先复习**(ADR-0009 + ADR-0001):
- **VPBuddy 必须运行在 Hermes Agent 之上**(不自研 LLM 框架)
- 一次会议 = 一个 Hermes session,6 doc_kind = 6 子 session
- 真并发 = `ThreadPoolExecutor(3)` + in-process `from run_agent import AIAgent`
- LLM API key 由 `~/.hermes/.env` 通过 env var 注入,VPBuddy 不自己调 LLM HTTP

**信息隔离三条铁律**(ADR-0010):
1. `config.yaml` / `.env` 都用占位符,真实 key 由用户手动 `vim` 填
2. install 脚本从不包含真实 API key(可以安全推到 GitHub)
3. install 脚本不覆盖用户已存在的 `~/.hermes/config.yaml` 或 `.env`

```bash
# 1. install 只创建占位符(GPU 服务器 install-gpu-server.sh 一步装 hermes-agent + vpbuddy)
bash scripts/install-gpu-server.sh
# 输出:MINIMAX_CN_API_KEY=*** 2. 用户手动填
vim ~/.hermes/.env
chmod 600 ~/.hermes/.env

# 3. 验证:hermes-agent + vpbuddy 真共享 session
conda activate vpbuddy-gpu
python3 -c "from run_agent import AIAgent; print('✅ VPBuddy ↔ Hermes 真连接')"
```

### 命令速查

| 命令 | 说明 |
|------|------|
| `vpbuddy ui` | 启动 Web UI(开会时主入口) |
| `vpbuddy controller --start` | 启动后台 7×24 controller |
| `vpbuddy transcribe audio.wav` | 单次音频转写 |
| `vpbuddy trigger <meeting_id> <doc_kind>` | 手动触发某种文档 |
| `vpbuddy kb-search <query>` | 命令行 KB 检索 |
| `vpbuddy kb-status` | 查看 KB 状态(失败重试等) |
| `vpbuddy setup-gpu` | 装 GPU 模型(开发用) |

### 实时链路测试

服务端实时链路有两种测试方式。

进程内回归测试:

```bash
PYTHONPATH=src python src/tests/test_e2e_realtime_standalone.py
```

该测试在同一 Python 进程里启动测试服务端线程,覆盖 SSE、多客户端订阅、会议隔离、历史补偿、状态 API、文档 API、chunk 元数据和去重。

两进程无头测试:

```bash
# 进程 1: 测试服务端
PYTHONPATH=src python src/tests/headless_test_server.py --host 127.0.0.1 --port 18767

# 进程 2: 无头客户端
PYTHONPATH=src python src/tests/headless_client.py --server http://127.0.0.1:18767 --chunks 1 --json
```

两进程测试通过时,说明 `stream_start`、SSE、`stream_chunk`、事实更新、指标更新、6 类文档和 Demo 回流协议已经跑通。`headless_test_server.py` 使用 fake ASR 和 fake 文档生成器,因此它验证的是协议和实时链路,不验证真实 funasr、真实 GPU、真实 Hermes/LLM 或 Tauri GUI。

要验证真实模型环境,启动真实 `vpbuddy ui` 或生产服务端,再用同一个 `headless_client.py` 连接。真实环境需要 funasr/Hermes/KB 路径可用。

### 当前代码约束

- VPBuddy 是客户端-服务端架构:客户端采集音频和展示结果,服务端处理 ASR、事实抽取、文档、Demo 和 KB。
- 客户端上传 30s WAV chunk,当前保留 2s overlap。服务端用 `chunk_index` 去重,用 `chunk_start_sec` 转成会议绝对时间。
- SSE 事件包括 `transcript-segment`、`state-update`、`metrics-update`、`doc-update`、`heartbeat`。事件历史存在服务端内存里,进程重启后不保留。
- 会议持久状态写入 `{DATA_DIR}/{meeting_id}.json`,stream 元数据写入 `{DATA_DIR}/{meeting_id}.stream.json`,文档写入 `{DOCS_DIR}/{meeting_id}/`。
- 6 类交付物固定为 `req`、`arch`、`tasks`、`api`、`risk`、`demo`;Demo 主文件是 `demo/demo.html`。
- AI 可以主动在 VPBuddy 客户端内展示候选结论、风险和文档状态,但不能主动外发、投屏或调用外部会议软件。
- 客户端提供 VP 自由输入窗口。服务端把输入接到 Hermes `meeting:{meeting_id}:vp-chat` 主控 session,由 Hermes 负责上下文、工具调用、skill 选择和调度 6 个子 agent。
- VP Chat 的历史写入 `{DATA_DIR}/{meeting_id}.chat.json`,并通过 SSE `chat-message` 回流客户端。
- Linux 构建 Tauri 客户端需要 GTK/WebKit/GLib 开发库。缺少 `glib-2.0.pc` 时,`cargo check` 会在 `glib-sys` 构建阶段失败。

### 技术栈

- **Python 3.11+**,FastAPI / Pydantic v2
- **前端**: Vanilla JS (零依赖,符合 YAGNI)
- **ASR**: funasr paraformer-zh + fsmn-vad + cam++ (本地)
- **Embedding**: sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 (384 维)
- **向量存储**: sqlite-vec (单文件,零依赖)
- **LLM**: OpenAI 兼容 API(默认 MiniMax-M3,可换 GPT-4o / Claude / Qwen 等)
- **音频**: PipeWire / PulseAudio / WASAPI / BlackHole (跨平台)

### 状态(2026-06-24)

- ✅ MVP 全链路 work: 音频 → ASR → 6 文档 → Demo → KB → 客户端实时展示
- ✅ 服务端实时链路独立测试通过
- ✅ 两进程无头端到端测试通过
- 🚧 强模型 swap 提升工具调用成功率(当前 MiniMax-M3 8B 弱,需 fallback 兜底)

### License

MIT

---

## English

### What is VPBuddy?

VPBuddy is a **meeting AI system**. A desktop client captures system audio directly (no SDK needed for Tencent Meeting / DingTalk / WeCom) and streams it to the server. During meetings, the system produces in real time:

- 🎙️ **Real-time transcription** — funasr paraformer-zh (>90% Chinese accuracy, server-side)
- 📋 **Structured accumulation** — 5 fact categories: REQ / GOAL / FEAT / RISK / QUE
- 📄 **6 document types generated live** — requirements / architecture / tasks / API / risks / demo, updated continuously during the meeting
- 🧠 **Cross-meeting KB** — sqlite-vec + sentence-transformers with cosine similarity
- 🌐 **Client-side live display** — structured facts, documents, and demo update in real time on the client

**Key characteristics**: The client runs locally on the VP's machine for capture and display; heavy computation (ASR, LLM, vector search) runs on the server. Supports private deployment with data control.

### 5-minute Quick Start

```bash
git clone https://github.com/zhangsheng377/vpbuddy.git
cd vpbuddy
pip install -e .
vpbuddy ui    # → http://localhost:8765
```

### Documentation

See `docs/` for:
- Architecture (`docs/design/总体架构.md`)
- Decisions (`docs/decisions/0004-*.md`, `0009-*.md`)
- Installation (`docs/部署/INSTALL.md`)
- Model swap guide (`docs/部署/MODEL_SWAP.md`)

### License

MIT
