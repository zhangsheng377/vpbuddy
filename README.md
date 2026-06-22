# VPBuddy

> **本地优先的会议操作系统级 AI 助手** —— 为 VP / 售前 / 项目负责人设计,运行在 VP 自己桌面客户端,数据完全本地化。

[English](#english) | [中文](#中文)

---

## 中文

### 什么是 VPBuddy?

VPBuddy 是一款**本地优先的会议 AI 系统**,直接捕获系统音频(腾讯会议/钉钉/企微 不需要 SDK),在会议过程中自动:

- 🎙️ **实时转写** — funasr paraformer-zh (本地 ASR,中文识别率 > 90%)
- 📋 **结构化累积** — REQ / GOAL / FEAT / RISK / QUE 5 类事实自动分类
- 📄 **6 种文档自动生成** — 需求 / 架构 / 任务 / API / 风险 / 演示
- 🧠 **跨会议知识库** — sqlite-vec + sentence-transformers,余弦相似度检索
- 🌐 **Web UI** — 端口 8765,实时看累积、检索历史会议

**关键特性**: 完全运行在 VP 自己机器,数据不上传云端。单租户,单实例,单镜像。

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
├─────────────────────────────────────────────────────┤
│  Audio loopback (PipeWire / WASAPI / BlackHole)     │
│        ↓                                            │
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
│        ↓                                            │
│  Web UI (FastAPI + Vanilla JS, port 8765)          │
└─────────────────────────────────────────────────────┘

可选: GPU 服务器 (cuda) 跑 ASR/embedding,加速
```

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

### 技术栈

- **Python 3.11+**,FastAPI / Pydantic v2
- **前端**: Vanilla JS (零依赖,符合 YAGNI)
- **ASR**: funasr paraformer-zh + fsmn-vad + cam++ (本地)
- **Embedding**: sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 (384 维)
- **向量存储**: sqlite-vec (单文件,零依赖)
- **LLM**: OpenAI 兼容 API(默认 MiniMax-M3,可换 GPT-4o / Claude / Qwen 等)
- **音频**: PipeWire / PulseAudio / WASAPI / BlackHole (跨平台)

### 状态(2026-06-22)

- ✅ MVP 全链路 work: 音频 → ASR → 6 文档 → KB → UI 检索
- ✅ 116 个单元测试 + 集成测试通过(GPU)
- ✅ E2E 集成测试 `RUN_E2E=1 pytest` 真跑完整链路
- 🚧 强模型 swap 提升工具调用成功率(当前 MiniMax-M3 8B 弱,需 fallback 兜底)

### License

MIT

---

## English

### What is VPBuddy?

VPBuddy is a **local-first meeting AI system** that captures system audio directly (no SDK needed for Tencent Meeting / DingTalk / WeCom). During meetings, it automatically:

- 🎙️ **Real-time transcription** — funasr paraformer-zh (>90% Chinese accuracy)
- 📋 **Structured accumulation** — 5 fact categories: REQ / GOAL / FEAT / RISK / QUE
- 📄 **6 document types auto-generated** — requirements / architecture / tasks / API / risks / demo
- 🧠 **Cross-meeting KB** — sqlite-vec + sentence-transformers with cosine similarity
- 🌐 **Web UI** — port 8765, real-time accumulation + history search

**Key feature**: Runs entirely on VP's own machine. Data never leaves the device. Single-tenant, single-instance, single-binary.

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
